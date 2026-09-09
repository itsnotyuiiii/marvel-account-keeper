"""Offline checks for the metadata/handle diagnostic; never touch the game."""

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock


spec = importlib.util.spec_from_file_location("career_access", Path(__file__).parents[1] / "scripts" / "check-career-access.py")
access = importlib.util.module_from_spec(spec)
spec.loader.exec_module(access)


class CareerAccessTest(unittest.TestCase):
    def api(self, elevated=True, image=access.EXPECTED_IMAGE):
        return Mock(image=Mock(return_value=image), elevated=Mock(return_value=elevated),
                    can_open_for_read=Mock(return_value=(True, 0)))

    def test_default_does_not_request_memory_access(self):
        api = self.api()
        self.assertEqual(access.access_report(api, 123)["outcome"], "metadata_only")
        api.can_open_for_read.assert_not_called()

    def test_non_elevated_or_unknown_caller_does_not_retry_denied_path(self):
        for elevated in (False, None):
            api = self.api(elevated=elevated)
            report = access.access_report(api, 123, check_read_access=True)
            self.assertFalse(report["read_access_attempted"])
            api.can_open_for_read.assert_not_called()

    def test_wrong_image_or_missing_process_is_not_targeted(self):
        for image in ("", r"C:\Other\Marvel-Win64-Shipping.exe"):
            api = self.api(image=image)
            report = access.access_report(api, 123, check_read_access=True)
            self.assertEqual(report["outcome"], "wrong_or_unavailable_game_process")
            api.can_open_for_read.assert_not_called()

    def test_explicit_admin_check_opens_only_once_and_collects_no_credentials(self):
        api = self.api()
        report = access.access_report(api, 123, check_read_access=True)
        self.assertEqual(report["outcome"], "read_handle_available")
        self.assertFalse(report["memory_read"])
        self.assertFalse(report["credentials_collected"])
        api.can_open_for_read.assert_called_once_with(123)

    def test_denial_is_reported_without_retry_or_escalation(self):
        api = self.api()
        api.can_open_for_read.return_value = (False, 5)
        report = access.access_report(api, 123, check_read_access=True)
        self.assertEqual(report["outcome"], "read_handle_denied")
        self.assertEqual(report["win32_error"], 5)
        api.can_open_for_read.assert_called_once_with(123)


if __name__ == "__main__":
    unittest.main()
