import csv
import hashlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import match_sources as sources


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def participant(
    uid="123456789",
    platform="pc",
    *,
    name="Owner Name",
    hero="Storm",
    rank="Diamond I",
    stats=None,
):
    return {
        "uid": uid,
        "platform": platform,
        "name": name,
        "hero": hero,
        "rank": rank,
        "stats": stats or {"kills": 17, "damage_dealt": 12345},
    }


def match(match_id="match-001", **changes):
    value = {
        "match_id": match_id,
        "started_at": "2026-08-29T18:30:00-04:00",
        "season": "season-4",
        "mode": "competitive",
        "map": "Tokyo 2099",
        "result": "win",
        "duration_seconds": 611,
        "participants_complete": True,
        "participants": [
            participant(),
            participant(
                "987654321",
                "xbox",
                name="Second Owner",
                hero="Loki",
                stats={"healing_done": 8000, "assists": 22},
            ),
            participant(
                "555555555",
                "playstation",
                name="Unrelated Bystander",
                hero="Groot",
                stats={"damage_taken": 9999},
            ),
        ],
    }
    value.update(changes)
    return value


def document(matches=None, **changes):
    value = {
        "schema": sources.SCHEMA_ID,
        "source": {"uid": "123456789", "platform": "pc"},
        "matches": [match()] if matches is None else matches,
    }
    value.update(changes)
    return value


def json_bytes(value=None):
    return json.dumps(document() if value is None else value, separators=(",", ":")).encode()


def opted_in_accounts():
    return [
        {
            "id": "acct-source",
            "rivals_uid": "123456789",
            "rivals_platform": "pc",
            "match_history_authorized": True,
        },
        {
            "id": "acct-inferred",
            "rivals_uid": "987654321",
            "rivals_platform": "xbox",
            "match_history_authorized": True,
        },
        {
            "id": "acct-not-opted-in",
            "rivals_uid": "555555555",
            "rivals_platform": "playstation",
            "match_history_authorized": False,
        },
    ]


CSV_FIELDS = [
    "schema",
    "source_uid",
    "source_platform",
    "match_id",
    "started_at",
    "season",
    "mode",
    "map",
    "result",
    "duration_seconds",
    "participants_complete",
    "participant_uid",
    "participant_platform",
    "participant_name",
    "hero",
    "rank",
    "stat_kills",
    "stat_assists",
    "stat_damage_dealt",
    "stat_healing_done",
]


def csv_bytes(rows=None):
    if rows is None:
        base = {
            "schema": sources.SCHEMA_ID,
            "source_uid": "123456789",
            "source_platform": "pc",
            "match_id": "match-001",
            "started_at": "2026-08-29T22:30:00Z",
            "season": "season-4",
            "mode": "competitive",
            "map": "Tokyo 2099",
            "result": "win",
            "duration_seconds": "611",
            "participants_complete": "true",
        }
        rows = [
            {
                **base,
                "participant_uid": "123456789",
                "participant_platform": "pc",
                "participant_name": "Owner Name",
                "hero": "Storm",
                "rank": "Diamond I",
                "stat_kills": "17",
                "stat_damage_dealt": "12345",
            },
            {
                **base,
                "participant_uid": "987654321",
                "participant_platform": "xbox",
                "participant_name": "Second Owner",
                "hero": "Loki",
                "stat_assists": "22",
                "stat_healing_done": "8000",
            },
        ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


class MatchImportTests(unittest.TestCase):
    def assert_error(self, code, fn, error_type=sources.MatchSourceError):
        with self.assertRaises(error_type) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)
        self.assertTrue(caught.exception.message)
        self.assertTrue(caught.exception.path)
        return caught.exception

    def test_json_normalizes_source_match_participants_and_stats(self):
        raw = json_bytes()
        parsed = sources.parse_json_bytes(raw, now=NOW)

        self.assertEqual(parsed.schema_id, sources.SCHEMA_ID)
        self.assertEqual(parsed.source_kind, "json")
        self.assertEqual(parsed.source, sources.SourceIdentity("pc", "123456789"))
        self.assertEqual(parsed.source_digest, hashlib.sha256(raw).hexdigest())
        self.assertEqual(parsed.input_match_count, 1)
        self.assertEqual(parsed.rejections, ())
        self.assertEqual(len(parsed.matches), 1)
        normalized = parsed.matches[0]
        self.assertTrue(normalized.participants_complete)
        self.assertEqual(normalized.started_at, "2026-08-29T22:30:00.000000Z")
        self.assertTrue(normalized.match_key.startswith("m1_"))
        self.assertEqual(normalized.participants[0].stats_dict()["kills"], 17)

    def test_source_digest_is_exact_bytes_and_match_key_is_semantic(self):
        compact = json_bytes()
        pretty = json.dumps(document(), indent=2).encode()
        first = sources.parse_json_bytes(compact, now=NOW)
        repeated = sources.parse_json_bytes(compact, now=NOW)
        reformatted = sources.parse_json_bytes(pretty, now=NOW)

        self.assertEqual(first.source_digest, repeated.source_digest)
        self.assertNotEqual(first.source_digest, reformatted.source_digest)
        self.assertEqual(first.matches[0].match_key, reformatted.matches[0].match_key)
        self.assertEqual(first.matches[0].source_record_digest, reformatted.matches[0].source_record_digest)

        reordered = document()
        reordered["matches"][0]["participants"].reverse()
        reordered_parse = sources.parse_json_bytes(json_bytes(reordered), now=NOW)
        self.assertEqual(
            first.matches[0].source_record_digest,
            reordered_parse.matches[0].source_record_digest,
        )

        self.assertIs(sources.require_source_digest(first, first.source_digest), first)
        error = self.assert_error(
            "source_digest_mismatch",
            lambda: sources.require_source_digest(first, "0" * 64),
            sources.MatchSourceConflictError,
        )
        self.assertEqual(error.http_status, 409)

    def test_csv_groups_one_participant_per_row(self):
        raw = csv_bytes()
        parsed = sources.parse_csv_bytes(raw, now=NOW)

        self.assertEqual(parsed.source_kind, "csv")
        self.assertEqual(parsed.source_digest, hashlib.sha256(raw).hexdigest())
        self.assertEqual(parsed.input_match_count, 1)
        self.assertEqual(len(parsed.matches), 1)
        self.assertEqual(len(parsed.matches[0].participants), 2)
        self.assertEqual(parsed.matches[0].participants[1].stats_dict()["assists"], 22)

    def test_manual_entry_is_direct_only_without_fake_completeness(self):
        manual = {
            "schema": sources.SCHEMA_ID,
            "source": {"uid": "123456789", "platform": "pc"},
            "match": {
                "match_id": "manual-1",
                "started_at": "2026-08-29T22:30:00Z",
                "mode": "competitive",
                "hero": "Storm",
                "rank": "Diamond I",
                "stats": {"kills": 12, "damage_dealt": 9000},
            },
        }
        parsed = sources.normalize_manual_entry(manual, now=NOW)
        preview = sources.preview_owned_matches(parsed, opted_in_accounts())

        self.assertEqual(parsed.source_kind, "manual")
        self.assertFalse(parsed.matches[0].participants_complete)
        self.assertEqual(len(parsed.matches[0].participants), 1)
        self.assertEqual(preview.direct_fact_count, 1)
        self.assertEqual(preview.inferred_fact_count, 0)
        self.assertEqual(preview.facts[0].hero, "Storm")
        self.assertEqual(preview.facts[0].kills, 12)

    def test_manual_entry_rejects_complete_lobby_claim(self):
        manual = {
            "schema": sources.SCHEMA_ID,
            "source": {"uid": "123456789", "platform": "pc"},
            "match": {
                "started_at": "2026-08-29T22:30:00Z",
                "participants_complete": True,
                "participants": [participant()],
            },
        }
        self.assert_error(
            "manual_direct_only",
            lambda: sources.normalize_manual_entry(manual, now=NOW),
            sources.MatchSourceValidationError,
        )

    def test_upload_record_nesting_and_string_limits(self):
        error = self.assert_error(
            "upload_too_large",
            lambda: sources.parse_json_bytes(b" " * (sources.MAX_UPLOAD_BYTES + 1), now=NOW),
            sources.MatchSourceLimitError,
        )
        self.assertEqual(error.http_status, 413)

        with mock.patch.object(sources, "MAX_RECORDS", 1):
            self.assert_error(
                "record_limit_exceeded",
                lambda: sources.parse_json_bytes(
                    json_bytes(document(matches=[match("one"), match("two")])), now=NOW
                ),
                sources.MatchSourceLimitError,
            )

        nested = "leaf"
        for _ in range(sources.MAX_JSON_DEPTH + 1):
            nested = {"x": nested}
        self.assert_error(
            "json_too_deep",
            lambda: sources.parse_json_bytes(json.dumps(nested).encode(), now=NOW),
            sources.MatchSourceLimitError,
        )

        too_long = match()
        too_long["participants"][0]["hero"] = "x" * (sources.MAX_STRING_LENGTH + 1)
        self.assert_error(
            "string_too_long",
            lambda: sources.parse_json_bytes(json_bytes(document(matches=[too_long])), now=NOW),
            sources.MatchSourceLimitError,
        )

    def test_schema_source_uid_and_source_platform_are_fatal(self):
        self.assert_error(
            "unsupported_schema",
            lambda: sources.parse_json_bytes(json_bytes(document(schema="other")), now=NOW),
        )
        self.assert_error(
            "invalid_uid",
            lambda: sources.parse_json_bytes(
                json_bytes(document(source={"uid": "abc", "platform": "pc"})), now=NOW
            ),
        )
        self.assert_error(
            "invalid_platform",
            lambda: sources.parse_json_bytes(
                json_bytes(document(source={"uid": "123456789", "platform": "PC"})), now=NOW
            ),
        )

    def test_match_date_uid_platform_stats_and_completeness_are_rejections(self):
        invalid = []
        early = match("early", started_at="2024-11-30T23:59:59Z")
        late = match("late", started_at=(NOW + timedelta(days=8)).isoformat())
        bad_uid = match("uid")
        bad_uid["participants"][1]["uid"] = "12abc"
        bad_platform = match("platform")
        bad_platform["participants"][1]["platform"] = "switch"
        bad_stat = match("stat")
        bad_stat["participants"][0]["stats"] = {"headshots": 5}
        fractional_stat = match("fractional-stat")
        fractional_stat["participants"][0]["stats"] = {"damage_dealt": 1.5}
        incomplete = match("incomplete", participants_complete=False)
        invalid.extend(
            [early, late, bad_uid, bad_platform, bad_stat, fractional_stat, incomplete]
        )

        parsed = sources.parse_json_bytes(json_bytes(document(matches=invalid)), now=NOW)
        self.assertEqual(parsed.matches, ())
        self.assertEqual(
            [rejection.code for rejection in parsed.rejections],
            [
                "timestamp_too_early",
                "timestamp_too_late",
                "invalid_uid",
                "invalid_platform",
                "unsupported_stat",
                "invalid_stat",
                "incomplete_participants",
            ],
        )

    def test_duplicate_json_keys_and_unknown_fields_are_stable_errors(self):
        raw = b'{"schema":"mrat.matches.v1","schema":"mrat.matches.v1"}'
        self.assert_error("duplicate_json_key", lambda: sources.parse_json_bytes(raw, now=NOW))
        value = document()
        value["unexpected"] = True
        self.assert_error(
            "unknown_field", lambda: sources.parse_json_bytes(json_bytes(value), now=NOW)
        )

    def test_partial_preview_has_rejection_reasons_and_counts(self):
        bad = match("bad", participants_complete=False)
        parsed = sources.parse_json_bytes(
            json_bytes(document(matches=[match("good"), bad])), now=NOW
        )
        preview = sources.preview_owned_matches(parsed, opted_in_accounts())

        self.assertEqual(preview.state, "partial_import")
        self.assertEqual(preview.input_match_count, 2)
        self.assertEqual(preview.accepted_match_count, 1)
        self.assertEqual(preview.rejected_match_count, 1)
        self.assertEqual(preview.rejections[0].code, "incomplete_participants")

    def test_preview_labels_exact_owned_matches_and_sheds_bystanders(self):
        parsed = sources.parse_json_bytes(json_bytes(), now=NOW)
        preview = sources.preview_owned_matches(parsed, opted_in_accounts())

        self.assertEqual(preview.state, "ready")
        self.assertEqual(preview.direct_fact_count, 1)
        self.assertEqual(preview.inferred_fact_count, 1)
        self.assertEqual(
            {fact.evidence_kind for fact in preview.facts},
            {"direct", "inferred_owned_account"},
        )
        inferred = next(f for f in preview.facts if f.evidence_kind == "inferred_owned_account")
        direct = next(f for f in preview.facts if f.evidence_kind == "direct")
        self.assertEqual(inferred.account_id, "acct-inferred")
        self.assertEqual(inferred.source_account_id, "acct-source")
        self.assertIsNone(direct.source_account_id)
        self.assertEqual(inferred.platform, "xbox")
        self.assertEqual(inferred.occurred_at, "2026-08-29T22:30:00.000000Z")
        self.assertEqual(inferred.result, "win")
        self.assertEqual(inferred.healing_done, 8000)

        persisted = json.dumps(preview.persistable_facts(), sort_keys=True)
        for forbidden in (
            "123456789",
            "987654321",
            "555555555",
            "Owner Name",
            "Second Owner",
            "Unrelated Bystander",
            "participants",
            "participant_name",
            "raw_payload",
        ):
            self.assertNotIn(forbidden, persisted)

    def test_inference_requires_exact_platform_and_uid(self):
        accounts = opted_in_accounts()
        accounts[1]["rivals_platform"] = "playstation"
        preview = sources.preview_owned_matches(
            sources.parse_json_bytes(json_bytes(), now=NOW), accounts
        )
        self.assertEqual(preview.direct_fact_count, 1)
        self.assertEqual(preview.inferred_fact_count, 0)

    def test_source_must_be_opted_in_with_explicit_platform(self):
        unauthorized = opted_in_accounts()
        unauthorized[0]["match_history_authorized"] = False
        error = self.assert_error(
            "source_not_authorized",
            lambda: sources.preview_owned_matches(
                sources.parse_json_bytes(json_bytes(), now=NOW), unauthorized
            ),
            sources.MatchSourceAuthorizationError,
        )
        self.assertEqual(error.http_status, 409)

        unknown_doc = document(source={"uid": "123456789", "platform": "unknown"})
        unknown_doc["matches"][0]["participants"][0]["platform"] = "unknown"
        self.assert_error(
            "source_platform_required",
            lambda: sources.preview_owned_matches(
                sources.parse_json_bytes(json_bytes(unknown_doc), now=NOW),
                [sources.OwnedAccount("acct-source", "unknown", "123456789")],
            ),
            sources.MatchSourceAuthorizationError,
        )

    def test_duplicate_matches_are_counted_and_conflicts_rejected(self):
        same = match("same")
        parsed = sources.parse_json_bytes(
            json_bytes(document(matches=[same, json.loads(json.dumps(same))])), now=NOW
        )
        preview = sources.preview_owned_matches(parsed, opted_in_accounts())
        self.assertEqual(preview.input_match_count, 2)
        self.assertEqual(preview.accepted_match_count, 1)
        self.assertEqual(preview.duplicate_match_count, 1)
        self.assertEqual(preview.direct_fact_count, 1)

        changed = match("same", result="loss")
        conflicting = sources.parse_json_bytes(
            json_bytes(document(matches=[same, changed])), now=NOW
        )
        self.assert_error(
            "conflicting_match",
            lambda: sources.preview_owned_matches(conflicting, opted_in_accounts()),
        )

    def test_csv_malformed_match_is_rejected_while_valid_group_survives(self):
        text = csv_bytes().decode()
        reader = list(csv.DictReader(io.StringIO(text)))
        valid = reader[0]
        bad = dict(reader[1])
        bad["match_id"] = "bad-match"
        bad["participants_complete"] = "false"
        bad["participant_uid"] = "123456789"
        bad["participant_platform"] = "pc"
        parsed = sources.parse_csv_bytes(csv_bytes([valid, bad]), now=NOW)
        preview = sources.preview_owned_matches(parsed, opted_in_accounts())

        self.assertEqual(preview.state, "partial_import")
        self.assertEqual(preview.input_match_count, 2)
        self.assertEqual(preview.accepted_match_count, 1)
        self.assertEqual(preview.rejections[0].code, "incomplete_participants")

    def test_dispatch_rejects_non_allowlisted_format(self):
        self.assert_error(
            "unsupported_format",
            lambda: sources.parse_source_bytes(json_bytes(), format_hint="zip", now=NOW),
        )

    def test_persistable_facts_round_trip_through_match_index(self):
        from match_index import ImportBatch, MatchFact, MatchIndex

        parsed = sources.parse_json_bytes(json_bytes(), now=NOW)
        preview = sources.preview_owned_matches(parsed, opted_in_accounts())
        records = [MatchFact(**fact.as_dict()) for fact in preview.facts]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            index = MatchIndex(root / "match-index.sqlite3", root / "backups")
            self.assertTrue(index.initialize().available)
            classification = index.classify_records(records)
            self.assertEqual(classification.new_count, 2)
            self.assertEqual(classification.conflict_count, 0)
            result = index.import_records(
                ImportBatch(
                    "json",
                    preview.source_digest,
                    sources.SCHEMA_ID,
                    "owner-only-v1",
                    "owner_attested",
                ),
                records,
                rejected_count=preview.rejected_match_count,
            )
            self.assertEqual(result.accepted_count, 2)


if __name__ == "__main__":
    unittest.main()
