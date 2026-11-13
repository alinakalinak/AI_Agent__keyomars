"""Tests for a model that tries to finish without mandatory tool calling."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from run_web import DashboardState


class PrematureThenTools:
    def __init__(self, model, timeout): self.calls = 0
    def chat_structured(self, messages, schema):
        self.calls += 1
        if self.calls == 1:
            return {'content': 'کار را انجام دادم؛ برنامه آماده است'}
        valid = {'days':[{'day':i, 'tasks':([{'id':'T-1','hours':1}] if i==1 else [])} for i in range(1,6)],
                 'deferred':[], 'commentary':'برنامه پس از اعتبارسنجی'}
        return {'content':json.dumps(valid,ensure_ascii=False)}

class ModelAlwaysFinal:
    def __init__(self, model, timeout): pass
    def chat_structured(self, messages, schema):
        return {'content': 'همه چیز تمام شد'}

class MandatoryFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        self.project.joinpath('tasks.json').write_text(json.dumps({"tasks": [{"id": "T-1", "status": "todo", "estimate_hours": 1, "priority": "high", "depends_on": []}]}),encoding='utf8')
        self.project.joinpath('project_constraints.json').write_text(json.dumps({"working_days": 5, "hours_per_day": 2, "max_extra_hours_for_approval": 2, "optional_task_ids": []}),encoding='utf8')
    def tearDown(self): self.tmp.cleanup()
    def wait(self, state, status):
        for _ in range(300):
            if state.snapshot()["status"] == status: return True
            time.sleep(.01)
        return False
    def test_premature_finishing_repaired(self):
        state = DashboardState(client_factory=PrematureThenTools, project=self.project)
        self.assertTrue(state.start("qwen3:8b", "برنامه بده", 600))
        self.assertTrue(self.wait(state,"approval"))
        self.assertEqual(state.snapshot()["validation"]["verdict"], "pass")
        self.assertFalse((self.project/'output'/'plan.md').exists())
        self.assertTrue(state.make_decision(True))
        self.assertTrue(self.wait(state,'done'))
        self.assertTrue((self.project/'output'/'plan.md').exists())
        self.assertTrue(any('تلاش دوباره' in x['message'] for x in state.snapshot()['logs']))
    def test_premature_finishing_not_accepted_silently(self):
        state=DashboardState(client_factory=ModelAlwaysFinal, project=self.project)
        self.assertTrue(state.start("qwen3:8b", "برنامه بده", 600))
        self.assertTrue(self.wait(state, 'approval'))
        self.assertEqual(state.snapshot()['validation']['verdict'], 'pass')
        self.assertIn('Python',state.snapshot()['plan_origin'])
        self.assertFalse((self.project/'output'/'plan.md').exists())
        self.assertTrue(state.make_decision(False))
        self.assertTrue(self.wait(state, 'done'))

if __name__ == '__main__': unittest.main()
