"""Prepare a public-only runtime candidate for the reviewed build 3825464.

All four source paths are explicit. Existing build-gated, read-only loaders
interpret public distribution files; recovered game code is never executed.
There are no credentials, network requests, installed-path defaults or file
outputs. Successful CLI output is the exact candidate bytes on stdout and its
digest on stderr. Review and independently pin those bytes before deployment:
the generated digest is NOT independent approval or live compatibility proof.
"""
import sys

# This tool does not create an artifact or import-cache files.
sys.dont_write_bytecode = True

import argparse
import base64
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.hazmat.primitives import serialization

import game_career_probe as career
import game_config_probe as config
import game_transport_probe as transport
import portable_game_runtime as runtime


# Reviewed WindowsGame.ini plaintext and version metadata documented in
# API_RESEARCH.md. These constants are never learned from the input files.
SHIPPING_CONFIG_SHA256 = "0609b5302a6cc11e5398a2d58eecd99bc0913d90bc8a46c5717d9ab4046080fc"
REVIEWED_VERSION = "1.1.3825464"
REVIEWED_CHANGELIST = 3825464
# The reviewed version.json is 108231 bytes because it also inventories public
# distribution files (NonUFSFiles). Read with a fixed bound; export only the
# separately checked version/changelist, never that inventory.
MAX_VERSION_BYTES = 256 * 1024


class RuntimeExportError(ValueError):
    """Fixed diagnostics only; never source paths, data or third-party errors."""


def _path(value):
    if not isinstance(value, (str, Path)) or not str(value) or "\0" in str(value):
        raise RuntimeExportError("runtime_export_explicit_path_required")
    return Path(value)


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise RuntimeExportError("runtime_export_version_unverified")
        result[name] = value
    return result


def _verify_version(path):
    try:
        with path.open("rb") as source:
            raw = source.read(MAX_VERSION_BYTES + 1)
        if not 0 < len(raw) <= MAX_VERSION_BYTES:
            raise ValueError
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        if (type(value) is not dict or value.get("version") != REVIEWED_VERSION
                or type(value.get("changelist")) is not int
                or value["changelist"] != REVIEWED_CHANGELIST
                or runtime.SUPPORTED_PROVENANCE.client_version != str(REVIEWED_CHANGELIST)):
            raise ValueError
    except Exception:
        raise RuntimeExportError("runtime_export_version_unverified") from None


def build_manifest(*, archive, image, config_pak, version_json):
    """Return deterministic candidate bytes, not a trusted deployment artifact.

    Source verification is build-specific. The parser recheck validates shape
    and existing material/provenance pins, not the trust of a self-made digest.
    No source paths, timestamps or unselected distribution fields are included.
    """
    archive, image, config_pak, version_json = map(
        _path, (archive, image, config_pak, version_json))
    _verify_version(version_json)
    # Optional parser libraries must not leak diagnostics into either stream.
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        try:
            public = transport.load_public_configuration(archive=archive, image=image)
            methods = career.load_career_methods(archive=archive)
        except Exception:
            raise RuntimeExportError("runtime_export_build_unverified") from None
        try:
            index = config.load_config_index(pak=config_pak)
            shipping = config.read_config(index, config.SHIPPING_CONFIG)
            if (hashlib.sha256(shipping).hexdigest() != SHIPPING_CONFIG_SHA256
                    or config.shipping_login_url(shipping) != runtime.LOGIN_LIST_URL):
                raise ValueError
        except Exception:
            raise RuntimeExportError("runtime_export_shipping_unverified") from None
        try:
            der = public.public_key.public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
            value = {
                "schema_version": runtime.SCHEMA_VERSION,
                "game_id": runtime.GAME_ID,
                "provenance": asdict(runtime.SUPPORTED_PROVENANCE),
                "transport": {
                    "public_key_der_base64": base64.b64encode(der).decode("ascii"),
                    "handshake_parameter_base64": base64.b64encode(public.parameter).decode("ascii"),
                },
                "career_methods": {"request": methods.request, "reply": methods.reply},
                "login_list_url": runtime.LOGIN_LIST_URL,
            }
            raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("ascii")
            runtime.parse_runtime_manifest(
                raw, expected_sha256=hashlib.sha256(raw).hexdigest(),
                expected_provenance=runtime.SUPPORTED_PROVENANCE)
        except Exception:
            raise RuntimeExportError("runtime_export_output_unverified") from None
    return raw


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # Argparse normally repeats unknown arguments, which may contain data.
        raise RuntimeExportError("runtime_export_arguments_invalid")


def main(argv=None, *, stdout=None, stderr=None):
    """stdout must be binary; it receives bytes only after all checks pass."""
    if stdout is None:
        stdout = sys.stdout.buffer
    if stderr is None:
        stderr = sys.stderr
    parser = _Parser(description=__doc__, allow_abbrev=False)
    for name in ("archive", "image", "config-pak", "version-json"):
        parser.add_argument("--" + name, required=True)
    try:
        args = parser.parse_args(argv)
        raw = build_manifest(archive=args.archive, image=args.image,
                             config_pak=args.config_pak, version_json=args.version_json)
    except RuntimeExportError as exc:
        stderr.write(json.dumps({"outcome": str(exc)}) + "\n")
        return 1
    stderr.write(json.dumps({
        "outcome": "review_required_public_runtime_candidate",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "client_version": str(REVIEWED_CHANGELIST),
        "live_compatibility_verified": False,
    }, sort_keys=True) + "\n")
    stdout.write(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
