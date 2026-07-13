#!/usr/bin/env python3
"""Create Toggl Track time entries from a JSON file (output of report-to-json.py).

Usage:
  TOGGL_API_TOKEN=xxx ./toggl-fill.py entries.json [--dry-run]

Skips entries that already exist in Toggl (same start + description), so it is
safe to re-run.
"""
import base64
import json
import os
import sys
import time
import urllib.request

API = "https://api.track.toggl.com/api/v9"
PROJECT_NAME = "0200_A0_CalculatorCo2"


def call(method, path, body=None):
    token = os.environ["TOGGL_API_TOKEN"]
    auth = base64.b64encode(f"{token}:api_token".encode()).decode()
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
    )
    while True:
        try:
            with urllib.request.urlopen(req) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code not in (402, 429):
                sys.exit(f"{method} {path} -> {e.code}: {e.read().decode()}")
            print("rate limit hit, waiting 15 min...", file=sys.stderr)
            time.sleep(900)  # ponytail: sliding 60-min quota window, blunt wait is fine


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]
    if len(args) != 1:
        sys.exit("usage: toggl-fill.py entries.json [--dry-run]")
    entries = json.load(open(args[0]))
    if not entries:
        sys.exit("no entries in file")

    if dry_run:
        for e in entries:
            print(f"would create: {e['start']}  {e['duration']:>5}s  {e['tags']}  {e['description']}")
        print(f"\n{len(entries)} entries (dry run, nothing sent)")
        return

    if "TOGGL_API_TOKEN" not in os.environ:
        sys.exit("set TOGGL_API_TOKEN")

    me = call("GET", "/me")
    wid = me["default_workspace_id"]
    projects = call("GET", f"/workspaces/{wid}/projects")
    project = next((p for p in projects if p["name"] == PROJECT_NAME), None)
    if not project:
        sys.exit(f"project {PROJECT_NAME!r} not found in workspace {wid}")

    dates = sorted(e["start"][:10] for e in entries)
    existing = call("GET", f"/me/time_entries?start_date={dates[0]}&end_date={dates[-1]}T23:59:59Z")
    seen = {(x["start"], x.get("description", "")) for x in existing}

    created = skipped = 0
    for e in entries:
        if (e["start"], e["description"]) in seen:
            skipped += 1
            continue
        call("POST", f"/workspaces/{wid}/time_entries", {
            "created_with": "commit-hours-fill",
            "workspace_id": wid,
            "project_id": project["id"],
            "start": e["start"],
            "duration": e["duration"],
            "description": e["description"],
            "tags": e["tags"],
        })
        created += 1
        print(f"created: {e['start']}  {e['description']}")
        time.sleep(1)  # ponytail: toggl rate limit ~1 req/s

    print(f"\n{created} created, {skipped} already existed")


if __name__ == "__main__":
    main()
