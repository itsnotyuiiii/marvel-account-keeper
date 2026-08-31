"""Strict, local-only match-history source normalization.

This module deliberately does not perform any I/O beyond parsing caller-provided
bytes.  Parsed participant identities exist only on :class:`NormalizedImport`;
``preview_owned_matches`` projects them into persistable facts containing only
vault account IDs and owner-specific match data.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_ID = "mrat.matches.v1"
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 20_000
MAX_JSON_DEPTH = 12
MAX_STRING_LENGTH = 512
EARLIEST_TIMESTAMP = datetime(2024, 12, 1, tzinfo=timezone.utc)
MAX_FUTURE_SKEW = timedelta(days=7)
PLATFORMS = frozenset({"unknown", "pc", "playstation", "xbox"})
STAT_FIELDS = frozenset(
    {
        "kills",
        "deaths",
        "assists",
        "damage_dealt",
        "damage_taken",
        "healing_done",
    }
)

_UID_RE = re.compile(r"^[0-9]{6,11}$")
_EVIDENCE_KINDS = frozenset({"direct", "inferred_owned_account"})
_ROOT_KEYS = frozenset({"schema", "source", "matches"})
_MANUAL_ROOT_KEYS = frozenset({"schema", "source", "match"})
_SOURCE_KEYS = frozenset({"uid", "platform"})
_MATCH_KEYS = frozenset(
    {
        "match_id",
        "started_at",
        "season",
        "mode",
        "map",
        "result",
        "duration_seconds",
        "participants_complete",
        "participants",
    }
)
_PARTICIPANT_KEYS = frozenset(
    {"uid", "platform", "name", "hero", "rank", "stats"}
)
_MANUAL_MATCH_KEYS = _MATCH_KEYS | frozenset({"hero", "rank", "stats"})
_CSV_REQUIRED = frozenset(
    {
        "schema",
        "source_uid",
        "source_platform",
        "match_id",
        "started_at",
        "participants_complete",
        "participant_uid",
        "participant_platform",
    }
)
_CSV_OPTIONAL = frozenset(
    {
        "season",
        "mode",
        "map",
        "result",
        "duration_seconds",
        "participant_name",
        "hero",
        "rank",
    }
    | {f"stat_{name}" for name in STAT_FIELDS}
)


class MatchSourceError(ValueError):
    """Base error with a stable machine code and suggested HTTP status."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: str = "$",
        http_status: int = 422,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path
        self.http_status = http_status

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "path": self.path}


class MatchSourceValidationError(MatchSourceError):
    """The source is well-sized but does not satisfy ``mrat.matches.v1``."""


class MatchSourceLimitError(MatchSourceError):
    """A source exceeds a bounded resource limit (suggested HTTP 413)."""

    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        super().__init__(code, message, path=path, http_status=413)


class MatchSourceAuthorizationError(MatchSourceError):
    """The source identity is not an opted-in owned account (HTTP 409)."""

    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        super().__init__(code, message, path=path, http_status=409)


class MatchSourceConflictError(MatchSourceError):
    """Preview and commit inputs do not describe the same source (HTTP 409)."""

    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        super().__init__(code, message, path=path, http_status=409)


class _DuplicateJsonKey(ValueError):
    def __init__(self, key: str) -> None:
        self.key = key


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    platform: str
    uid: str


@dataclass(frozen=True, slots=True)
class NormalizedParticipant:
    platform: str
    uid: str
    name: str | None
    hero: str | None
    rank: str | None
    stats: tuple[tuple[str, int], ...]

    def stats_dict(self) -> dict[str, int]:
        return dict(self.stats)


@dataclass(frozen=True, slots=True)
class NormalizedMatch:
    match_key: str
    source_record_digest: str
    external_match_id: str | None
    started_at: str
    season: str | None
    mode: str | None
    map_name: str | None
    result: str | None
    duration_seconds: int | None
    participants_complete: bool
    participants: tuple[NormalizedParticipant, ...]


@dataclass(frozen=True, slots=True)
class MatchRejection:
    index: int
    code: str
    message: str
    path: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "code": self.code,
            "message": self.message,
            "path": self.path,
        }


@dataclass(frozen=True, slots=True)
class NormalizedImport:
    schema_id: str
    source_kind: str
    source_digest: str
    source: SourceIdentity
    input_match_count: int
    matches: tuple[NormalizedMatch, ...]
    rejections: tuple[MatchRejection, ...]


@dataclass(frozen=True, slots=True)
class OwnedAccount:
    account_id: str
    platform: str
    uid: str


@dataclass(frozen=True, slots=True)
class MatchFact:
    """A database-safe match fact containing no participant identity."""

    account_id: str
    match_key: str
    occurred_at: str
    platform: str
    evidence_kind: str
    source_record_digest: str
    season: str | None = None
    mode: str | None = None
    map_name: str | None = None
    result: str | None = None
    duration_seconds: int | None = None
    hero: str | None = None
    kills: int | None = None
    deaths: int | None = None
    assists: int | None = None
    damage_dealt: int | None = None
    damage_taken: int | None = None
    healing_done: int | None = None
    rank_at_match: str | None = None
    source_account_id: str | None = None

    def __post_init__(self) -> None:
        if self.evidence_kind not in _EVIDENCE_KINDS:
            raise ValueError("invalid evidence kind")

    def as_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "source_account_id": self.source_account_id,
            "match_key": self.match_key,
            "source_record_digest": self.source_record_digest,
            "occurred_at": self.occurred_at,
            "platform": self.platform,
            "evidence_kind": self.evidence_kind,
            "season": self.season,
            "mode": self.mode,
            "map_name": self.map_name,
            "result": self.result,
            "duration_seconds": self.duration_seconds,
            "hero": self.hero,
            "kills": self.kills,
            "deaths": self.deaths,
            "assists": self.assists,
            "damage_dealt": self.damage_dealt,
            "damage_taken": self.damage_taken,
            "healing_done": self.healing_done,
            "rank_at_match": self.rank_at_match,
        }


@dataclass(frozen=True, slots=True)
class ImportPreview:
    state: str
    source_digest: str
    source: SourceIdentity
    input_match_count: int
    accepted_match_count: int
    duplicate_match_count: int
    rejected_match_count: int
    direct_fact_count: int
    inferred_fact_count: int
    started_at_min: str | None
    started_at_max: str | None
    rejections: tuple[MatchRejection, ...]
    facts: tuple[MatchFact, ...]

    def persistable_facts(self) -> tuple[dict[str, Any], ...]:
        """Return rows safe to hand to persistence; no raw participant data."""

        return tuple(fact.as_dict() for fact in self.facts)


def _validation(code: str, message: str, path: str = "$") -> None:
    raise MatchSourceValidationError(code, message, path=path)


def _check_upload(data: bytes | bytearray | memoryview) -> bytes:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        _validation("invalid_source_type", "Source data must be bytes.")
    raw = bytes(data)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise MatchSourceLimitError(
            "upload_too_large",
            f"Source exceeds the {MAX_UPLOAD_BYTES}-byte upload limit.",
        )
    if not raw:
        _validation("empty_source", "Source data is empty.")
    return raw


def _decode_utf8(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise MatchSourceValidationError(
            "invalid_encoding", "Source must be UTF-8 encoded.", path="$"
        ) from exc


def _no_duplicate_object_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise _DuplicateJsonKey(key)
        obj[key] = value
    return obj


def _walk_json_limits(value: Any, *, path: str = "$", depth: int = 0) -> None:
    if isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            raise MatchSourceLimitError(
                "string_too_long",
                f"String exceeds the {MAX_STRING_LENGTH}-character limit.",
                path=path,
            )
        return
    if isinstance(value, Mapping):
        next_depth = depth + 1
        if next_depth > MAX_JSON_DEPTH:
            raise MatchSourceLimitError(
                "json_too_deep",
                f"JSON nesting exceeds the depth limit of {MAX_JSON_DEPTH}.",
                path=path,
            )
        for key, child in value.items():
            _walk_json_limits(key, path=f"{path}.<key>", depth=next_depth)
            _walk_json_limits(child, path=f"{path}.{key}", depth=next_depth)
        return
    if isinstance(value, list):
        next_depth = depth + 1
        if next_depth > MAX_JSON_DEPTH:
            raise MatchSourceLimitError(
                "json_too_deep",
                f"JSON nesting exceeds the depth limit of {MAX_JSON_DEPTH}.",
                path=path,
            )
        for index, child in enumerate(value):
            _walk_json_limits(child, path=f"{path}[{index}]", depth=next_depth)


def _reject_unknown_keys(
    value: Mapping[str, Any], allowed: frozenset[str], path: str
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        _validation(
            "unknown_field", f"Unsupported field: {unknown[0]}", f"{path}.{unknown[0]}"
        )


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _validation("invalid_type", "Expected an object.", path)
    return value


def _require_uid(value: Any, path: str) -> str:
    if isinstance(value, bool):
        _validation("invalid_uid", "UID must contain 6 to 11 digits.", path)
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str) or _UID_RE.fullmatch(value) is None:
        _validation("invalid_uid", "UID must contain 6 to 11 digits.", path)
    return value


def _require_platform(value: Any, path: str) -> str:
    if not isinstance(value, str) or value not in PLATFORMS:
        _validation(
            "invalid_platform",
            "Platform must be one of: unknown, pc, playstation, xbox.",
            path,
        )
    return value


def _optional_text(
    value: Any, path: str, *, maximum: int = MAX_STRING_LENGTH
) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        _validation("invalid_type", "Expected a string.", path)
    if len(value) > maximum:
        raise MatchSourceLimitError(
            "string_too_long",
            f"String exceeds the {maximum}-character field limit.",
            path=path,
        )
    value = value.strip()
    return value or None


def _duration(value: Any, path: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        _validation("invalid_duration", "Duration must be a non-negative integer.", path)
    if isinstance(value, str):
        if not value.isdigit():
            _validation(
                "invalid_duration", "Duration must be a non-negative integer.", path
            )
        value = int(value)
    if not isinstance(value, int) or value < 0 or value > 86_400:
        _validation(
            "invalid_duration",
            "Duration must be an integer between 0 and 86400 seconds.",
            path,
        )
    return value


_STAT_MAXIMUMS = {
    "kills": 1_000_000,
    "deaths": 1_000_000,
    "assists": 1_000_000,
    "damage_dealt": 2_000_000_000,
    "damage_taken": 2_000_000_000,
    "healing_done": 2_000_000_000,
}


def _stat_integer(value: Any, name: str, path: str) -> int:
    if isinstance(value, bool):
        _validation("invalid_stat", "Statistic must be a non-negative integer.", path)
    if isinstance(value, str):
        if not value.isdigit():
            _validation("invalid_stat", "Statistic must be a non-negative integer.", path)
        value = int(value)
    if not isinstance(value, int) or value < 0 or value > _STAT_MAXIMUMS[name]:
        _validation(
            "invalid_stat",
            f"Statistic must be an integer from 0 to {_STAT_MAXIMUMS[name]}.",
            path,
        )
    return value


def _stats(value: Any, path: str) -> tuple[tuple[str, int], ...]:
    if value is None:
        return ()
    obj = _require_mapping(value, path)
    unknown = sorted(set(obj) - STAT_FIELDS)
    if unknown:
        _validation(
            "unsupported_stat",
            f"Unsupported statistic: {unknown[0]}",
            f"{path}.{unknown[0]}",
        )
    return tuple(
        (name, _stat_integer(obj[name], name, f"{path}.{name}"))
        for name in sorted(obj)
    )


def _timestamp(value: Any, *, now: datetime, path: str) -> str:
    if not isinstance(value, str):
        _validation("invalid_timestamp", "Timestamp must be an ISO-8601 string.", path)
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise MatchSourceValidationError(
            "invalid_timestamp", "Timestamp must be valid ISO-8601.", path=path
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _validation("invalid_timestamp", "Timestamp must include a timezone.", path)
    parsed = parsed.astimezone(timezone.utc)
    if parsed < EARLIEST_TIMESTAMP:
        _validation(
            "timestamp_too_early",
            "Timestamp cannot be earlier than 2024-12-01T00:00:00Z.",
            path,
        )
    if parsed > now + MAX_FUTURE_SKEW:
        _validation(
            "timestamp_too_late",
            "Timestamp cannot be more than seven days in the future.",
            path,
        )
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _utc_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    return now.astimezone(timezone.utc)


def _source_identity(value: Any, path: str = "$.source") -> SourceIdentity:
    obj = _require_mapping(value, path)
    _reject_unknown_keys(obj, _SOURCE_KEYS, path)
    if "uid" not in obj or "platform" not in obj:
        _validation("missing_field", "Source requires uid and platform.", path)
    return SourceIdentity(
        platform=_require_platform(obj["platform"], f"{path}.platform"),
        uid=_require_uid(obj["uid"], f"{path}.uid"),
    )


def _participant(value: Any, path: str) -> NormalizedParticipant:
    obj = _require_mapping(value, path)
    _reject_unknown_keys(obj, _PARTICIPANT_KEYS, path)
    if "uid" not in obj or "platform" not in obj:
        _validation("missing_field", "Participant requires uid and platform.", path)
    return NormalizedParticipant(
        platform=_require_platform(obj["platform"], f"{path}.platform"),
        uid=_require_uid(obj["uid"], f"{path}.uid"),
        name=_optional_text(obj.get("name"), f"{path}.name"),
        hero=_optional_text(obj.get("hero"), f"{path}.hero", maximum=120),
        rank=_optional_text(obj.get("rank"), f"{path}.rank", maximum=100),
        stats=_stats(obj.get("stats"), f"{path}.stats"),
    )


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _participant_canonical(p: NormalizedParticipant) -> dict[str, Any]:
    return {
        "platform": p.platform,
        "uid": p.uid,
        "name": p.name,
        "hero": p.hero,
        "rank": p.rank,
        "stats": dict(p.stats),
    }


def _stable_match_key(
    *,
    external_match_id: str | None,
    started_at: str,
    season: str | None,
    mode: str | None,
    map_name: str | None,
    duration_seconds: int | None,
    participants: Sequence[NormalizedParticipant],
) -> str:
    if external_match_id:
        identity: dict[str, Any] = {
            "schema": SCHEMA_ID,
            "external_match_id": external_match_id,
        }
    else:
        identity = {
            "schema": SCHEMA_ID,
            "started_at": started_at,
            "season": season,
            "mode": mode,
            "map": map_name,
            "duration_seconds": duration_seconds,
            "participants": sorted((p.platform, p.uid) for p in participants),
        }
    return "m1_" + _sha256(_canonical_json(identity))


def _normalize_match(
    value: Any,
    *,
    source: SourceIdentity,
    now: datetime,
    path: str,
    allow_incomplete_direct: bool = False,
) -> NormalizedMatch:
    obj = _require_mapping(value, path)
    _reject_unknown_keys(obj, _MATCH_KEYS, path)
    required = {"started_at", "participants"}
    if not allow_incomplete_direct:
        required.add("participants_complete")
    missing = sorted(required - set(obj))
    if missing:
        _validation("missing_field", f"Match requires {missing[0]}.", f"{path}.{missing[0]}")
    participants_complete = obj.get("participants_complete", False)
    if not isinstance(participants_complete, bool):
        _validation(
            "invalid_type",
            "participants_complete must be a boolean.",
            f"{path}.participants_complete",
        )
    if not participants_complete and not allow_incomplete_direct:
        _validation(
            "incomplete_participants",
            "A complete participant list must be explicitly confirmed.",
            f"{path}.participants_complete",
        )
    raw_participants = obj["participants"]
    if not isinstance(raw_participants, list) or not raw_participants:
        _validation(
            "incomplete_participants",
            "Match participants must be a non-empty array.",
            f"{path}.participants",
        )
    participants = tuple(
        _participant(item, f"{path}.participants[{index}]")
        for index, item in enumerate(raw_participants)
    )
    identities = [(p.platform, p.uid) for p in participants]
    if len(identities) != len(set(identities)):
        _validation(
            "duplicate_participant",
            "A participant identity may appear only once per match.",
            f"{path}.participants",
        )
    if (source.platform, source.uid) not in set(identities):
        _validation(
            "source_participant_missing",
            "The source account must appear in every complete participant list.",
            f"{path}.participants",
        )
    if allow_incomplete_direct and (
        participants_complete
        or len(participants) != 1
        or identities[0] != (source.platform, source.uid)
    ):
        _validation(
            "manual_direct_only",
            "Manual entry must contain only the source participant and cannot claim a complete lobby.",
            f"{path}.participants",
        )

    external_id = _optional_text(obj.get("match_id"), f"{path}.match_id")
    started_at = _timestamp(obj["started_at"], now=now, path=f"{path}.started_at")
    season = _optional_text(obj.get("season"), f"{path}.season", maximum=80)
    mode = _optional_text(obj.get("mode"), f"{path}.mode", maximum=100)
    map_name = _optional_text(obj.get("map"), f"{path}.map", maximum=120)
    result = _optional_text(obj.get("result"), f"{path}.result", maximum=80)
    duration_seconds = _duration(
        obj.get("duration_seconds"), f"{path}.duration_seconds"
    )
    match_key = _stable_match_key(
        external_match_id=external_id,
        started_at=started_at,
        season=season,
        mode=mode,
        map_name=map_name,
        duration_seconds=duration_seconds,
        participants=participants,
    )
    record = {
        "match_key": match_key,
        "external_match_id": external_id,
        "started_at": started_at,
        "season": season,
        "mode": mode,
        "map": map_name,
        "result": result,
        "duration_seconds": duration_seconds,
        "participants": [
            _participant_canonical(p)
            for p in sorted(participants, key=lambda p: (p.platform, p.uid))
        ],
    }
    return NormalizedMatch(
        match_key=match_key,
        source_record_digest=_sha256(_canonical_json(record)),
        external_match_id=external_id,
        started_at=started_at,
        season=season,
        mode=mode,
        map_name=map_name,
        result=result,
        duration_seconds=duration_seconds,
        participants_complete=participants_complete,
        participants=participants,
    )


def _normalize_document(
    document: Any,
    *,
    source_kind: str,
    source_digest: str,
    now: datetime,
    initial_rejections: Sequence[MatchRejection] = (),
    input_match_count: int | None = None,
    allow_incomplete_direct: bool = False,
) -> NormalizedImport:
    root = _require_mapping(document, "$")
    _reject_unknown_keys(root, _ROOT_KEYS, "$")
    if root.get("schema") != SCHEMA_ID:
        _validation("unsupported_schema", f"Schema must be {SCHEMA_ID}.", "$.schema")
    if "source" not in root or "matches" not in root:
        _validation("missing_field", "Source requires source and matches.", "$")
    source = _source_identity(root["source"])
    raw_matches = root["matches"]
    if not isinstance(raw_matches, list):
        _validation("invalid_type", "Matches must be an array.", "$.matches")
    if len(raw_matches) > MAX_RECORDS:
        raise MatchSourceLimitError(
            "record_limit_exceeded",
            f"Source exceeds the {MAX_RECORDS}-match limit.",
            path="$.matches",
        )
    if not raw_matches and not initial_rejections:
        _validation("empty_matches", "Source must contain at least one match.", "$.matches")
    matches: list[NormalizedMatch] = []
    rejections = list(initial_rejections)
    for index, item in enumerate(raw_matches):
        try:
            matches.append(
                _normalize_match(
                    item,
                    source=source,
                    now=now,
                    path=f"$.matches[{index}]",
                    allow_incomplete_direct=allow_incomplete_direct,
                )
            )
        except MatchSourceValidationError as exc:
            rejections.append(
                MatchRejection(
                    index=index,
                    code=exc.code,
                    message=exc.message,
                    path=exc.path,
                )
            )
    return NormalizedImport(
        schema_id=SCHEMA_ID,
        source_kind=source_kind,
        source_digest=source_digest,
        source=source,
        input_match_count=(
            len(raw_matches) + len(initial_rejections)
            if input_match_count is None
            else input_match_count
        ),
        matches=tuple(matches),
        rejections=tuple(rejections),
    )


def parse_json_bytes(data: bytes, *, now: datetime | None = None) -> NormalizedImport:
    """Parse a strict ``mrat.matches.v1`` JSON upload without persistence."""

    raw = _check_upload(data)
    text = _decode_utf8(raw)
    try:
        document = json.loads(text, object_pairs_hook=_no_duplicate_object_pairs)
    except _DuplicateJsonKey as exc:
        raise MatchSourceValidationError(
            "duplicate_json_key", f"Duplicate JSON key: {exc.key}", path="$"
        ) from exc
    except (json.JSONDecodeError, RecursionError) as exc:
        code = "json_too_deep" if isinstance(exc, RecursionError) else "invalid_json"
        error_type = MatchSourceLimitError if code == "json_too_deep" else MatchSourceValidationError
        raise error_type(code, "Source is not valid bounded JSON.", path="$") from exc
    _walk_json_limits(document)
    return _normalize_document(
        document,
        source_kind="json",
        source_digest=_sha256(raw),
        now=_utc_now(now),
    )


def _csv_value(row: Mapping[str, str | None], name: str) -> str:
    value = row.get(name)
    return "" if value is None else value


def parse_csv_bytes(data: bytes, *, now: datetime | None = None) -> NormalizedImport:
    """Parse v1 CSV, grouping its one-participant-per-row representation."""

    raw = _check_upload(data)
    text = _decode_utf8(raw)
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        headers = reader.fieldnames
        if headers is None:
            _validation("missing_csv_header", "CSV requires a header row.", "$header")
        if len(headers) != len(set(headers)):
            _validation("duplicate_csv_header", "CSV headers must be unique.", "$header")
        for index, header in enumerate(headers):
            if len(header) > MAX_STRING_LENGTH:
                raise MatchSourceLimitError(
                    "string_too_long",
                    f"Header exceeds the {MAX_STRING_LENGTH}-character limit.",
                    path=f"$header[{index}]",
                )
        missing = sorted(_CSV_REQUIRED - set(headers))
        if missing:
            _validation(
                "missing_csv_column",
                f"CSV requires column: {missing[0]}",
                "$header",
            )
        unknown = sorted(set(headers) - _CSV_REQUIRED - _CSV_OPTIONAL)
        if unknown:
            _validation(
                "unknown_csv_column", f"Unsupported CSV column: {unknown[0]}", "$header"
            )
        rows: list[dict[str, str | None]] = []
        for row_number, row in enumerate(reader, start=2):
            if row.get(None):
                _validation(
                    "csv_column_mismatch",
                    "CSV row has more values than the header.",
                    f"$row[{row_number}]",
                )
            if len(rows) >= MAX_RECORDS:
                raise MatchSourceLimitError(
                    "record_limit_exceeded",
                    f"CSV exceeds the {MAX_RECORDS}-record limit.",
                    path=f"$row[{row_number}]",
                )
            for column, value in row.items():
                if column is not None and value is not None and len(value) > MAX_STRING_LENGTH:
                    raise MatchSourceLimitError(
                        "string_too_long",
                        f"CSV value exceeds the {MAX_STRING_LENGTH}-character limit.",
                        path=f"$row[{row_number}].{column}",
                    )
            rows.append(row)
    except csv.Error as exc:
        raise MatchSourceValidationError(
            "invalid_csv", "Source is not valid CSV.", path="$"
        ) from exc

    if not rows:
        _validation("empty_matches", "CSV must contain at least one participant row.", "$")

    first = rows[0]
    if _csv_value(first, "schema") != SCHEMA_ID:
        _validation("unsupported_schema", f"Schema must be {SCHEMA_ID}.", "$row[2].schema")
    source = SourceIdentity(
        platform=_require_platform(_csv_value(first, "source_platform"), "$row[2].source_platform"),
        uid=_require_uid(_csv_value(first, "source_uid"), "$row[2].source_uid"),
    )
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    csv_rejections: list[MatchRejection] = []
    invalid_groups: set[str] = set()
    match_fields = (
        "started_at",
        "season",
        "mode",
        "map",
        "result",
        "duration_seconds",
        "participants_complete",
    )
    for row_number, row in enumerate(rows, start=2):
        path = f"$row[{row_number}]"
        if _csv_value(row, "schema") != SCHEMA_ID:
            _validation("unsupported_schema", f"Schema must be {SCHEMA_ID}.", f"{path}.schema")
        row_source = SourceIdentity(
            platform=_require_platform(_csv_value(row, "source_platform"), f"{path}.source_platform"),
            uid=_require_uid(_csv_value(row, "source_uid"), f"{path}.source_uid"),
        )
        if row_source != source:
            _validation("mixed_source_identity", "All CSV rows must use one source identity.", path)
        match_id = _csv_value(row, "match_id")
        if not match_id:
            csv_rejections.append(
                MatchRejection(
                    index=len(order) + len(csv_rejections),
                    code="missing_match_id",
                    message="CSV match_id cannot be empty.",
                    path=f"{path}.match_id",
                )
            )
            continue
        signature = tuple(_csv_value(row, field) for field in match_fields)
        if match_id not in grouped:
            grouped[match_id] = {
                "signature": signature,
                "match": {
                    "match_id": match_id,
                    "started_at": _csv_value(row, "started_at"),
                    "season": _csv_value(row, "season"),
                    "mode": _csv_value(row, "mode"),
                    "map": _csv_value(row, "map"),
                    "result": _csv_value(row, "result"),
                    "duration_seconds": _csv_value(row, "duration_seconds"),
                    "participants_complete": (
                        _csv_value(row, "participants_complete") == "true"
                    ),
                    "participants": [],
                },
            }
            order.append(match_id)
        elif grouped[match_id]["signature"] != signature:
            if match_id not in invalid_groups:
                csv_rejections.append(
                    MatchRejection(
                        index=order.index(match_id),
                        code="inconsistent_match_rows",
                        message="Rows for one match_id must have identical match metadata.",
                        path=path,
                    )
                )
                invalid_groups.add(match_id)
            continue
        stat_values = {
            stat: _csv_value(row, f"stat_{stat}")
            for stat in STAT_FIELDS
            if _csv_value(row, f"stat_{stat}") != ""
        }
        grouped[match_id]["match"]["participants"].append(
            {
                "uid": _csv_value(row, "participant_uid"),
                "platform": _csv_value(row, "participant_platform"),
                "name": _csv_value(row, "participant_name"),
                "hero": _csv_value(row, "hero"),
                "rank": _csv_value(row, "rank"),
                "stats": stat_values,
            }
        )

    if len(grouped) > MAX_RECORDS:
        raise MatchSourceLimitError(
            "record_limit_exceeded",
            f"CSV exceeds the {MAX_RECORDS}-match limit.",
            path="$",
        )
    valid_order = [match_id for match_id in order if match_id not in invalid_groups]
    document = {
        "schema": SCHEMA_ID,
        "source": {"uid": source.uid, "platform": source.platform},
        "matches": [grouped[match_id]["match"] for match_id in valid_order],
    }
    return _normalize_document(
        document,
        source_kind="csv",
        source_digest=_sha256(raw),
        now=_utc_now(now),
        initial_rejections=csv_rejections,
        input_match_count=len(order) + sum(r.code == "missing_match_id" for r in csv_rejections),
    )


def parse_source_bytes(
    data: bytes, *, format_hint: str, now: datetime | None = None
) -> NormalizedImport:
    """Dispatch only to explicitly allowlisted local source formats."""

    if format_hint == "json":
        return parse_json_bytes(data, now=now)
    if format_hint == "csv":
        return parse_csv_bytes(data, now=now)
    _validation("unsupported_format", "Format must be json or csv.", "$.format")


def require_source_digest(
    normalized: NormalizedImport, expected_digest: str
) -> NormalizedImport:
    """Bind a reparsed commit input to the bytes approved during preview."""

    if not isinstance(expected_digest, str) or re.fullmatch(
        r"[0-9a-f]{64}", expected_digest
    ) is None:
        _validation(
            "invalid_expected_digest",
            "Expected source digest must be 64 lowercase hexadecimal characters.",
            "$.expected_digest",
        )
    if not hmac.compare_digest(normalized.source_digest, expected_digest):
        raise MatchSourceConflictError(
            "source_digest_mismatch",
            "The source changed after preview; preview the current source again.",
            path="$.expected_digest",
        )
    return normalized


def normalize_manual_entry(
    entry: Mapping[str, Any], *, now: datetime | None = None
) -> NormalizedImport:
    """Normalize one manual-entry mapping using the same strict match schema."""

    root = _require_mapping(entry, "$")
    _walk_json_limits(root)
    _reject_unknown_keys(root, _MANUAL_ROOT_KEYS, "$")
    if root.get("schema") != SCHEMA_ID:
        _validation("unsupported_schema", f"Schema must be {SCHEMA_ID}.", "$.schema")
    if "source" not in root or "match" not in root:
        _validation("missing_field", "Manual entry requires source and match.", "$")
    try:
        canonical = _canonical_json(root)
    except (TypeError, ValueError) as exc:
        raise MatchSourceValidationError(
            "invalid_manual_value",
            "Manual entry contains a value that cannot be normalized.",
            path="$",
        ) from exc
    if len(canonical) > MAX_UPLOAD_BYTES:
        raise MatchSourceLimitError(
            "upload_too_large",
            f"Manual entry exceeds the {MAX_UPLOAD_BYTES}-byte limit.",
        )
    source = _source_identity(root["source"])
    raw_match = _require_mapping(root["match"], "$.match")
    _reject_unknown_keys(raw_match, _MANUAL_MATCH_KEYS, "$.match")
    if raw_match.get("participants_complete") is True:
        _validation(
            "manual_direct_only",
            "Manual entry cannot attest to a complete participant lobby.",
            "$.match.participants_complete",
        )
    transformed = dict(raw_match)
    if "participants" not in transformed:
        participant = {
            "uid": source.uid,
            "platform": source.platform,
            "hero": transformed.pop("hero", None),
            "rank": transformed.pop("rank", None),
            "stats": transformed.pop("stats", None),
        }
        transformed["participants"] = [participant]
    elif any(key in transformed for key in ("hero", "rank", "stats")):
        _validation(
            "ambiguous_manual_participant",
            "Put manual hero, rank, and stats either on the match or its sole participant.",
            "$.match",
        )
    transformed.setdefault("participants_complete", False)
    document = {
        "schema": SCHEMA_ID,
        "source": root["source"],
        "matches": [transformed],
    }
    return _normalize_document(
        document,
        source_kind="manual",
        source_digest=_sha256(canonical),
        now=_utc_now(now),
        allow_incomplete_direct=True,
    )


def _owned_account(value: OwnedAccount | Mapping[str, Any], index: int) -> OwnedAccount | None:
    if isinstance(value, OwnedAccount):
        return value
    if not isinstance(value, Mapping):
        _validation("invalid_owned_account", "Owned account must be an object.", f"$owned[{index}]")
    if value.get("match_history_authorized") is not True:
        return None
    account_id = value.get("account_id", value.get("id"))
    uid = value.get("uid", value.get("rivals_uid"))
    platform = value.get("platform", value.get("rivals_platform"))
    if not isinstance(account_id, str) or not account_id or len(account_id) > MAX_STRING_LENGTH:
        _validation("invalid_account_id", "Owned account requires a valid account ID.", f"$owned[{index}].id")
    return OwnedAccount(
        account_id=account_id,
        platform=_require_platform(platform, f"$owned[{index}].platform"),
        uid=_require_uid(uid, f"$owned[{index}].uid"),
    )


def preview_owned_matches(
    normalized: NormalizedImport,
    owned_accounts: Iterable[OwnedAccount | Mapping[str, Any]],
) -> ImportPreview:
    """Infer facts for opted-in vault accounts and immediately shed bystanders.

    Mappings are considered opted in only when ``match_history_authorized`` is
    exactly ``True``.  Passing :class:`OwnedAccount` is an explicit equivalent.
    """

    identities: dict[tuple[str, str], OwnedAccount] = {}
    for index, candidate in enumerate(owned_accounts):
        account = _owned_account(candidate, index)
        if account is None:
            continue
        if account.platform == "unknown":
            continue
        key = (account.platform, account.uid)
        if key in identities:
            _validation(
                "duplicate_owned_identity",
                "Two opted-in accounts cannot share the same platform and UID.",
                f"$owned[{index}]",
            )
        identities[key] = account

    source_key = (normalized.source.platform, normalized.source.uid)
    if normalized.source.platform == "unknown":
        raise MatchSourceAuthorizationError(
            "source_platform_required",
            "The source account must have an explicit platform.",
            path="$.source.platform",
        )
    source_account = identities.get(source_key)
    if source_account is None:
        raise MatchSourceAuthorizationError(
            "source_not_authorized",
            "The source identity is not an opted-in owned account.",
            path="$.source",
        )

    seen: dict[str, str] = {}
    facts: list[MatchFact] = []
    duplicate_count = 0
    accepted_dates: list[str] = []
    for match in normalized.matches:
        prior_digest = seen.get(match.match_key)
        if prior_digest is not None:
            if prior_digest != match.source_record_digest:
                _validation(
                    "conflicting_match",
                    "Duplicate match identity has conflicting content.",
                    "$.matches",
                )
            duplicate_count += 1
            continue
        seen[match.match_key] = match.source_record_digest
        accepted_dates.append(match.started_at)
        for participant in match.participants:
            account = identities.get((participant.platform, participant.uid))
            if account is None:
                continue
            if not match.participants_complete and account.account_id != source_account.account_id:
                continue
            evidence = (
                "direct"
                if account.account_id == source_account.account_id
                else "inferred_owned_account"
            )
            facts.append(
                MatchFact(
                    account_id=account.account_id,
                    match_key=match.match_key,
                    source_record_digest=match.source_record_digest,
                    occurred_at=match.started_at,
                    platform=participant.platform,
                    evidence_kind=evidence,
                    season=match.season,
                    mode=match.mode,
                    map_name=match.map_name,
                    result=match.result,
                    duration_seconds=match.duration_seconds,
                    hero=participant.hero,
                    kills=participant.stats_dict().get("kills"),
                    deaths=participant.stats_dict().get("deaths"),
                    assists=participant.stats_dict().get("assists"),
                    damage_dealt=participant.stats_dict().get("damage_dealt"),
                    damage_taken=participant.stats_dict().get("damage_taken"),
                    healing_done=participant.stats_dict().get("healing_done"),
                    rank_at_match=participant.rank,
                    source_account_id=(
                        None
                        if evidence == "direct"
                        else source_account.account_id
                    ),
                )
            )

    direct_count = sum(fact.evidence_kind == "direct" for fact in facts)
    inferred_count = len(facts) - direct_count
    state = (
        "partial_import"
        if normalized.rejections and seen
        else "rejected"
        if normalized.rejections
        else "ready"
    )
    return ImportPreview(
        state=state,
        source_digest=normalized.source_digest,
        source=normalized.source,
        input_match_count=normalized.input_match_count,
        accepted_match_count=len(seen),
        duplicate_match_count=duplicate_count,
        rejected_match_count=len(normalized.rejections),
        direct_fact_count=direct_count,
        inferred_fact_count=inferred_count,
        started_at_min=min(accepted_dates) if accepted_dates else None,
        started_at_max=max(accepted_dates) if accepted_dates else None,
        rejections=normalized.rejections,
        facts=tuple(facts),
    )


__all__ = [
    "EARLIEST_TIMESTAMP",
    "ImportPreview",
    "MAX_FUTURE_SKEW",
    "MAX_JSON_DEPTH",
    "MAX_RECORDS",
    "MAX_STRING_LENGTH",
    "MAX_UPLOAD_BYTES",
    "MatchFact",
    "MatchRejection",
    "MatchSourceAuthorizationError",
    "MatchSourceConflictError",
    "MatchSourceError",
    "MatchSourceLimitError",
    "MatchSourceValidationError",
    "NormalizedImport",
    "NormalizedMatch",
    "NormalizedParticipant",
    "OwnedAccount",
    "PLATFORMS",
    "SCHEMA_ID",
    "STAT_FIELDS",
    "SourceIdentity",
    "normalize_manual_entry",
    "parse_csv_bytes",
    "parse_json_bytes",
    "parse_source_bytes",
    "preview_owned_matches",
    "require_source_digest",
]
