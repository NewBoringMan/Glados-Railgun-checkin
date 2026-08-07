import json
import pathlib
import unittest
from scripts.v3_matrix import build_matrix

class MatrixTests(unittest.TestCase):
    def test_filters_and_pauses(self):
        config={'globalPaused':False,'accounts':{
            'AAAAAAAAAAAAAAAA':{'enabled':True,'autoExchange':True},
            'BBBBBBBBBBBBBBBB':{'enabled':False},
            'CCCCCCCCCCCCCCCC':{'enabled':True,'archived':True}}}
        matrix=build_matrix(config)
        self.assertEqual(matrix,{'include':[{'slot':1,'secret_name':'GLADOS_ACCOUNT_AAAAAAAAAAAAAAAA','auto_exchange':True}]})
        self.assertNotIn('account_key',matrix['include'][0])
        config['globalPaused']=True
        self.assertEqual(build_matrix(config),{'include':[]})
    def test_invalid_key_is_ignored(self):
        self.assertEqual(build_matrix({'accounts':{'bad':{'enabled':True}}}),{'include':[]})
    def test_real_config_has_no_credentials_and_five_accounts(self):
        raw=pathlib.Path('.github/glados/accounts.v3.json').read_text(encoding='utf-8')
        self.assertNotIn('koa:sess',raw); self.assertNotIn('cookie',raw.lower())
        cfg=json.loads(raw); matrix=build_matrix(cfg)
        self.assertEqual(len(cfg['accounts']),5); self.assertEqual(len(matrix['include']),5)
        self.assertTrue(all('account_key' not in x for x in matrix['include']))

if __name__=='__main__': unittest.main()
