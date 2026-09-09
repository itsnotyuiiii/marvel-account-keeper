"""Build the experimental Career desktop app in fresh, isolated output folders.

Run after installing requirements-dev.txt and requirements-research.txt.
Never deletes existing builds, vaults, captures, or game files. No session
material or game archive is bundled; the app reads explicitly supplied local
files at runtime. Prints the resulting executable path on success.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.platform != "win32":
        raise SystemExit("The experimental Netsh Career build currently requires Windows.")
    name = "MarvelRivalsAccountTracker-Career"
    tag = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    output = ROOT / "dist" / ("career-" + tag)
    scratch_root = ROOT / "build"
    scratch_root.mkdir(exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="career-", dir=scratch_root))
    output.mkdir(parents=True, exist_ok=False)
    info = scratch / "_build_info.json"
    info.write_text(json.dumps({"commit": "local-netease-full-career", "stats_provider": "netease",
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}), encoding="utf-8")
    command = [sys.executable, "-m", "PyInstaller", "--onefile", "--windowed", "--noupx",
               "--name", name, "--distpath", str(output), "--workpath", str(scratch / "work"), "--specpath", str(scratch),
               "--add-data", str(ROOT / "templates") + ":templates", "--add-data", str(ROOT / "static") + ":static", "--add-data", str(info) + ":.",
               "--collect-all", "webview", "--collect-all", "clr_loader", "--collect-all", "pythonnet",
               "--copy-metadata", "pythonnet", "--collect-all", "xdis", "--collect-all", "lz4", "--hidden-import", "dpkt",
               "--hidden-import", "cryptography.hazmat.primitives.padding",
               "--exclude-module", "cloudscraper",
               "--icon", str(ROOT / "icon.ico")]
    for helper in ("inspect-netsh-career.py", "summarize-career-capture.py", "inspect-script-container.py", "inspect-sdk-log.py"):
        command += ["--add-data", str(ROOT / "scripts" / helper) + ":scripts"]
    command.append(str(ROOT / "app.py"))
    subprocess.run(command, cwd=ROOT, check=True)
    artifact = output / (name + ".exe")
    if not artifact.is_file():
        raise SystemExit("Build did not produce the expected executable.")
    print(json.dumps({"artifact": str(artifact), "bytes": artifact.stat().st_size, "credentials_bundled": False}))


if __name__ == "__main__":
    main()
