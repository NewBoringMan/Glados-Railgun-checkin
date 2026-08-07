import json
import unittest
from scripts.v3_cutover import arm_config, retire_v2_schedule

class CutoverTests(unittest.TestCase):
    def test_arms_v3_config(self):
        src=json.dumps({'version':3,'productionScheduleEnabled':False,'accounts':{}})
        out=json.loads(arm_config(src))
        self.assertTrue(out['productionScheduleEnabled'])
    def test_refuses_double_arm(self):
        with self.assertRaises(ValueError): arm_config(json.dumps({'version':3,'productionScheduleEnabled':True}))
    def test_retires_v2_schedule_but_keeps_manual_fallback(self):
        src='''name: GLaDOS Multi\non:\n  workflow_dispatch:\n  schedule:\n    - cron: '7 5 * * *'\n      timezone: 'Asia/Taipei'\n\npermissions:\n  contents: read\njobs:\n  x:\n    runs-on: ubuntu-latest\n'''
        out=retire_v2_schedule(src)
        self.assertIn('  workflow_dispatch:',out)
        self.assertNotIn('  schedule:',out)
        self.assertIn('permissions:',out)
        self.assertIn('jobs:',out)
    def test_refuses_missing_schedule(self):
        with self.assertRaises(ValueError): retire_v2_schedule('name: x\non:\n  workflow_dispatch:\n')

if __name__=='__main__': unittest.main()
