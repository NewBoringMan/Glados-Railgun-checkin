import json
import pathlib
import unittest
from scripts.v3_matrix import build_matrix, lock_id

class MatrixTests(unittest.TestCase):
    def test_filters_and_pauses(self):
        config={'globalPaused':False,'productionScheduleEnabled':False,'accounts':{
            'AAAAAAAAAAAAAAAA':{'enabled':True,'autoExchange':True},
            'BBBBBBBBBBBBBBBB':{'enabled':False},
            'CCCCCCCCCCCCCCCC':{'enabled':True,'archived':True}}}
        matrix=build_matrix(config); row=matrix['include'][0]
        self.assertEqual(row['slot'],1); self.assertEqual(row['secret_name'],'GLADOS_ACCOUNT_AAAAAAAAAAAAAAAA')
        self.assertEqual(row['lock_id'],lock_id('AAAAAAAAAAAAAAAA')); self.assertTrue(row['auto_exchange']); self.assertNotIn('account_key',row)
        config['globalPaused']=True; self.assertEqual(build_matrix(config),{'include':[]})
    def test_scheduled_execution_requires_explicit_arm(self):
        config={'productionScheduleEnabled':False,'accounts':{'AAAAAAAAAAAAAAAA':{'enabled':True}}}
        self.assertEqual(build_matrix(config,scheduled=True),{'include':[]})
        self.assertEqual(len(build_matrix(config)['include']),1)
        config['productionScheduleEnabled']=True
        self.assertEqual(len(build_matrix(config,scheduled=True)['include']),1)
    def test_anonymous_lock_filter(self):
        config={'accounts':{'AAAAAAAAAAAAAAAA':{'enabled':True},'BBBBBBBBBBBBBBBB':{'enabled':True}}}
        wanted=lock_id('BBBBBBBBBBBBBBBB')
        rows=build_matrix(config,wanted)['include']
        self.assertEqual(len(rows),1); self.assertEqual(rows[0]['lock_id'],wanted); self.assertEqual(rows[0]['slot'],2)
    def test_invalid_key_is_ignored(self): self.assertEqual(build_matrix({'accounts':{'bad':{'enabled':True}}}),{'include':[]})
    def test_lock_id_is_stable_and_non_revealing(self):
        value=lock_id('AAAAAAAAAAAAAAAA'); self.assertEqual(len(value),16); self.assertNotIn('AAAAAAAA',value.upper())
    def test_real_config_has_no_credentials_and_five_accounts(self):
        raw=pathlib.Path('.github/glados/accounts.v3.json').read_text(encoding='utf-8')
        self.assertNotIn('koa:sess',raw); self.assertNotIn('cookie',raw.lower())
        cfg=json.loads(raw); matrix=build_matrix(cfg)
        self.assertEqual(len(cfg['accounts']),5); self.assertEqual(len(matrix['include']),5); self.assertTrue(all('account_key' not in x for x in matrix['include']))
        self.assertFalse(cfg['productionScheduleEnabled']); self.assertEqual(build_matrix(cfg,scheduled=True),{'include':[]})

if __name__=='__main__': unittest.main()
