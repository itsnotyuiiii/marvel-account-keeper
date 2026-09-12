"""Real public-only artifact, no installed files, account data or network."""
from contextlib import ExitStack
import base64
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import portable_game_runtime as runtime


ARTIFACT = Path(__file__).resolve().parents[1] / "runtime-inputs" / "3825464.json"
ARTIFACT_ROOT = ARTIFACT.parent
# Independent test expectations from the reviewed distribution evidence, not
# values read from the candidate artifacts or learned from the runtime registry.
REVIEWED = {
    "3825464": {
        "sha256": "63d536706d9cb055770129ca413950861d2553cb6c977bdee4f113fa66ccbc28",
        "archive_sha256": "4dbe9d3246a05d4a402373cc11c03a938692db9bc16aaaf939ddff4e8682d22e",
        "image_sha256": "54154e879731f3f6fad32371bff78d62629c3d87ad5443dc9466871312a76714",
    },
    "3848703": {
        "sha256": "0111accea3a87a8ed6f45190f41cf5e517b400944c6342bdf689afa34bbb1d81",
        "archive_sha256": "07a3dcf20d539b9a613246047f5f91e4c7b44de62e96e24c47acea1cef5d1f9f",
        "image_sha256": "f9b9afda45adbb8db8162c12906d79d82fe627afecab5c0c3bbf0b5edcea2612",
    },
}


class ReviewedRuntimeArtifactTest(unittest.TestCase):
    def test_reviewed_bytes_load_with_independent_pins_without_desktop_sources(self):
        original_open = Path.open
        targets = ("game_transport_probe.load_public_configuration",
                   "game_career_probe.load_career_methods",
                   "game_login_probe.load_own_login_inputs",
                   "game_login_probe.advertised_server",
                   "game_login_probe.load_config_index",
                   "socket.create_connection", "requests.get", "requests.post")
        for version, expected in REVIEWED.items():
            with self.subTest(version=version):
                artifact = ARTIFACT_ROOT / (version + ".json")
                paths = []

                def only_artifact(path, *args, **kwargs):
                    self.assertEqual(path, artifact)
                    self.assertEqual(args, ("rb",))
                    paths.append(path)
                    return original_open(path, *args, **kwargs)

                provenance = runtime.RuntimeProvenance(
                    expected["archive_sha256"], expected["image_sha256"], version)
                with ExitStack() as stack:
                    stack.enter_context(patch.object(Path, "open", only_artifact))
                    guards = [stack.enter_context(patch(target, side_effect=AssertionError("unexpected source")))
                              for target in targets]
                    public = runtime.load_runtime_manifest(
                        artifact, expected_sha256=expected["sha256"], expected_provenance=provenance)
                self.assertEqual(paths, [artifact])
                for guard in guards:
                    guard.assert_not_called()
                self.assertEqual(public.client_version, version)
                self.assertEqual(public.provenance, provenance)
                self.assertEqual(public.manifest_sha256, expected["sha256"])
                self.assertEqual(public.career_methods.request, 113612951)
                self.assertEqual(public.career_methods.reply, 1993623137)
                self.assertEqual(public.login_list_url, runtime.LOGIN_LIST_URL)

    def test_portable_exact_byte_format(self):
        for version, expected in REVIEWED.items():
            with self.subTest(version=version):
                raw = (ARTIFACT_ROOT / (version + ".json")).read_bytes()
                self.assertEqual(len(raw), 774)
                self.assertNotIn(b"\r", raw)
                self.assertTrue(raw.endswith(b"\n"))
                self.assertEqual(raw.decode("ascii").encode("ascii"), raw)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), expected["sha256"])

    def test_both_artifacts_match_independent_reviewed_public_evidence(self):
        for version, expected in REVIEWED.items():
            with self.subTest(version=version):
                value = json.loads((ARTIFACT_ROOT / (version + ".json")).read_bytes())
                self.assertEqual(value["provenance"], {
                    "archive_sha256": expected["archive_sha256"],
                    "image_sha256": expected["image_sha256"], "client_version": version})
                self.assertEqual(value["schema_version"], 1)
                self.assertEqual(value["game_id"], "x20naxx2gb")
                self.assertEqual(value["career_methods"], {"request": 113612951, "reply": 1993623137})
                self.assertEqual(value["login_list_url"], "https://x20na.update.easebar.com/server_pc_prod_serverB")
                parameter = base64.b64decode(value["transport"]["handshake_parameter_base64"], validate=True)
                public_der = base64.b64decode(value["transport"]["public_key_der_base64"], validate=True)
                self.assertEqual(len(parameter), 18)
                self.assertEqual(hashlib.sha256(parameter).hexdigest(),
                                 "e0f8b1c2e1e906a1d4cf345c554852a844cc8bc1411c0b7ba2385885cd4a9c49")
                self.assertEqual(hashlib.sha256(public_der).hexdigest(),
                                 "d37c1ab339cfd920f261b8457f9e5dadac6c076684ba93c51987d2299464401c")
                provenance, pin = runtime.reviewed_runtime_pin(version)
                self.assertEqual(asdict(provenance), value["provenance"])
                self.assertEqual(pin, expected["sha256"])

    def test_cross_build_digest_or_provenance_cannot_select_another_artifact(self):
        for version in REVIEWED:
            with self.subTest(version=version):
                other = next(item for item in REVIEWED if item != version)
                raw = (ARTIFACT_ROOT / (version + ".json")).read_bytes()
                provenance, pin = runtime.reviewed_runtime_pin(version)
                other_provenance, other_pin = runtime.reviewed_runtime_pin(other)
                with self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_manifest_hash_mismatch"):
                    runtime.parse_runtime_manifest(raw, expected_sha256=other_pin,
                                                   expected_provenance=provenance)
                with self.assertRaisesRegex(runtime.RuntimeManifestError, "runtime_provenance_mismatch"):
                    runtime.parse_runtime_manifest(raw, expected_sha256=pin,
                                                   expected_provenance=other_provenance)


if __name__ == "__main__":
    unittest.main()
