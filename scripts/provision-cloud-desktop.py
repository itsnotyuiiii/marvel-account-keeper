"""Operator-only setup: retrieve the existing service bearer into a DPAPI file.

Never bundles credentials or changes AWS resources. The packaged app does not
need boto3 or AWS credentials. Values and exception details are never printed.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--secret-id", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--verify-uid", action="append", default=[])
    args = parser.parse_args()
    from cloud_desktop_provider import CloudDesktopConnection, CLOUD_CONFIG_FILE, DEFAULT_ORIGIN, provision_cloud_config
    from native_rank_display import current_rank_display
    from career_provider import CareerUnavailable
    provider = None
    try:
        root = Path(args.data_dir).resolve()
        path = root / CLOUD_CONFIG_FILE
        if path.exists():
            created = False
        else:
            import boto3
            session = boto3.Session(profile_name=args.profile, region_name="us-east-1")
            token = session.client("secretsmanager").get_secret_value(SecretId=args.secret_id)["SecretString"]
            provision_cloud_config(DEFAULT_ORIGIN, token, path)
            token = None
            created = True
        # This operator verification uses no vault/password. The actual app
        # supplies its normal unlock callback before opening this config.
        provider = CloudDesktopConnection(root, lambda: b"operator-verification-only-00000!"[:32].ljust(32, b"!"))
        status = provider.status()
        if not status["connected"]:
            raise ValueError()
        print(json.dumps({"outcome": "cloud_config_ready", "created": created,
            "path": str(path), "storage": "Windows-user DPAPI", "credentials_printed": False}), flush=True)
        for uid in args.verify_uid:
            try:
                result = provider.refresh_account({"rivals_uid": uid, "rivals_platform": "pc"})
                profile = result["native_profile"]
                display = current_rank_display(profile)
                print(json.dumps({"uid": uid, "outcome": "verified", "in_game_name": result["in_game_name"],
                    "career": profile["career"]["status"], "seasons": profile["ranks"]["data"]["season_count"] if profile["ranks"]["status"] == "available" else None,
                    "matches": profile["matches"]["data"]["match_count"] if profile["matches"]["status"] == "available" else None,
                    "placements": display["placements_label"], "placement_status": display["status"],
                    "rank_level": display["level"], "fetched_at": result["fetched_at"]}, ensure_ascii=True), flush=True)
            except CareerUnavailable as error:
                print(json.dumps({"uid": uid, "outcome": error.code, "message": error.message}), flush=True)
                if error.code != "cloud_profile_not_returned":
                    return 1
        return 0
    except Exception:
        print(json.dumps({"outcome": "cloud_setup_failed", "credentials_printed": False}), flush=True)
        return 1
    finally:
        if provider is not None:
            provider.close()


if __name__ == "__main__":
    raise SystemExit(main())
