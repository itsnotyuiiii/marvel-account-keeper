"""Explicit, pinned public inputs for a future standalone game reader.

No installed-game loaders, credential sources or network requests run here.
The caller must supply an independently reviewed SHA256 of the exact manifest
bytes AND the expected source provenance; copying either from an untrusted
manifest is not a trust decision. These pins provide content integrity, not a
publisher signature, permission to authenticate, or proof of current server
compatibility. Reviewed public-only build 3825464 and 3848703 artifacts are
bundled under runtime-inputs; their independent digests are recorded below. Callers
still supply the explicit artifact path, expected digest and provenance.

Each supported provenance records a separately reviewed build, not an assertion
that a later patch is compatible. A new build needs separate revalidation.
Returned inputs fit GameCareerClient's existing explicit provider callbacks;
credentials, normal login, renewal and HTTP hosting remain separate work.
"""
from dataclasses import dataclass, field
import base64
import binascii
import hashlib
import hmac
import json
from pathlib import Path
import re

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from game_career_probe import CareerMethods
from game_login_probe import CLIENT_VERSION, LoginError, LoginServer, parse_server_list
from game_transport_probe import (
    IMAGE_SHA256, PARAMETER_SHA256, PUBLIC_KEY_SHA256, PublicConfiguration,
)


SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 8192
GAME_ID = "x20naxx2gb"
# Established by game_config_probe.shipping_login_url; no alternate destinations.
LOGIN_LIST_URL = "https://x20na.update.easebar.com/server_pc_prod_serverB"
# Same archive pin as scripts/inspect-script-container.py. No inspector import
# or game-file read is needed to validate an explicitly supplied manifest.
ARCHIVE_SHA256 = "4dbe9d3246a05d4a402373cc11c03a938692db9bc16aaaf939ddff4e8682d22e"


class RuntimeManifestError(ValueError):
    """Fixed diagnostic codes only, never raw manifest or filesystem details."""


@dataclass(frozen=True)
class RuntimeProvenance:
    archive_sha256: str
    image_sha256: str
    client_version: str


SUPPORTED_PROVENANCE = RuntimeProvenance(ARCHIVE_SHA256, IMAGE_SHA256, CLIENT_VERSION)
# Reviewed against the hash-gated source loaders and exact selected public
# fields on September 11, 2026. Not learned from the manifest at runtime.
# LF is pinned by .gitattributes. This does not approve a later server patch.
REVIEWED_MANIFEST_SHA256 = "63d536706d9cb055770129ca413950861d2553cb6c977bdee4f113fa66ccbc28"
# September 12 review: new archive entries/native RVAs were inspected, not
# blindly repinned. Transport values and selected LOGIN/Career schemas match.
# Preserve the legacy constants and artifact for reproducible older evidence.
CURRENT_PROVENANCE = RuntimeProvenance(
    "07a3dcf20d539b9a613246047f5f91e4c7b44de62e96e24c47acea1cef5d1f9f",
    "f9b9afda45adbb8db8162c12906d79d82fe627afecab5c0c3bbf0b5edcea2612",
    "3848703",
)
CURRENT_MANIFEST_SHA256 = "0111accea3a87a8ed6f45190f41cf5e517b400944c6342bdf689afa34bbb1d81"


def reviewed_runtime_pin(version):
    """Select known public metadata only; no file/network lookup or fallback."""
    if type(version) is str:
        if version == SUPPORTED_PROVENANCE.client_version:
            return SUPPORTED_PROVENANCE, REVIEWED_MANIFEST_SHA256
        if version == CURRENT_PROVENANCE.client_version:
            return CURRENT_PROVENANCE, CURRENT_MANIFEST_SHA256
    raise RuntimeManifestError("runtime_provenance_unsupported")


@dataclass(frozen=True)
class PortableGameRuntime:
    configuration: PublicConfiguration = field(repr=False)
    career_methods: CareerMethods
    login_list_url: str
    provenance: RuntimeProvenance
    manifest_sha256: str

    @property
    def client_version(self):
        return self.provenance.client_version


def _validate_pin(expected_sha256, expected_provenance):
    if (type(expected_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None):
        raise RuntimeManifestError("runtime_manifest_pin_required")
    if (type(expected_provenance) is not RuntimeProvenance
            or expected_provenance not in (SUPPORTED_PROVENANCE, CURRENT_PROVENANCE)):
        raise RuntimeManifestError("runtime_provenance_unsupported")


def _object(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise RuntimeManifestError("runtime_manifest_shape")
    return value


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise RuntimeManifestError("runtime_manifest_duplicate_key")
        result[name] = value
    return result


def _reject_constant(_value):
    raise RuntimeManifestError("runtime_manifest_json")


def _base64(value, max_bytes):
    if type(value) is not str or not 0 < len(value) <= 4 * ((max_bytes + 2) // 3):
        raise RuntimeManifestError("runtime_public_material_shape")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise RuntimeManifestError("runtime_public_material_shape") from None
    if (not 0 < len(raw) <= max_bytes
            or base64.b64encode(raw).decode("ascii") != value):
        raise RuntimeManifestError("runtime_public_material_shape")
    return raw


def parse_runtime_manifest(raw, *, expected_sha256, expected_provenance):
    """Parse reviewed public bytes without reading files or contacting a host.

    Shape: schema_version, game_id, provenance (archive_sha256, image_sha256,
    client_version), transport (public_key_der_base64,
    handshake_parameter_base64), career_methods (request, reply), login_list_url.
    Unknown fields are errors, including credentials, alternate URLs and paths.
    """
    _validate_pin(expected_sha256, expected_provenance)
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_MANIFEST_BYTES:
        raise RuntimeManifestError("runtime_manifest_bound")
    digest = hashlib.sha256(raw).hexdigest()
    if not hmac.compare_digest(digest, expected_sha256):
        raise RuntimeManifestError("runtime_manifest_hash_mismatch")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        if type(exc) is RuntimeManifestError:
            raise
        raise RuntimeManifestError("runtime_manifest_json") from None
    root = _object(value, ("schema_version", "game_id", "provenance", "transport",
                           "career_methods", "login_list_url"))
    if type(root["schema_version"]) is not int or root["schema_version"] != SCHEMA_VERSION:
        raise RuntimeManifestError("runtime_manifest_version")
    if root["game_id"] != GAME_ID or type(root["game_id"]) is not str:
        raise RuntimeManifestError("runtime_manifest_game")
    source = _object(root["provenance"], ("archive_sha256", "image_sha256", "client_version"))
    if any(type(source[key]) is not str for key in source):
        raise RuntimeManifestError("runtime_provenance_mismatch")
    provenance = RuntimeProvenance(**source)
    if provenance != expected_provenance:
        raise RuntimeManifestError("runtime_provenance_mismatch")
    if type(root["login_list_url"]) is not str or root["login_list_url"] != LOGIN_LIST_URL:
        raise RuntimeManifestError("runtime_login_url_unsupported")

    material = _object(root["transport"], ("public_key_der_base64", "handshake_parameter_base64"))
    der = _base64(material["public_key_der_base64"], 256)
    parameter = _base64(material["handshake_parameter_base64"], 18)
    if (len(parameter) != 18
            or hashlib.sha256(parameter).hexdigest() != PARAMETER_SHA256
            or hashlib.sha256(der).hexdigest() != PUBLIC_KEY_SHA256):
        raise RuntimeManifestError("runtime_public_material_mismatch")
    try:
        public_key = serialization.load_der_public_key(der)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise RuntimeManifestError("runtime_public_key_shape") from None
    if (not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size != 1024
            or public_key.public_bytes(serialization.Encoding.DER,
                                       serialization.PublicFormat.SubjectPublicKeyInfo) != der):
        raise RuntimeManifestError("runtime_public_key_shape")
    methods = _object(root["career_methods"], ("request", "reply"))
    if (any(type(value) is not int or not 0 < value <= 0x7FFFFFFF for value in methods.values())
            or methods["request"] == methods["reply"]):
        raise RuntimeManifestError("runtime_career_methods_shape")
    return PortableGameRuntime(PublicConfiguration(public_key, parameter),
                               CareerMethods(**methods), LOGIN_LIST_URL, provenance, digest)


def load_runtime_manifest(path, *, expected_sha256, expected_provenance):
    """Read only this explicit file, bounded; there is no default or fallback."""
    _validate_pin(expected_sha256, expected_provenance)
    if not isinstance(path, (str, Path)) or (isinstance(path, str) and not path):
        raise RuntimeManifestError("runtime_manifest_path_required")
    try:
        with Path(path).open("rb") as source:
            raw = source.read(MAX_MANIFEST_BYTES + 1)
    except (OSError, ValueError, TypeError):
        raise RuntimeManifestError("runtime_manifest_file_unavailable") from None
    return parse_runtime_manifest(raw, expected_sha256=expected_sha256,
                                  expected_provenance=expected_provenance)


def project_login_server(raw) -> LoginServer:
    """Validate caller-supplied bytes using the existing production-list parser.

    Pure projection only: this does not fetch or authenticate the source. A
    future HTTPS fetcher must bind these bytes to the manifest's exact URL.
    """
    try:
        return parse_server_list(raw)
    except LoginError:
        raise RuntimeManifestError("runtime_server_list_invalid") from None
