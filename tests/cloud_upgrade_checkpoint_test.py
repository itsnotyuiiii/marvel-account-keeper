import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from cloud_upgrade_checkpoint import checkpoint_existing_data


class CloudUpgradeCheckpointTest(unittest.TestCase):
    def test_preserves_vault_and_local_history_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            content = json.dumps({"accounts": [{"id": "fixture", "password_enc": "ciphertext",
                "notes": "keep", "current_rank": "Bronze III"}]}).encode()
            (root / "vault.json").write_bytes(content)
            (root / "netease-session.sqlite3").write_bytes(b"do-not-open")
            with closing(sqlite3.connect(root / "match-index.sqlite3")) as connection:
                connection.execute("create table facts (value text)")
                connection.execute("insert into facts values ('synthetic-match')")
                connection.commit()
            result = checkpoint_existing_data(root)
            self.assertEqual((result / "vault.json").read_bytes(), content)
            self.assertEqual((root / "vault.json").read_bytes(), content)
            self.assertEqual((root / "netease-session.sqlite3").read_bytes(), b"do-not-open")
            with closing(sqlite3.connect(result / "match-index.sqlite3")) as connection:
                self.assertEqual(connection.execute("select value from facts").fetchone()[0], "synthetic-match")
            self.assertIsNone(checkpoint_existing_data(root))
            self.assertEqual(len(list((root / "backups").iterdir())), 1)

    def test_new_empty_install_creates_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertIsNone(checkpoint_existing_data(Path(temporary)))
            self.assertEqual(list(Path(temporary).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
