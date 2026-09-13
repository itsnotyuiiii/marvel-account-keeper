"""Synthetic JWTs only; no gateway, capture, local data or network access."""
import base64
from dataclasses import FrozenInstanceError, replace
import json
import pickle
import unittest
from unittest.mock import patch

import standalone_social_session as social


NOW = 1000


def segment(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def claims(**changes):
    return {"aid": "1_123", "iat": NOW - 10, "exp": NOW + 100,
            "tcp": {"addr": "8.8.8.8:12101", "rc4": True, "tls": False}, **changes}


def token(value=None, *, raw=None, header=None, signature=b"synthetic-not-verified"):
    body = raw if raw is not None else json.dumps(claims() if value is None else value).encode()
    head = json.dumps({"alg": "HS256"} if header is None else header).encode()
    return ".".join(map(segment, (head, body, signature)))


class StandaloneSocialSessionTest(unittest.TestCase):
    def parse(self, value=None, **kwargs):
        return social.parse_gateway_social_token(token() if value is None else value,
            own_uid=kwargs.pop("own_uid", 123), zone_id=kwargs.pop("zone_id", 1),
            now=kwargs.pop("now", NOW), **kwargs)

    def rejects(self, code, value, **kwargs):
        with self.assertRaises(social.SocialSessionError) as raised:
            self.parse(value, **kwargs)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), code)
        self.assertIn(code, social.ERROR_CODES)

    def test_exact_owner_zone_and_dynamic_public_route(self):
        for host in ("8.8.8.8", "1.1.1.1"):
            raw = token(claims(tcp={"addr": host + ":12102", "rc4": True}))
            session = self.parse(raw, own_uid="123", zone_id="1")
            self.assertEqual(session.token, raw)
            self.assertEqual((session.own_uid, session.zone_id, session.aid), (123, 1, "1_123"))
            self.assertEqual((session.route.host, session.route.port), (host, 12102))
            self.assertEqual((session.issued_at, session.expires_at, session.not_before), (990, 1100, None))

    def test_uid_or_zone_substrings_do_not_count_as_identity(self):
        for aid in ("1_1234", "11_123", "other_1_123", "1_123_extra", "1_0123", "01_123",
                    "fixture:123", "1_124", "2_123", 123, None, "1_123\n"):
            with self.subTest(aid=aid):
                self.rejects("social_token_identity", token(claims(aid=aid)))

    def test_context_ids_are_canonical_bounded_and_not_bools(self):
        for value in (True, False, 0, -1, "01", "+1", " 1", "١", 1.0, None, 2**64, "9" * 21):
            for field in ("own_uid", "zone_id"):
                with self.subTest(field=field, value=value):
                    self.rejects("social_session_context", token(), **{field: value})

    def test_time_claims_require_strict_integer_order_and_current_validity(self):
        for update in ({"iat": True}, {"exp": True}, {"iat": 990.0}, {"exp": "1100"},
                       {"iat": -1}, {"exp": 2**53}, {"iat": 1001}, {"exp": 1000},
                       {"iat": 1100, "exp": 1099}, {"nbf": None}, {"nbf": True},
                       {"nbf": 1001}, {"nbf": -1}, {"nbf": 1100}):
            with self.subTest(update=update):
                self.rejects("social_token_time", token(claims(**update)))
        for name in ("iat", "exp"):
            missing = claims()
            del missing[name]
            self.rejects("social_token_time", token(missing))
        session = self.parse(token(claims(iat=NOW, nbf=NOW)))
        self.assertEqual(session.not_before, NOW)

    def test_bad_clocks_are_not_silently_accepted(self):
        for now in (True, "1000", -1, float("inf"), float("nan"), 2**53):
            self.rejects("social_session_context", token(), now=now)
        with patch.object(social.time, "time", return_value=NOW) as clock:
            self.parse(now=None)
        clock.assert_called_once_with()

    def test_route_is_public_numeric_ipv4_only_and_has_canonical_port(self):
        for address in ("127.0.0.1:12101", "10.0.0.1:12101", "169.254.1.1:12101",
                        "0.0.0.0:12101", "100.64.0.1:12101", "192.0.2.1:12101",
                        "224.0.0.1:12101", "240.0.0.1:12101", "255.255.255.255:12101",
                        "host.netease.com:12101", "[2606:4700::1111]:12101",
                        "8.8.8.8:0", "8.8.8.8:65536", "8.8.8.8:012101", "8.8.8.8:+1",
                        "08.8.8.8:12101", "8.8.8.8", "8.8.8.8:12101/path",
                        "https://8.8.8.8:12101", "user@8.8.8.8:12101", "8.8.8.8:1:2",
                        "8.8.8.8:12101\n", "8.8.8.8:１２", None):
            with self.subTest(address=address):
                self.rejects("social_token_route", token(claims(tcp={"addr": address, "rc4": True})))
        for port in (1, 65535):
            self.assertEqual(self.parse(token(claims(tcp={"addr": f"8.8.8.8:{port}", "rc4": True}))).route.port, port)

    def test_transport_flags_do_not_coerce_truthy_values(self):
        for rc4 in (None, False, 1, "true"):
            self.rejects("social_token_transport", token(claims(tcp={"addr": "8.8.8.8:12101", "rc4": rc4})))
        for tls in (None, True, 0, "false"):
            self.rejects("social_token_transport", token(claims(tcp={"addr": "8.8.8.8:12101", "rc4": True, "tls": tls})))
        self.rejects("social_token_route", token(claims(tcp=[])))

    def test_jwt_format_bounds_and_canonical_base64url(self):
        for value in (b"bytes", "Bearer " + token(), " " + token(), token() + "\n", "a.b.c", ".a.b",
                      "a..b", "a.b.", "a.b.c.d", token().replace(".", "=.", 1)):
            with self.subTest(value=type(value).__name__):
                self.rejects("social_token_format", value)
        for value in ("", "a" * (social.MAX_TOKEN_BYTES + 1)):
            self.rejects("social_token_bound", value)
        # A nonzero unused pad bit must not produce a second spelling.
        self.rejects("social_token_format", token(signature=b"x")[:-1] + "B")

    def test_duplicate_keys_and_nonfinite_json_are_rejected_at_any_depth(self):
        for raw in (b'{"aid":"1_123","aid":"2_123"}', b'{"x":{"a":1,"a":2}}',
                    b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e9999}', b'[]', b'null',
                    b'"string"', b'{', b'\xff', b'{"x":"\\ud800"}'):
            with self.subTest(raw=raw):
                self.rejects("social_token_json", token(raw=raw))
        self.rejects("social_token_json", token(header=[]))

    def test_unknown_optional_values_are_bounded_and_never_projected(self):
        raw = token(claims(extra={"secret": "do-not-project"}, tcp={"addr": "8.8.8.8:12101", "rc4": True,
                           "addition_addrs": ["private-routing-not-used"], "signature": "not-used"}))
        session = self.parse(raw)
        self.assertFalse(hasattr(session, "claims"))
        self.assertFalse(hasattr(session.route, "addition_addrs"))
        self.assertNotIn("do-not-project", json.dumps(social.social_token_metadata(session, now=NOW)))
        nested = 1
        for _ in range(18):
            nested = [nested]
        self.rejects("social_token_json", token(claims(extra=nested)))
        self.rejects("social_token_json", token(claims(extra=list(range(513)))))

    def test_repr_and_metadata_do_not_export_secret_or_route_or_claim_identity(self):
        session = self.parse()
        metadata = social.social_token_metadata(session, now=NOW)
        output = repr(session) + repr(session.route) + json.dumps(metadata)
        for private in (session.token, session.aid, session.route.host, str(session.route.port)):
            self.assertNotIn(private, output)
        self.assertEqual(set(metadata), {"token_bytes", "expires_at", "identity_claim_matches",
            "time_claims_current", "tcp_route_supported", "jwt_signature_verified", "social_session_accepted"})
        self.assertIs(metadata["jwt_signature_verified"], False)
        self.assertIs(metadata["social_session_accepted"], False)
        self.assertEqual(metadata["expires_at"], 1100)

    def test_signature_format_is_not_cryptographic_verification(self):
        for signature in (b"fake", b"another-untrusted-signature"):
            session = self.parse(token(signature=signature))
            self.assertFalse(social.social_token_metadata(session, now=NOW)["jwt_signature_verified"])

    def test_metadata_rechecks_expiry_and_rejects_forged_dataclass_fields(self):
        session = self.parse()
        with self.assertRaises(social.SocialSessionError) as expired:
            social.social_token_metadata(session, now=1100)
        self.assertEqual(expired.exception.code, "social_token_time")
        with self.assertRaises(social.SocialSessionError) as changed:
            social.social_token_metadata(replace(session, route=social.SocialRoute("1.1.1.1", 12101)), now=NOW)
        self.assertEqual(changed.exception.code, "social_session_context")
        with self.assertRaises(social.SocialSessionError):
            social.social_token_metadata({"token": session.token}, now=NOW)

    def test_callback_owned_value_has_no_mutation_or_pickle_path(self):
        session = self.parse()
        with self.assertRaises(FrozenInstanceError):
            session.token = "changed"
        with self.assertRaises(social.SocialSessionError) as saved:
            pickle.dumps(session)
        self.assertEqual(saved.exception.code, "social_session_persistence_unsupported")

    def test_no_network_file_capture_or_installed_runtime_reads(self):
        with patch("builtins.open", side_effect=AssertionError("file access")), \
                patch("socket.create_connection", side_effect=AssertionError("connection")), \
                patch("socket.getaddrinfo", side_effect=AssertionError("DNS")):
            session = self.parse()
            social.social_token_metadata(session, now=NOW)

    def test_unknown_error_text_cannot_escape_fixed_codes(self):
        for value in ("secret-token-in-error", None, {}, True):
            error = social.SocialSessionError(value)
            self.assertEqual(error.code, "social_session_invalid")
            self.assertEqual(str(error), "social_session_invalid")


if __name__ == "__main__":
    unittest.main()
