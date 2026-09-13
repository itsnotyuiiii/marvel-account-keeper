"""Isolated Windows native-NetEase desktop packaging; no game/session inputs.

--dry-run validates the public input pins and prints the planned command without
creating directories, writing build metadata, or invoking PyInstaller. Actual
builds use fresh timestamp/UUID output, work and spec locations; failed builds
are retained. Never calls build.py or deletes/replaces build/dist contents.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
NAME = "MarvelRivalsAccountTracker-Native"
PUBLIC_PINS = {
    "runtime-inputs/3848703.json": "0111accea3a87a8ed6f45190f41cf5e517b400944c6342bdf689afa34bbb1d81",
    "runtime-inputs/3848703-social.json": "c3d1a44164613862529a63f4598b01af969dcb2b881107ae12c5edbb0eae09e4",
}


def validate_inputs(root):
    for relative, digest in PUBLIC_PINS.items():
        path = root / relative
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 1024 * 1024:
            raise ValueError("Native desktop public input is missing or not a regular bounded file.")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Native desktop public input pin does not match the reviewed build.")
    if not (root / "app.py").is_file() or any(not (root / name).is_dir() for name in ("static", "templates")):
        raise ValueError("Native desktop application resources are missing.")


def commit_id(root):
    try:
        result = subprocess.run(["git", "-c", "safe.directory=" + root.as_posix(),
            "rev-parse", "--short=12", "HEAD"], cwd=root,
            capture_output=True, text=True, check=True, timeout=5)
        value = result.stdout.strip()
        if re.fullmatch(r"[0-9a-f]{12}", value):
            return value
    except (OSError, subprocess.SubprocessError):
        pass
    return "local-native-dev"


def build_plan(root, tag, *, executable=None):
    """Pure path/command construction, also usable by offline packaging checks."""
    root = Path(root)
    if type(tag) is not str or re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{32}", tag) is None:
        raise ValueError("Invalid isolated native build identifier.")
    scratch = root / "build" / ("native-" + tag)
    output = root / "dist" / ("native-" + tag)
    work, spec = scratch / "work", scratch / "spec"
    info = scratch / "_build_info.json"
    command = [executable or sys.executable, "-m", "PyInstaller", "--onefile", "--windowed", "--noupx",
        "--name", NAME, "--distpath", str(output), "--workpath", str(work), "--specpath", str(spec),
        "--add-data", str(root / "templates") + ":templates",
        "--add-data", str(root / "static") + ":static", "--add-data", str(info) + ":."]
    # Each manifest is named separately: never add the runtime-inputs directory
    # wholesale, let alone captures, archives, application data or SDK logs.
    for relative in PUBLIC_PINS:
        command += ["--add-data", str(root / relative) + ":runtime-inputs"]
    command += ["--collect-all", "webview", "--collect-all", "clr_loader",
        "--collect-all", "pythonnet", "--copy-metadata", "pythonnet",
        "--collect-all", "msgpack", "--hidden-import", "cryptography.hazmat.primitives.padding",
        "--hidden-import", "local_native_login", "--hidden-import", "local_native_session",
        "--hidden-import", "local_native_provider", "--exclude-module", "cloudscraper"]
    # Existing app imports may pull legacy provider code into PyInstaller's
    # analysis. No research helper scripts or game/capture data are add-data.
    if (root / "icon.ico").is_file():
        command += ["--icon", str(root / "icon.ico")]
    command.append(str(root / "app.py"))
    return {"scratch": scratch, "output": output, "work": work, "spec": spec,
            "info": info, "artifact": output / (NAME + ".exe"), "command": command}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Check public pins and print the plan; create no files and run no build.")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        parser.error("This desktop artifact uses Windows WebView2/pythonnet; build it on Windows.")
    validate_inputs(ROOT)
    tag = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex
    plan = build_plan(ROOT, tag)
    info = {"commit": commit_id(ROOT), "stats_provider": "netease-native", "native_build": "3848703",
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if args.dry_run:
        print(json.dumps({"dry_run": True, "artifact": str(plan["artifact"]), "command": plan["command"],
            "build_info": info, "public_inputs": PUBLIC_PINS, "directories_created": False,
            "pyinstaller_invoked": False, "credential_inputs_included": False}, indent=2))
        return 0
    # mkdir(exist_ok=False) guards every per-run directory. Parent directories
    # may already contain unrelated desktop/server releases and stay untouched.
    plan["scratch"].parent.mkdir(exist_ok=True)
    plan["output"].parent.mkdir(exist_ok=True)
    for name in ("scratch", "output", "work", "spec"):
        plan[name].mkdir(exist_ok=False)
    with plan["info"].open("x", encoding="utf-8") as stream:
        json.dump(info, stream, indent=2)
    subprocess.run(plan["command"], cwd=ROOT, check=True)
    artifact = plan["artifact"]
    if not artifact.is_file() or artifact.stat().st_size <= 0:
        raise RuntimeError("Native desktop build did not produce the expected executable.")
    with artifact.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    print(json.dumps({"artifact": str(artifact), "bytes": artifact.stat().st_size, "sha256": digest,
        "stats_provider": "netease-native", "reviewed_build": "3848703", "credentials_bundled": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
