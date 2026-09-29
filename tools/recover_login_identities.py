"""Explicit private-state migration for Account Center; no emails printed or uploaded."""
import argparse
import base64
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from login_refresh_core import RefreshError,RefreshStore
from login_refresh_identity import IdentityRecovery

REPO='NewBoringMan/Glados-Railgun-checkin'
SUPPORT=Path.home()/'Library/Application Support/GLaDOS Account Center'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--snapshot',required=True)
    parser.add_argument('--sha256',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    source=Path(args.snapshot)
    metadata=json.loads(Path(str(source)+'.meta.json').read_text())
    if metadata.get('original')!=str(SUPPORT/'status-cache.json'):
        raise RefreshError('identity_snapshot_origin_mismatch')
    snapshot_at=datetime.fromisoformat(metadata['createdAt'].replace('Z','+00:00'))
    response=subprocess.run(['/opt/homebrew/bin/gh','api',f'repos/{REPO}/contents/.github/glados/accounts.json?ref=master'],
                            capture_output=True,check=False,timeout=25)
    if response.returncode:raise RefreshError('github_registry_unavailable')
    envelope=json.loads(response.stdout);registry=json.loads(base64.b64decode(envelope['content']))['accounts']
    if not isinstance(registry,dict) or not registry:raise RefreshError('invalid_account_registry')
    rows,ignored=IdentityRecovery.inspect(source,args.sha256,registry)
    cache=SUPPORT/'status-cache.json'
    before=hashlib.sha256(cache.read_bytes()).hexdigest()
    result={'registered_accounts':len(registry),'recoverable_identities':len(rows),'ignored_rows':ignored,
            'missing_identities':len(registry)-len(rows),'applied':False}
    if args.apply:
        if not SUPPORT.is_dir():raise RefreshError('existing_app_data_required')
        store=RefreshStore(SUPPORT/'login-refresh.sqlite')
        try:
            result.update(IdentityRecovery.apply(store,rows,source_sha256=args.sha256,snapshot_at=snapshot_at,now=datetime.now(timezone.utc)))
            actual=store.db.execute('SELECT COUNT(*) FROM identity').fetchone()[0]
            result.update(applied=True,stored_identities=actual,private_file_mode=oct((SUPPORT/'login-refresh.sqlite').stat().st_mode&0o777))
        finally:store.close()
    result['status_cache_unchanged']=before==hashlib.sha256(cache.read_bytes()).hexdigest()
    result['credential_reads']=0
    result['remote_writes']=0
    print(json.dumps(result))
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except RefreshError as exc:
        print(json.dumps({'status':'BLOCKED','reason':str(exc)}));raise SystemExit(1)
    except Exception as exc:
        print(json.dumps({'status':'BLOCKED','reason':'migration_failed','error_type':type(exc).__name__}));raise SystemExit(1)
