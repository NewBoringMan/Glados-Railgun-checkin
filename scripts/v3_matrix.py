#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path


def load_config(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
        raise SystemExit('accounts config must be an object')
    return raw


def build_matrix(config: dict, account_filter: str = 'all') -> dict:
    if bool(config.get('globalPaused', False)):
        return {'include': []}
    accounts = config.get('accounts') or {}
    if not isinstance(accounts, dict):
        raise SystemExit('accounts must be an object')
    include = []
    for key, item in sorted(accounts.items()):
        if account_filter not in ('', 'all') and key != account_filter:
            continue
        if not isinstance(item, dict):
            continue
        if not item.get('enabled', True) or item.get('archived', False):
            continue
        if not isinstance(key, str) or len(key) != 16 or any(ch not in '0123456789ABCDEF' for ch in key):
            continue
        include.append({'slot': len(include) + 1, 'account_key': key, 'secret_name': f'GLADOS_ACCOUNT_{key}', 'auto_exchange': bool(item.get('autoExchange', False))})
    return {'include': include}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='.github/glados/accounts.v3.json')
    parser.add_argument('--account', default='all')
    args = parser.parse_args()
    print(json.dumps(build_matrix(load_config(Path(args.config)), args.account), separators=(',', ':')))

if __name__ == '__main__':
    main()
