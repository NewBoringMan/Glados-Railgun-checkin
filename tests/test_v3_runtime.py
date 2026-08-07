import json
import tempfile
import unittest
from pathlib import Path

import v3_runtime as v

class FakeResponse:
    def __init__(self,status,payload,headers=None): self.status_code=status; self._payload=payload; self.text=json.dumps(payload); self.headers=headers or {}
    def json(self): return self._payload
class FakeSession:
    def __init__(self,responses): self.responses=list(responses); self.calls=[]
    def request(self,method,url,**kwargs): self.calls.append((method,url,kwargs.get('json'))); return self.responses.pop(0)
    def close(self): pass

class RuntimeTests(unittest.TestCase):
    def test_best_plan_and_live_intersection(self):
        trusted=[v.Plan('plan100',100,10),v.Plan('plan500',500,100),v.Plan('plan800',800,200)]
        live=[v.Plan('plan100',100,10,False),v.Plan('plan500',500,100,False),v.Plan('plan800',800,200,False)]
        self.assertEqual(v.best_plan(v.trusted_live_intersection(trusted,live)).plan_id,'plan800')
    def test_tie_prefers_shorter_duration(self): self.assertEqual(v.best_plan([v.Plan('plan500',500,100),v.Plan('plan900',900,180)]).plan_id,'plan500')
    def test_live_plan_mismatch_is_excluded(self): self.assertEqual(v.trusted_live_intersection([v.Plan('plan500',500,100)],[v.Plan('plan500',500,90,False)]),[])
    def test_parse_live_plans_dict(self):
        plans=v.parse_live_plans({'plans':{'plan500':{'points':500,'days':100}}}); self.assertEqual([(p.plan_id,p.points,p.days) for p in plans],[('plan500',500,100)])
    def test_reliable_history_requires_explicit_marker(self):
        now=int(v.datetime.now(v.TAIPEI).timestamp()*1000)
        self.assertFalse(v.reliable_today_checkin({'history':[{'time':now,'change':5}]}))
        self.assertTrue(v.reliable_today_checkin({'history':[{'time':now,'change':5,'reason':'checkin'}]}))
        self.assertFalse(v.reliable_today_checkin({'history':[{'time':now,'change':100,'reason':'invite reward'}]}))
    def test_client_post_is_not_retried(self):
        s=FakeSession([FakeResponse(500,{'x':1})]); c=v.Client('glados.cloud','secret',s)
        with self.assertRaises(v.NetworkError): c.checkin()
        self.assertEqual(len(s.calls),1)
    def test_runtime_and_workflows_do_not_expose_persistent_keys(self):
        runtime=Path('v3_runtime.py').read_text(encoding='utf-8')
        combined=runtime+Path('.github/workflows/v3Checkin.yml').read_text()+Path('.github/workflows/v3Canary.yml').read_text()
        for token in ['points_total','result.email','cookie_header','print(cookie','print(payload','koa:sess=','GLADOS_ACCOUNT_KEY']:
            self.assertNotIn(token,combined)
        matrix_source=Path('scripts/v3_matrix.py').read_text(encoding='utf-8')
        self.assertNotIn("'account_key':",matrix_source)
    def test_catalog_requires_verified(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'x.json'; p.write_text('{"plans":[{"id":"plan1","points":1,"days":100,"verified":false},{"id":"plan500","points":500,"days":100,"verified":true}]}')
            self.assertEqual([x.plan_id for x in v.load_catalog(p)],['plan500'])

if __name__=='__main__': unittest.main()
