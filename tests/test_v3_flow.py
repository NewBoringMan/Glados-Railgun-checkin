import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import v3_runtime as v


def catalog_file(td):
    p=Path(td)/'catalog.json'
    p.write_text(json.dumps({'plans':[{'id':'plan500','points':500,'days':100,'verified':True}]}),encoding='utf-8')
    return p

class FlowClient:
    def __init__(self, before_points=500, after_checkin_points=500, after_exchange_points=0, before_days=10, after_days=110, live=True):
        self.before_points=before_points; self.after_checkin_points=after_checkin_points; self.after_exchange_points=after_exchange_points
        self.before_days=before_days; self.after_days=after_days; self.live=live
        self.points_calls=0; self.status_calls=0; self.checkin_calls=0; self.exchange_calls=[]; self.closed=False
    def _payload(self, points):
        out={'points':points,'history':[]}
        if self.live: out['plans']={'plan500':{'points':500,'days':100}}
        return out
    def points(self):
        self.points_calls += 1
        return self._payload(self.after_checkin_points if self.points_calls == 1 else self.after_exchange_points)
    def status(self):
        self.status_calls += 1
        return {'leftDays': self.before_days if self.status_calls == 1 else self.after_days}
    def checkin(self): self.checkin_calls += 1; return 'success'
    def exchange(self, plan_id): self.exchange_calls.append(plan_id)
    def close(self): self.closed=True

class FlowTests(unittest.TestCase):
    def env(self, catalog, mode='primary', auto='false'):
        return {'GLADOS_COOKIES':'secret','GLADOS_ACCOUNT_KEY':'AAAAAAAAAAAAAAAA','GLADOS_RUN_MODE':mode,
                'GLADOS_AUTO_EXCHANGE':auto,'GLADOS_EXCHANGE_CATALOG':str(catalog)}
    def test_read_only_never_posts(self):
        with tempfile.TemporaryDirectory() as td:
            c=FlowClient(before_points=100)
            with patch.object(v,'_client_for',return_value=(c,{'leftDays':50},c._payload(100))): result=v.execute(self.env(catalog_file(td),mode='read_only',auto='true'))
            self.assertTrue(result['ok']); self.assertEqual(c.checkin_calls,0); self.assertEqual(c.exchange_calls,[])
    def test_verified_exchange_is_checked_after_spend(self):
        with tempfile.TemporaryDirectory() as td:
            c=FlowClient(); c.status_calls=1
            with patch.object(v,'_client_for',return_value=(c,{'leftDays':10},c._payload(500))): result=v.execute(self.env(catalog_file(td),auto='true'))
            self.assertTrue(result['ok']); self.assertEqual(result['exchange'],'success-verified'); self.assertEqual(c.exchange_calls,['plan500'])
    def test_exchange_verification_failure_fails_job(self):
        with tempfile.TemporaryDirectory() as td:
            c=FlowClient(after_exchange_points=500,after_days=10); c.status_calls=1
            with patch.object(v,'_client_for',return_value=(c,{'leftDays':10},c._payload(500))): result=v.execute(self.env(catalog_file(td),auto='true'))
            self.assertFalse(result['ok']); self.assertEqual(result['exchange'],'verification-failed')
    def test_missing_live_plan_metadata_holds_spend(self):
        with tempfile.TemporaryDirectory() as td:
            c=FlowClient(live=False)
            with patch.object(v,'_client_for',return_value=(c,{'leftDays':10},c._payload(999))): result=v.execute(self.env(catalog_file(td),auto='true'))
            self.assertTrue(result['ok']); self.assertEqual(result['exchange'],'held-unverified'); self.assertEqual(c.exchange_calls,[])

if __name__=='__main__': unittest.main()
