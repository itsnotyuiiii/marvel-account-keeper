"""Offline checks for isolated cloud packaging and synthetic smoke boundaries."""

import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = load_script("build-cloud-desktop")
smoke = load_script("check-cloud-desktop")
TAG = "20260913-010203-" + "a" * 32


class CloudDesktopBuildTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in build.APPLICATION_FILES:
            (self.root / name).write_text("# synthetic application input\n", encoding="utf-8")
        for name in ("static", "templates"):
            (self.root / name).mkdir()

    def test_cloud_validation_does_not_require_runtime_manifests(self):
        build.validate_inputs(self.root)
        self.assertFalse((self.root / "runtime-inputs").exists())

    def test_missing_or_empty_application_module_is_rejected(self):
        for name in build.APPLICATION_FILES:
            with self.subTest(name=name):
                (self.root / name).write_text("", encoding="utf-8")
                with self.assertRaises(ValueError):
                    build.validate_inputs(self.root)
                (self.root / name).unlink()
                with self.assertRaises(ValueError):
                    build.validate_inputs(self.root)
                (self.root / name).write_text("# restored\n", encoding="utf-8")

    def test_resource_directories_are_required(self):
        (self.root / "static").rmdir()
        with self.assertRaises(ValueError):
            build.validate_inputs(self.root)

    def test_plan_is_isolated_and_pure(self):
        before = sorted(str(path) for path in self.root.rglob("*"))
        plan = build.build_plan(self.root, TAG, executable="python-test")
        self.assertEqual(plan["artifact"], self.root / "dist" / ("cloud-" + TAG) / (build.NAME + ".exe"))
        self.assertEqual(plan["scratch"], self.root / "build" / ("cloud-" + TAG))
        self.assertEqual(plan["command"][:3], ["python-test", "-m", "PyInstaller"])
        self.assertEqual(plan["command"][-1], str(self.root / "app.py"))
        self.assertEqual(sorted(str(path) for path in self.root.rglob("*")), before)

    def test_only_named_static_resources_and_metadata_are_added(self):
        # Nearby private-looking files must not become arguments or data assets.
        for name in ("vault.json", "cloud-connection.bin", "netease-session.sqlite3", "accessKeys.csv"):
            (self.root / name).write_text("synthetic-do-not-package", encoding="utf-8")
        plan = build.build_plan(self.root, TAG)
        command = plan["command"]
        data = [command[index + 1] for index, item in enumerate(command) if item == "--add-data"]
        self.assertEqual(data, [str(self.root / "templates") + ":templates",
            str(self.root / "static") + ":static", str(plan["info"]) + ":."])
        self.assertNotIn("runtime-inputs", " ".join(command))
        self.assertNotIn("synthetic-do-not-package", " ".join(command))
        self.assertNotIn("--clean", command)
        self.assertNotIn("--noconfirm", command)

    def test_cloud_modules_are_explicit_hidden_imports(self):
        command = build.build_plan(self.root, TAG)["command"]
        imports = [command[index + 1] for index, item in enumerate(command) if item == "--hidden-import"]
        self.assertIn("cloud_desktop_provider", imports)
        self.assertIn("native_rank_display", imports)
        self.assertNotIn("local_native_login", imports)
        self.assertNotIn("local_native_session", imports)

    def test_invalid_tags_cannot_escape_build_parent(self):
        for tag in (None, 1, "", "../release", TAG + "/other", TAG.upper(), TAG + "\n"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                build.build_plan(self.root, tag)

    def test_optional_icon_is_only_added_when_present(self):
        self.assertNotIn("--icon", build.build_plan(self.root, TAG)["command"])
        (self.root / "icon.ico").write_bytes(b"synthetic-icon")
        command = build.build_plan(self.root, TAG)["command"]
        self.assertEqual(command[command.index("--icon") + 1], str(self.root / "icon.ico"))

    def test_commit_id_uses_command_scoped_safe_directory(self):
        result = subprocess.CompletedProcess([], 0, "0123456789ab\n", "")
        with patch.object(build.subprocess, "run", return_value=result) as run:
            self.assertEqual(build.commit_id(self.root), "0123456789ab")
        self.assertIn("safe.directory=" + self.root.as_posix(), run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs["cwd"], self.root)

    def test_unavailable_or_invalid_commit_id_is_cloud_specific(self):
        with patch.object(build.subprocess, "run", side_effect=OSError):
            self.assertEqual(build.commit_id(self.root), "local-cloud-dev")
        result = subprocess.CompletedProcess([], 0, "unexpected output\n", "")
        with patch.object(build.subprocess, "run", return_value=result):
            self.assertEqual(build.commit_id(self.root), "local-cloud-dev")

    def test_dry_run_creates_no_files_and_never_builds(self):
        before = sorted(str(path) for path in self.root.rglob("*"))
        output = io.StringIO()
        with patch.object(build, "ROOT", self.root), patch.object(build.sys, "platform", "win32"), \
                patch.object(build, "commit_id", return_value="0123456789ab"), \
                patch.object(build.subprocess, "run") as run, contextlib.redirect_stdout(output):
            self.assertEqual(build.main(["--dry-run"]), 0)
        run.assert_not_called()
        value = json.loads(output.getvalue())
        self.assertEqual(value["build_info"]["stats_provider"], "netease-cloud")
        for field in ("directories_created", "pyinstaller_invoked", "credential_inputs_included", "runtime_manifests_included"):
            self.assertIs(value[field], False)
        self.assertEqual(sorted(str(path) for path in self.root.rglob("*")), before)

    def test_successful_build_reports_only_artifact_and_public_metadata(self):
        plan = build.build_plan(self.root, TAG)
        artifact_bytes = b"synthetic-packaged-executable"

        def package(command, **kwargs):
            self.assertEqual(command, plan["command"])
            self.assertEqual(kwargs["cwd"], self.root)
            self.assertTrue(kwargs["check"])
            plan["artifact"].write_bytes(artifact_bytes)

        output = io.StringIO()
        with patch.object(build, "ROOT", self.root), patch.object(build.sys, "platform", "win32"), \
                patch.object(build, "commit_id", return_value="0123456789ab"), \
                patch.object(build, "build_plan", return_value=plan), \
                patch.object(build.subprocess, "run", side_effect=package), contextlib.redirect_stdout(output):
            self.assertEqual(build.main([]), 0)
        info = json.loads(plan["info"].read_text(encoding="utf-8"))
        self.assertEqual(set(info), {"commit", "stats_provider", "built_at"})
        self.assertEqual(info["stats_provider"], "netease-cloud")
        result = json.loads(output.getvalue())
        self.assertEqual(result["bytes"], len(artifact_bytes))
        self.assertEqual(result["sha256"], build.hashlib.sha256(artifact_bytes).hexdigest())
        self.assertIs(result["credentials_bundled"], False)
        self.assertIs(result["runtime_manifests_bundled"], False)

    def test_existing_isolated_directory_is_never_overwritten(self):
        plan = build.build_plan(self.root, TAG)
        plan["scratch"].mkdir(parents=True)
        marker = plan["scratch"] / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        with patch.object(build, "ROOT", self.root), patch.object(build.sys, "platform", "win32"), \
                patch.object(build, "commit_id", return_value="0123456789ab"), \
                patch.object(build, "build_plan", return_value=plan), \
                patch.object(build.subprocess, "run") as run, self.assertRaises(FileExistsError):
            build.main([])
        run.assert_not_called()
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")


class CloudDesktopSmokeBoundaryTest(unittest.TestCase):
    def test_live_mode_requires_both_explicit_inputs_and_bounded_dpapi_file(self):
        self.assertIsNone(smoke.live_options(None, None))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "connection.dpapi"
            path.write_bytes(smoke.DPAPI_PREFIX + b"synthetic-ciphertext")
            self.assertEqual(smoke.live_options(str(path), smoke.TEST_UID), path.absolute())
            for config, uid in ((str(path), None), (None, smoke.TEST_UID), (str(path), "../123456"),
                                (str(path), "0123456"), (str(path), "123456\n")):
                with self.subTest(uid=uid), self.assertRaises(smoke.SmokeError):
                    smoke.live_options(config, uid)
            path.write_bytes(b"x" * 16385)
            with self.assertRaises(smoke.SmokeError):
                smoke.live_options(str(path), smoke.TEST_UID)

    def test_live_config_copy_is_ciphertext_only_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.dpapi"
            fixture = root / "fixture"
            fixture.mkdir()
            encrypted = smoke.DPAPI_PREFIX + b"synthetic-ciphertext"
            source.write_bytes(encrypted)
            smoke.copy_live_config(source, fixture)
            self.assertEqual((fixture / smoke.CLOUD_CONFIG_FILE).read_bytes(), encrypted)
            self.assertEqual(source.read_bytes(), encrypted)
            with self.assertRaises(FileExistsError):
                smoke.copy_live_config(source, fixture)
            (fixture / smoke.CLOUD_CONFIG_FILE).unlink()
            source.write_bytes(b"not-encrypted-synthetic-token")
            with self.assertRaises(smoke.SmokeError):
                smoke.copy_live_config(source, fixture)
            self.assertFalse((fixture / smoke.CLOUD_CONFIG_FILE).exists())

    def live_card(self):
        fields = {"rivals_uid": smoke.TEST_UID, "rivals_platform": "pc", "in_game_name": "",
            "password": "synthetic-secret", "notes": "keep", "current_rank": "Gold II"}
        profile = {"uid": smoke.TEST_UID, "platform": "pc", "identity_verified": True,
            "owner_authentication_verified": True, "game_login_verified": True, "social_session_accepted": True,
            "identity": {"uid": smoke.TEST_UID, "platform": "pc", "in_game_name": "Synthetic IGN"},
            "ranks": {"status": "available", "data": {"uid": smoke.TEST_UID, "season_count": 1, "seasons": [{}]}},
            "ranks_verified": True,
            "matches": {"status": "available", "data": {"uid": smoke.TEST_UID, "match_count": 0, "matches": []}},
            "matches_verified": True, "career_verified": True,
            "career": {"status": "available", "data": {"uid": smoke.TEST_UID, "statistics": {"matches": 12}}}}
        card = {**fields, "in_game_name": "Synthetic IGN", "last_refresh_status": "ok", "last_refresh_code": None,
            "last_refresh_source": "netease", "stats_provider": "netease", "last_refresh_ts": 1000,
            "netease_native": {"fetched_at": 1000, "profile": profile,
                "rank_display": {"uid": smoke.TEST_UID, "status": "estimated", "level": 14,
                                 "placements_total": 10, "placements_completed": 3}}}
        return card, fields

    def test_live_summary_emits_only_bounded_public_facts(self):
        card, fields = self.live_card()
        card["unexpected_token"] = "never-emit"
        with patch.object(smoke.time, "time", return_value=1020):
            value = smoke.live_summary(card, smoke.TEST_UID, fields, 1000)
        self.assertEqual(value, {"uid": smoke.TEST_UID, "in_game_name": "Synthetic IGN", "fetched_at": 1000,
            "rank_level": 14, "placement_status": "estimated", "placements_completed": 3, "placements_total": 10,
            "season_count": 1, "match_count": 0, "career_matches": 12})
        self.assertNotIn("synthetic-secret", json.dumps(value))
        self.assertNotIn("never-emit", json.dumps(value))

    def test_live_summary_rejects_wrong_identity_stale_data_and_changed_private_fields(self):
        original, fields = self.live_card()
        mutations = (
            lambda card: card.update(rivals_uid="123456"),
            lambda card: card.update(password="changed"),
            lambda card: card.update(notes="changed"),
            lambda card: card.update(current_rank="changed"),
            lambda card: card["netease_native"].update(fetched_at=1),
            lambda card: card["netease_native"]["profile"].update(identity_verified=False),
            lambda card: card["netease_native"]["rank_display"].update(uid="123456"),
            lambda card: card["netease_native"]["rank_display"].update(placements_completed=99),
        )
        with patch.object(smoke.time, "time", return_value=1020):
            for mutate in mutations:
                card = copy.deepcopy(original)
                mutate(card)
                with self.assertRaises(smoke.SmokeError):
                    smoke.live_summary(card, smoke.TEST_UID, fields, 1000)

    def test_local_http_timeout_is_bounded(self):
        http = smoke.LocalHttp(12345)
        with patch.object(http.opener, "open") as network:
            for timeout in (0, -1, 121, None, True):
                with self.assertRaises(smoke.SmokeError):
                    http.request("/api/accounts", timeout=timeout)
            network.assert_not_called()

    def test_child_environment_isolates_all_user_data_and_operator_inputs(self):
        fixture = Path("C:/synthetic-fixture")
        original = {"PATH": "public-path", "APPDATA": "operator-data", "NETEASE_API_TOKEN": "synthetic-token",
            "AWS_PROFILE": "operator", "AWS_SECRET_ACCESS_KEY": "synthetic-key", "MARVEL_KEEPER_DATA": "real-vault",
            "marvel_cloud_token": "synthetic-token-2", "NETEASE_LOG": "private-log"}
        with patch.dict(os.environ, original, clear=True):
            value = smoke.child_environment(fixture)
        self.assertEqual(value["PATH"], "public-path")
        self.assertEqual(value["MARVEL_KEEPER_DATA"], str(fixture))
        self.assertEqual(value["APPDATA"], str(fixture / "AppData" / "Roaming"))
        self.assertEqual(value["USERPROFILE"], str(fixture))
        self.assertFalse(any(key.upper().startswith(("NETEASE_", "AWS_")) for key in value))
        self.assertEqual([key for key in value if key.upper().startswith("MARVEL_")], ["MARVEL_KEEPER_DATA"])

    def test_fixture_binding_requires_exact_marker_and_provider(self):
        http = Mock()
        for response, expected in (
            ({"stats_provider": "netease", "ui_options": {"cloud_smoke_id": "ours"}}, True),
            ({"stats_provider": "tracker", "ui_options": {"cloud_smoke_id": "ours"}}, False),
            ({"stats_provider": "netease", "ui_options": {"cloud_smoke_id": "other"}}, False),
            ({"stats_provider": "netease", "ui_options": []}, False),
            ({"stats_provider": "netease"}, False),
        ):
            http.json.return_value = response
            self.assertIs(smoke.fixture_owned(http, "ours"), expected)
        http.json.side_effect = OSError("synthetic")
        self.assertFalse(smoke.fixture_owned(http, "ours"))

    def test_external_local_http_paths_and_redirects_are_rejected(self):
        http = smoke.LocalHttp(12345)
        with patch.object(http.opener, "open") as network:
            for path in ("https://example.invalid", "//example.invalid", "relative"):
                with self.assertRaises(smoke.SmokeError):
                    http.request(path)
            network.assert_not_called()
        with self.assertRaises(smoke.SmokeError):
            smoke.NoRedirect().redirect_request(None)

    def test_shutdown_never_targets_unbound_listener(self):
        process, http = Mock(), Mock()
        http.json.return_value = {"stats_provider": "netease", "ui_options": {"cloud_smoke_id": "other"}}
        with self.assertRaises(smoke.SmokeError):
            smoke.stop_fixture(process, http, "ours")
        http.json.assert_called_once_with("/api/status")
        process.wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
