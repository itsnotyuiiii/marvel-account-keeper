"""Real public-only artifact, no installed files, account data or network."""
from contextlib import ExitStack
from pathlib import Path
import unittest
from unittest.mock import patch

import portable_game_runtime as runtime


ARTIFACT = Path(__file__).resolve().parents[1] / "runtime-inputs" / "3825464.json"


class ReviewedRuntimeArtifactTest(unittest.TestCase):
    def test_reviewed_bytes_load_with_independent_pins_without_desktop_sources(self):
        original_open = Path.open
        paths = []

        def only_artifact(path, *args, **kwargs):
            self.assertEqual(path, ARTIFACT)
            self.assertEqual(args, ("rb",))
            paths.append(path)
            return original_open(path, *args, **kwargs)

        targets = ("game_transport_probe.load_public_configuration",
                   "game_career_probe.load_career_methods",
                   "game_login_probe.load_own_login_inputs",
                   "game_login_probe.advertised_server",
                   "game_login_probe.load_config_index",
                   "socket.create_connection", "requests.get", "requests.post")
        with ExitStack() as stack:
            stack.enter_context(patch.object(Path, "open", only_artifact))
            guards = [stack.enter_context(patch(target, side_effect=AssertionError("unexpected source")))
                      for target in targets]
            public = runtime.load_runtime_manifest(
                ARTIFACT, expected_sha256=runtime.REVIEWED_MANIFEST_SHA256,
                expected_provenance=runtime.SUPPORTED_PROVENANCE)
        self.assertEqual(paths, [ARTIFACT])
        for guard in guards:
            guard.assert_not_called()
        self.assertEqual(public.client_version, "3825464")
        self.assertEqual(public.career_methods.request, 113612951)
        self.assertEqual(public.career_methods.reply, 1993623137)
        self.assertEqual(public.login_list_url, runtime.LOGIN_LIST_URL)

    def test_portable_exact_byte_format(self):
        raw = ARTIFACT.read_bytes()
        self.assertEqual(len(raw), 774)
        self.assertNotIn(b"\r", raw)
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(raw.decode("ascii").encode("ascii"), raw)


if __name__ == "__main__":
    unittest.main()
