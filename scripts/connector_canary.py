"""Canary check for the bundled connectors.

Runs each connector against its live provider API to catch API drift early.
Credentials come from the environment; a connector whose credentials are not
set is SKIPPED, not failed, so the check only goes red when a connector that
you have configured actually breaks. That red is the drift signal: the CI job
opens an alert, a human ships the fix, and the release pipeline publishes a new
version.

Env per connector (all optional; set the ones you want watched):
  Slack       SLACK_TOKEN, SLACK_CHANNELS
  GitHub      GH_CANARY_TOKEN, GITHUB_REPOS
  Drive       DRIVE_SA_JSON (JSON content or a path), DRIVE_ROOT
  Jira        JIRA_SITE, JIRA_EMAIL, JIRA_TOKEN, JIRA_PROJECTS
  Salesforce  SF_INSTANCE, SF_TOKEN, SF_OBJECTS
  Notion      NOTION_TOKEN, NOTION_DATABASES

Usage:
  python scripts/connector_canary.py                 # check, print a table
  python scripts/connector_canary.py --update-manifest  # also stamp last_verified
Exit code is non-zero if any CONFIGURED connector failed.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import tempfile

MANIFEST = pathlib.Path(__file__).parent.parent / "aggrete" / "connectors" / "connectors.json"


def _first(env: str) -> str:
    return (os.environ.get(env, "") or "").split(",")[0].strip()


def check_slack() -> str:
    from aggrete.connectors.slack import Slack
    chans = Slack(os.environ["SLACK_TOKEN"]).channels()
    return f"{len(chans)} channels visible"


def check_github() -> str:
    from aggrete.connectors.github import GitHub
    repo = _first("GITHUB_REPOS")
    ok = GitHub(os.environ["GH_CANARY_TOKEN"]).repo_exists(repo)
    if not ok:
        raise RuntimeError(f"repo {repo!r} not visible to token")
    return f"repo {repo} reachable"


def check_drive() -> str:
    from aggrete.connectors.drive import Drive
    raw = os.environ["DRIVE_SA_JSON"]
    if raw.lstrip().startswith("{"):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        f.write(raw); f.close(); path = f.name
    else:
        path = raw
    d = Drive(path)
    d.token()  # proves the service-account key authenticates
    root = os.environ.get("DRIVE_ROOT", "")
    if root and not d.folder_by_name(root):
        raise RuntimeError(f"root folder {root!r} not shared with the service account")
    return "service account authenticates" + (f"; root {root} found" if root else "")


def check_jira() -> str:
    from aggrete.connectors.jira import Jira
    j = Jira(os.environ["JIRA_SITE"], os.environ["JIRA_EMAIL"], os.environ["JIRA_TOKEN"])
    key = _first("JIRA_PROJECTS")
    if not j.project_exists(key):
        raise RuntimeError(f"project {key!r} not visible")
    return f"project {key} reachable"


def check_salesforce() -> str:
    from aggrete.connectors.salesforce import Salesforce
    sf = Salesforce(os.environ["SF_INSTANCE"], os.environ["SF_TOKEN"])
    obj = _first("SF_OBJECTS")
    if not sf.object_exists(obj):
        raise RuntimeError(f"object {obj!r} not describable")
    return f"object {obj} reachable"


def check_notion() -> str:
    from aggrete.connectors.notion import Notion
    n = Notion(os.environ["NOTION_TOKEN"])
    db = _first("NOTION_DATABASES")
    if n.db_title(db) is None:
        raise RuntimeError(f"database {db!r} not visible to the integration")
    return f"database {db} reachable"


# id -> (required env vars, check callable)
CHECKS = {
    "slack": (["SLACK_TOKEN", "SLACK_CHANNELS"], check_slack),
    "github": (["GH_CANARY_TOKEN", "GITHUB_REPOS"], check_github),
    "drive": (["DRIVE_SA_JSON"], check_drive),
    "jira": (["JIRA_SITE", "JIRA_EMAIL", "JIRA_TOKEN", "JIRA_PROJECTS"], check_jira),
    "salesforce": (["SF_INSTANCE", "SF_TOKEN", "SF_OBJECTS"], check_salesforce),
    "notion": (["NOTION_TOKEN", "NOTION_DATABASES"], check_notion),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update-manifest", action="store_true",
                    help="stamp last_verified on connectors that passed")
    args = ap.parse_args()

    today = datetime.date.today().isoformat()
    results, failed = [], []
    for cid, (env_keys, fn) in CHECKS.items():
        if not all(os.environ.get(k) for k in env_keys):
            results.append((cid, "skip", "no credentials set"))
            continue
        try:
            detail = fn()
            results.append((cid, "ok", detail))
        except Exception as e:  # noqa: BLE001 - a canary reports any failure
            results.append((cid, "FAIL", f"{type(e).__name__}: {e}"))
            failed.append(cid)

    width = max(len(c) for c, _, _ in results)
    print(f"connector canary  {today}")
    for cid, status, detail in results:
        print(f"  {cid.ljust(width)}  {status:4}  {detail}")

    if args.update_manifest:
        data = json.loads(MANIFEST.read_text())
        passed = {c for c, s, _ in results if s == "ok"}
        for c in data["connectors"]:
            if c["id"] in passed:
                c["last_verified"] = today
        MANIFEST.write_text(json.dumps(data, indent=2) + "\n")
        print(f"stamped last_verified={today} on: {', '.join(sorted(passed)) or 'none'}")

    if failed:
        print(f"\nDRIFT: {', '.join(failed)} failed. A configured connector broke.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
