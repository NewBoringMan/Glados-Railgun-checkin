import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime,timezone,timedelta

from login_refresh_core import RefreshError,RefreshStore
from login_refresh_identity import IdentityRecovery

A='A'*16; B='B'*16
NOW=datetime(2026,9,29,tzinfo=timezone.utc)
OLD=NOW-timedelta(days=50)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.source=self.root/'snapshot.json';self.store=RefreshStore(self.root/'identity.sqlite')
    def tearDown(self):self.store.close();self.temp.cleanup()
    def write(self,rows):
        self.source.write_text(json.dumps(rows));return hashlib.sha256(self.source.read_bytes()).hexdigest()
    def run_apply(self,rows):
        digest=self.write(rows);data,ignored=IdentityRecovery.inspect(self.source,digest,[A,B])
        return IdentityRecovery.apply(self.store,data,source_sha256=digest,snapshot_at=OLD,now=NOW)
    def test_historical_identity_survives_reopen(self):
        self.run_apply([{'ok':True,'account_key':A,'email':'alice@example.com'}]);self.store.close()
        self.store=RefreshStore(self.root/'identity.sqlite')
        self.assertEqual(self.store.display_name(A),'alice@example.com')
    def test_historical_time_not_marked_as_live_verification(self):
        self.run_apply([{'ok':True,'account_key':A,'email':'alice@example.com'}])
        self.assertEqual(self.store.identity(A)['verified_at'],OLD.timestamp())
        self.assertEqual(self.store.db.execute('SELECT source_kind FROM identity_origin').fetchone()[0],'historical_successful_status')
    def test_failed_or_empty_rows_ignored(self):
        d=self.write([{'ok':False,'account_key':A,'email':''},{'ok':True,'account_key':B,'email':''}])
        rows,count=IdentityRecovery.inspect(self.source,d,[A,B]);self.assertEqual(rows,{});self.assertEqual(count,2)
    def test_removed_account_not_recreated(self):
        d=self.write([{'ok':True,'account_key':B,'email':'bob@example.com'}]);rows,count=IdentityRecovery.inspect(self.source,d,[A])
        self.assertEqual(rows,{});self.assertEqual(count,1)
    def test_changed_source_rejected(self):
        self.write([])
        with self.assertRaisesRegex(RefreshError,'identity_source_changed'):IdentityRecovery.inspect(self.source,'0'*64,[A])
    def test_conflicting_source_rejected(self):
        d=self.write([{'ok':True,'account_key':A,'email':'alice@example.com'},{'ok':True,'account_key':A,'email':'wrong@example.com'}])
        with self.assertRaisesRegex(RefreshError,'identity_source_conflict'):IdentityRecovery.inspect(self.source,d,[A,B])
    def test_duplicate_email_rejected(self):
        d=self.write([{'ok':True,'account_key':A,'email':'alice@example.com'},{'ok':True,'account_key':B,'email':'alice@example.com'}])
        with self.assertRaisesRegex(RefreshError,'identity_source_conflict'):IdentityRecovery.inspect(self.source,d,[A,B])
    def test_destination_conflict_rolls_back_whole_batch(self):
        self.store.remember_verified_identity(B,'existing@example.com',NOW)
        with self.assertRaisesRegex(RefreshError,'identity_destination_conflict'):
            self.run_apply([{'ok':True,'account_key':A,'email':'alice@example.com'},{'ok':True,'account_key':B,'email':'wrong@example.com'}])
        self.assertIsNone(self.store.identity(A));self.assertEqual(self.store.identity(B)['email'],'existing@example.com')
    def test_idempotent_repeat_does_not_change_current_identity_time(self):
        self.store.remember_verified_identity(A,'alice@example.com',NOW)
        result=self.run_apply([{'ok':True,'account_key':A,'email':'alice@example.com'}])
        self.assertEqual(result['imported'],0);self.assertEqual(result['already_present'],1)
        self.assertEqual(self.store.identity(A)['verified_at'],NOW.timestamp())
    def test_unknown_identity_not_guessed(self):
        self.run_apply([{'ok':True,'account_key':A,'email':'alice@example.com'}])
        self.assertIsNone(self.store.identity(B));self.assertEqual(self.store.display_name(B),'GLaDOS BBBBBB')
    def test_recovery_does_not_create_login_jobs(self):
        self.run_apply([{'ok':True,'account_key':A,'email':'alice@example.com'}])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)
    def test_future_source_rejected(self):
        with self.assertRaisesRegex(RefreshError,'future_identity_snapshot'):
            IdentityRecovery.apply(self.store,{A:'alice@example.com'},source_sha256='0'*64,snapshot_at=NOW+timedelta(days=1),now=NOW)


if __name__=='__main__':unittest.main()
