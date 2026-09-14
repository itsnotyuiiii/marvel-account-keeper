"""One-time, local-only backup before a cloud build opens an existing vault."""
import json
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone


def checkpoint_existing_data(data_dir):
    root = Path(data_dir)
    marker = root / "cloud-upgrade-checkpoint.json"
    vault = root / "vault.json"
    if marker.exists() or not vault.is_file():
        return None
    destination = root / "backups" / ("pre-cloud-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex)
    destination.mkdir(parents=True, exist_ok=False)
    # Called after the packaged single-instance lock. Password ciphertext is
    # copied unchanged; no decrypting, account rewrites or cloud calls occur.
    shutil.copy2(vault, destination / vault.name)
    database = root / "match-index.sqlite3"
    if database.is_file():
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as source:
            with closing(sqlite3.connect(destination / database.name)) as target:
                source.backup(target)
    # Existing local NetEase sessions are neither migrated nor opened by cloud
    # mode. Their originals, remembered unlock and all other files stay intact.
    marker.write_text(json.dumps({"version": 1, "backup_directory": str(destination),
                                 "vault_copied_unchanged": True}), encoding="utf-8")
    return destination
