#!/usr/bin/env python3
"""Decide whether a V3 Recovery account task can be skipped safely.

Scheduled Recovery is suppressed only when the same privacy-safe account lock had a
successful *scheduled morning primary* job today. Manual Release-Candidate Recovery
is suppressed only when the same lock had a successful *manual primary* job today.
The helper never relies on inferred point mutations.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")


def candidate_run_ids(
    rows: list[dict[str, Any]],
    current_run: int,
    now: datetime,
    *,
    morning_only: bool,
) -> list[int]:
    today = now.astimezone(TAIPEI).date()
    result: list[int] = []
    for row in rows:
        try:
            run_id = int(row.get("databaseId", 0))
        except (TypeError, ValueError):
            continue
        if run_id == current_run or row.get("status") != "completed":
            continue
        try:
            created = datetime.fromisoformat(str(row.get("createdAt", "")).replace("Z", "+00:00")).astimezone(TAIPEI)
        except ValueError:
            continue
        if created.date() != today:
            continue
        if morning_only and created.hour >= 12:
            continue
        result.append(run_id)
    return result


def primary_job_succeeded(jobs: Iterable[dict[str, Any]], slot: int, lock_id: str) -> bool:
    target = f"GLaDOS primary slot {slot} · {lock_id}"
    return any(job.get("name") == target and job.get("conclusion") == "success" for job in jobs)


def gh_json(args: list[str]) -> Any:
    return json.loads(subprocess.check_output(args, text=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--current-run", type=int, required=True)
    parser.add_argument("--slot", type=int, required=True)
    parser.add_argument("--lock-id", required=True)
    parser.add_argument("--event-name", choices=["schedule", "workflow_dispatch"], required=True)
    args = parser.parse_args()

    now = datetime.now(TAIPEI)
    event = "schedule" if args.event_name == "schedule" else "workflow_dispatch"
    try:
        rows = gh_json([
            "gh", "run", "list", "--repo", args.repo, "--workflow", "v3Checkin.yml",
            "--event", event, "--limit", "24", "--json", "databaseId,createdAt,status",
        ])
        run_ids = candidate_run_ids(
            rows,
            args.current_run,
            now,
            morning_only=(event == "schedule"),
        )
        for run_id in run_ids:
            view = gh_json(["gh", "run", "view", str(run_id), "--repo", args.repo, "--json", "jobs"])
            if primary_job_succeeded(view.get("jobs", []), args.slot, args.lock_id):
                print("true")
                return
        print("false")
    except Exception:
        # Uncertainty must not silently suppress a needed Recovery check. The
        # runtime still has its conservative explicit-history safeguard.
        print("false")


if __name__ == "__main__":
    main()
