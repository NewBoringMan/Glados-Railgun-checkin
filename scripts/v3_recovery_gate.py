#!/usr/bin/env python3
"""Determine whether the afternoon recovery slot can be skipped.

The decision is based on GitHub's own morning workflow result, not on inferred
point mutations. This helper is intentionally testable with plain JSON fixtures.
"""
from __future__ import annotations
import argparse, json, subprocess
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

TAIPEI=ZoneInfo('Asia/Taipei')

def find_morning_run(rows:list[dict[str,Any]], current_run:int, now:datetime)->int|None:
    for row in rows:
        try: rid=int(row.get('databaseId',0))
        except (TypeError,ValueError): continue
        if rid==current_run or row.get('status')!='completed': continue
        text=str(row.get('createdAt',''))
        try: created=datetime.fromisoformat(text.replace('Z','+00:00')).astimezone(TAIPEI)
        except ValueError: continue
        if created.date()==now.astimezone(TAIPEI).date() and created.hour<12: return rid
    return None

def slot_succeeded(jobs:list[dict[str,Any]], slot:int)->bool:
    target=f'GLaDOS check-in slot {slot}'
    return any(j.get('name')==target and j.get('conclusion')=='success' for j in jobs)

def gh_json(args:list[str])->Any:
    return json.loads(subprocess.check_output(args,text=True))

def main()->None:
    p=argparse.ArgumentParser(); p.add_argument('--repo',required=True); p.add_argument('--current-run',type=int,required=True); p.add_argument('--slot',type=int,required=True); a=p.parse_args()
    now=datetime.now(TAIPEI)
    try:
        rows=gh_json(['gh','run','list','--repo',a.repo,'--workflow','v3Checkin.yml','--event','schedule','--limit','12','--json','databaseId,createdAt,status'])
        rid=find_morning_run(rows,a.current_run,now)
        if not rid: print('false'); return
        view=gh_json(['gh','run','view',str(rid),'--repo',a.repo,'--json','jobs'])
        print('true' if slot_succeeded(view.get('jobs',[]),a.slot) else 'false')
    except Exception:
        # Fail open to recovery: uncertainty must not suppress a needed safety check.
        print('false')

if __name__=='__main__': main()
