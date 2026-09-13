"""Import/public-input smoke test without desktop or offline analysis packages.

Runs on the current OS; a Windows pass is not a claim of Linux verification.
No SDK context, game files or network calls are used.
"""
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = r'''
import importlib.abc
import sys
from pathlib import Path

blocked = {'flask', 'cloudscraper', 'webview', 'dpkt', 'pefile', 'capstone',
           'xdis', 'lz4', 'blake3', 'pyooz', 'win32api', 'win32crypt'}

class NoDesktopOrResearch(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in blocked:
            raise ImportError('unexpected nonportable dependency')

sys.meta_path.insert(0, NoDesktopOrResearch())
import portable_game_runtime as game
import portable_social_runtime as social
import standalone_profile_bundle
import web_sdk_history_chain
import web_sdk_history_login
import requests

root = Path.cwd()
provenance, pin = game.reviewed_runtime_pin('3848703')
public = game.load_runtime_manifest(root / 'runtime-inputs' / '3848703.json',
                                  expected_sha256=pin, expected_provenance=provenance)
matrix = social.load_social_runtime(root / 'runtime-inputs' / '3848703-social.json')
assert public.provenance.client_version == '3848703'
assert len(matrix.parameter) == 17
assert not any(name.split('.')[0] in blocked for name in sys.modules)
print('portable_imports_and_public_inputs_verified')
'''


class StandalonePortabilityTest(unittest.TestCase):
    def test_imports_without_desktop_or_research_dependencies(self):
        result = subprocess.run([sys.executable, '-B', '-c', SCRIPT], cwd=ROOT,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'portable_imports_and_public_inputs_verified')


if __name__ == '__main__':
    unittest.main()
