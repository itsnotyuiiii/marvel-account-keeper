"""Independent public artifact pin and offline runtime-loading checks."""
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

import game_social_token_probe as token_probe
import portable_social_runtime as runtime


ARTIFACT = Path(__file__).resolve().parents[1] / "runtime-inputs" / "3848703-social.json"
EXPECTED_SHA256 = "c3d1a44164613862529a63f4598b01af969dcb2b881107ae12c5edbb0eae09e4"


class PortableSocialRuntimeTest(unittest.TestCase):
    def test_packaged_public_inputs_match_independent_source_review(self):
        raw = ARTIFACT.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), EXPECTED_SHA256)
        self.assertNotIn(b"\r", raw)
        self.assertTrue(raw.endswith(b"\n"))
        self.assertTrue(raw.isascii())
        public = runtime.parse_social_runtime(raw)
        self.assertEqual(public.client_version, "3848703")
        self.assertEqual(public.token_rpc_index, 1538836248)
        self.assertEqual(token_probe.REVIEWED_TOKEN_RPC_INDEX, public.token_rpc_index)
        self.assertEqual(token_probe.REVIEWED_BUILD, public.client_version)
        self.assertEqual(len(public.parameter), 17)
        self.assertEqual(hashlib.sha256(public.parameter).hexdigest(),
                         "e7a322fd20ed8591c366213d4d4d9ca579a5c2d4e0ca11c8e7f63367c7aa5209")

    def test_runtime_load_reads_only_exact_artifact(self):
        real_open = Path.open
        opened = []
        def checked_open(path, *args, **kwargs):
            self.assertEqual(path, ARTIFACT)
            opened.append(path)
            return real_open(path, *args, **kwargs)
        with patch.object(Path, "open", checked_open), \
                patch("socket.create_connection") as network, \
                patch("game_transport_probe.load_public_configuration") as game:
            loaded = runtime.load_social_runtime(ARTIFACT)
        self.assertEqual(opened, [ARTIFACT])
        self.assertEqual(loaded.token_rpc_index, 1538836248)
        network.assert_not_called()
        game.assert_not_called()

    def test_changed_unknown_or_oversized_inputs_never_supply_parameters(self):
        raw = ARTIFACT.read_bytes()
        for value in (b"", raw + b"\n", b"x" * 4097, raw.replace(b"3848703", b"3848704"),
                      raw.decode("ascii"), None):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(runtime.SocialRuntimeError):
                runtime.parse_social_runtime(value)


if __name__ == "__main__":
    unittest.main()
