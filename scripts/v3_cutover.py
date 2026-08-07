#!/usr/bin/env python3
"""Prepare the overlap-free V3 production cutover.

Pure file transformer used by the manual cutover workflow and unit tests. It:
1. arms `productionScheduleEnabled` in accounts.v3.json;
2. removes only the top-level `on.schedule` block from the V2 multi-account workflow;
3. preserves V2 `workflow_dispatch` as an emergency manual fallback.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path


def arm_config(text: str) -> str:
    data = json.loads(text)
    if not isinstance(data, dict) or data.get('version') != 3:
        raise ValueError('not a V3 account config')
    if data.get('productionScheduleEnabled') is True:
        raise ValueError('V3 production schedule is already armed')
    data['productionScheduleEnabled'] = True
    return json.dumps(data, ensure_ascii=False, indent=2) + '\n'


def retire_v2_schedule(text: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    removed = False
    i = 0
    while i < len(lines):
        if lines[i] == '  schedule:':
            removed = True
            i += 1
            while i < len(lines) and (lines[i].startswith('    ') or not lines[i].strip()):
                i += 1
            continue
        output.append(lines[i])
        i += 1
    result = '\n'.join(output).rstrip() + '\n'
    if not removed:
        raise ValueError('V2 schedule block was not found')
    if '  workflow_dispatch:' not in result:
        raise ValueError('V2 manual fallback was unexpectedly removed')
    if '\n  schedule:' in result:
        raise ValueError('V2 schedule remains after transformation')
    return result


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--accounts', default='.github/glados/accounts.v3.json')
    p.add_argument('--v2-workflow', default='.github/workflows/gladosAccounts.yml')
    a = p.parse_args()
    accounts = Path(a.accounts); v2 = Path(a.v2_workflow)
    next_accounts = arm_config(accounts.read_text(encoding='utf-8'))
    next_v2 = retire_v2_schedule(v2.read_text(encoding='utf-8'))
    accounts.write_text(next_accounts, encoding='utf-8')
    v2.write_text(next_v2, encoding='utf-8')
    print('CUTOVER_FILES_READY')

if __name__ == '__main__':
    main()
