#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path


def load_config(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(raw, dict): raise SystemExit('accounts config must be an object')
    return raw


def lock_id(key: str) -> str:
    return hashlib.sha256(('glados-exchange-lock:' + key).encode()).hexdigest()[:16]


def build_matrix(config: dict, account_filter: str = 'all') -> dict:
    """Build privacy-minimized matrix rows with a non-reversible exchange-lock id."""
    if bool(config.get('globalPaused', False)): return {'include': []}
    accounts = config.get('accounts') or {}
    if not isinstance(accounts, dict): raise SystemExit('accounts must be an object')
    include = []
    for key, item in sorted(accounts.items()):
        if account_filter not in ('', 'all') and key != account_filter: continue
        if not isinstance(item, dict) or not item.get('enabled', True) or item.get('archived', False): continue
        if not isinstance(key, str) or len(key) != 16 or any(ch not in '0123456789ABCDEF' for ch in key): continue
        include.append({
            'slot': len(include) + 1,
            'secret_name': f'GLADOS_ACCOUNT_{key}',
            'lock_id': lock_id(key),
            'auto_exchange': bool(item.get('autoExchange', False)),
        })
    return {'include': include}


def main() -> None:
    p = argparse.ArgumentParser(); p.add_argument('--config', default='.github/glados/accounts.v3.json'); p.add_argument('--account', default='all'); a=p.parse_args()
    print(json.dumps(build_matrix(load_config(Path(a.config)), a.account), separators=(',', ':')))

if __name__ == '__main__': main()
