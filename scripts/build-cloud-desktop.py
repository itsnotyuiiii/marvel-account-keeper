"""Build an isolated Windows cloud-connected desktop without credential inputs.

--dry-run checks the application inputs and prints the planned command without
creating files or invoking PyInstaller. Each build uses fresh timestamp/UUID
paths. Existing builds, releases, user data and failed outputs stay untouched.
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
NAME = "MarvelRivalsAccountTracker-Cloud"
PROVIDER = "netease-cloud"
APPLICATION_FILES = ("app.py", "cloud_desktop_provider.py", "native_rank_display.py", "cloud_upgrade_checkpoint.py")


def validate_inputs(root):
    root = Path(root)
    for name in APPLICATION_FILES:
        path = root / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise ValueError("Cloud desktop application module is missing or invalid.")
    if any(not (root / name).is_dir() or (root / name).is_symlink()
           for name in ("static", "templates")):
        raise ValueError("Cloud desktop application resources are missing or invalid.")


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
    return "local-cloud-dev"


def build_plan(root, tag, *, executable=None):
    """Construct paths and arguments without reading credentials or writing files."""
    root = Path(root)
    if type(tag) is not str or re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{32}", tag) is None:
        raise ValueError("Invalid isolated cloud build identifier.")
    scratch = root / "build" / ("cloud-" + tag)
    output = root / "dist" / ("cloud-" + tag)
    work, spec = scratch / "work", scratch / "spec"
    info = scratch / "_build_info.json"
    command = [executable or sys.executable, "-m", "PyInstaller", "--onefile", "--windowed", "--noupx",
        "--name", NAME, "--distpath", str(output), "--workpath", str(work), "--specpath", str(spec),
        "--add-data", str(root / "templates") + ":templates",
        "--add-data", str(root / "static") + ":static", "--add-data", str(info) + ":.",
        "--collect-all", "webview", "--collect-all", "clr_loader",
        "--collect-all", "pythonnet", "--copy-metadata", "pythonnet",
        "--collect-all", "msgpack", "--hidden-import", "cryptography.hazmat.primitives.padding",
        "--hidden-import", "cloud_desktop_provider", "--hidden-import", "native_rank_display",
        "--exclude-module", "cloudscraper"]
    # Only the three resource inputs above are added as data. No runtime
    # manifest, local connection file, game material, vault, session or key is
    # a build input. Existing application imports remain available to normal
    # dependency analysis; this build selects the cloud path through metadata.
    if (root / "icon.ico").is_file():
        command += ["--icon", str(root / "icon.ico")]
    command.append(str(root / "app.py"))
    return {"scratch": scratch, "output": output, "work": work, "spec": spec,
            "info": info, "artifact": output / (NAME + ".exe"), "command": command}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
        help="Validate inputs and print the plan; create no files and run no build.")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        parser.error("This desktop artifact uses Windows WebView2/pythonnet; build it on Windows.")
    validate_inputs(ROOT)
    tag = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex
    plan = build_plan(ROOT, tag)
    info = {"commit": commit_id(ROOT), "stats_provider": PROVIDER,
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if args.dry_run:
        print(json.dumps({"dry_run": True, "artifact": str(plan["artifact"]), "command": plan["command"],
            "build_info": info, "directories_created": False, "pyinstaller_invoked": False,
            "credential_inputs_included": False, "runtime_manifests_included": False}, indent=2))
        return 0
    plan["scratch"].parent.mkdir(exist_ok=True)
    plan["output"].parent.mkdir(exist_ok=True)
    for name in ("scratch", "output", "work", "spec"):
        plan[name].mkdir(exist_ok=False)
    with plan["info"].open("x", encoding="utf-8") as stream:
        json.dump(info, stream, indent=2)
    subprocess.run(plan["command"], cwd=ROOT, check=True)
    artifact = plan["artifact"]
    if artifact.is_symlink() or not artifact.is_file() or artifact.stat().st_size <= 0:
        raise RuntimeError("Cloud desktop build did not produce the expected executable.")
    with artifact.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    print(json.dumps({"artifact": str(artifact), "bytes": artifact.stat().st_size, "sha256": digest,
        "stats_provider": PROVIDER, "credentials_bundled": False,
        "runtime_manifests_bundled": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
