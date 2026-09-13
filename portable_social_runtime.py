"""Reviewed public SocialMatrix inputs, loaded without an installed game."""
import base64
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path


MANIFEST_SHA256 = "c3d1a44164613862529a63f4598b01af969dcb2b881107ae12c5edbb0eae09e4"
ARCHIVE_SHA256 = "07a3dcf20d539b9a613246047f5f91e4c7b44de62e96e24c47acea1cef5d1f9f"
PARAMETER_SHA256 = "e7a322fd20ed8591c366213d4d4d9ca579a5c2d4e0ca11c8e7f63367c7aa5209"
CLIENT_VERSION = "3848703"
TOKEN_RPC_INDEX = 1538836248


class SocialRuntimeError(ValueError):
    """Fixed local configuration diagnostics, not arbitrary input text."""


@dataclass(frozen=True)
class SocialRuntime:
    client_version: str
    token_rpc_index: int
    parameter: bytes = field(repr=False)


def parse_social_runtime(raw):
    """Accept only the independently reviewed, exact-byte public artifact."""
    if type(raw) is not bytes or not 0 < len(raw) <= 4096:
        raise SocialRuntimeError("social_runtime_bound")
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise SocialRuntimeError("social_runtime_hash_mismatch")
    try:
        value = json.loads(raw.decode("ascii"))
        expected_source = {
            "archive_sha256": ARCHIVE_SHA256, "client_version": CLIENT_VERSION,
            "parameter_entry": 5512, "parameter_filename": "Scripts/share/const/const_social.py"}
        if (type(value) is not dict or set(value) != {"schema_version", "game_id", "provenance",
                "parameter_base64", "parameter_sha256", "token_rpc_index"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or value["game_id"] != "x20naxx2gb" or value["provenance"] != expected_source
                or value["parameter_sha256"] != PARAMETER_SHA256
                or type(value["token_rpc_index"]) is not int or value["token_rpc_index"] != TOKEN_RPC_INDEX):
            raise ValueError
        parameter = base64.b64decode(value["parameter_base64"], validate=True)
        if len(parameter) != 17 or hashlib.sha256(parameter).hexdigest() != PARAMETER_SHA256:
            raise ValueError
    except (ValueError, TypeError, UnicodeError, KeyError):
        raise SocialRuntimeError("social_runtime_shape") from None
    return SocialRuntime(CLIENT_VERSION, TOKEN_RPC_INDEX, parameter)


def load_social_runtime(path):
    """Read only the explicit public artifact; no fallback, network or game files."""
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(4097)
    except OSError:
        raise SocialRuntimeError("social_runtime_unavailable") from None
    return parse_social_runtime(raw)
