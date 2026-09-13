"""Game-independent wiring for the pinned public inputs and normal UID bundle."""
from pathlib import Path

import requests

import portable_game_runtime as game
from portable_social_runtime import load_social_runtime
from standalone_profile_bundle import run_profile_bundle
from web_sdk_auth_probe import EXPECTED_OWNER_UID, build_web_login_inputs


class NativeRuntimeError(ValueError):
    def __init__(self, code="native_runtime_unavailable"):
        self.code = code if code in {"native_runtime_unavailable", "native_inputs_invalid", "native_server_list_unavailable"} else "native_runtime_unavailable"
        super().__init__(self.code)


class NativeProfileQuery:
    """One owner-bound query; no session lookup, persistence, retry or fallback.

    All file reads are the two packaged, hash-pinned public manifests. Each
    lookup fetches the official public server list rather than pinning a stale
    private gateway address. Stage deadlines are not an end-to-end SLA.
    """
    def __init__(self, *, root=None, http_factory=requests.Session, query=run_profile_bundle):
        root = Path(root) if root is not None else Path(__file__).resolve().parent
        try:
            provenance, digest = game.reviewed_runtime_pin("3848703")
            self._public = game.load_runtime_manifest(root / "runtime-inputs" / "3848703.json",
                expected_sha256=digest, expected_provenance=provenance)
            self._social = load_social_runtime(root / "runtime-inputs" / "3848703-social.json")
            if not callable(http_factory) or not callable(query):
                raise ValueError()
            self._http_factory, self._query = http_factory, query
        except Exception:
            raise NativeRuntimeError("native_inputs_invalid") from None

    def __repr__(self):
        return "<NativeProfileQuery build=3848703>"

    def __call__(self, sauth, uid):
        # Imported here to keep public-input checks independent of persistence.
        from standalone_session_manager import QueryResult
        try:
            if type(uid) is not int or not 0 < uid < 2**63:
                raise ValueError()
            build_web_login_inputs(sauth, client_version="3848703", os_name="pc")
        except Exception:
            raise NativeRuntimeError("native_inputs_invalid") from None
        try:
            with self._http_factory() as http:
                http.trust_env = False
                with http.get(self._public.login_list_url, timeout=10, allow_redirects=False, stream=True) as response:
                    if response.status_code != 200 or response.url != game.LOGIN_LIST_URL:
                        raise ValueError()
                    raw = response.raw.read(4097, decode_content=True)
            server = game.project_login_server(raw)
        except Exception:
            raise NativeRuntimeError("native_server_list_unavailable") from None
        try:
            profile = self._query(sauth, self._public.configuration, server,
                parameter=self._social.parameter, career_methods=self._public.career_methods,
                target_uid=uid, client_version="3848703", platform="pc")
            if type(profile) is not dict:
                raise ValueError()
            owner_verified = profile.get("owner_authentication_verified") is True
            code = profile.get("auth_rejection_code")
            if (owner_verified or profile.get("owner_authentication_verified") is not False
                    or profile.get("game_login_verified") is not False
                    or type(code) is not int or not -(2**31) <= code < 2**31 or code == 0):
                code = None
            return QueryResult(EXPECTED_OWNER_UID, owner_verified, profile, auth_rejection_code=code)
        except Exception:
            raise NativeRuntimeError() from None
