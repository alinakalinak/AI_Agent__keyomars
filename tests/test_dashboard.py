"""State-flow tests: live events, approval gate, final save, extra hours confirmation."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from run_web import DashboardState


def plan():
    return {'days': [{'day': i, 'tasks': ([{'id': 'T-1', 'hours': 1}] if i == 1 else [])} for i in range(1,6)], 'deferred': []}


class FakeModel:
    def __init__(self, model, timeout): self.calls=0
    def chat_structured(self, messages, schema):
        self.calls+=1
        return {'content':json.dumps({**plan(), 'commentary':'# برنامه پنج‌روزه\nاول دیتابیس و بعد تست'},ensure_ascii=False)}


class TestDashboard(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.project=Path(self.tmp.name)
        self.project.joinpath('tasks.json').write_text(json.dumps({'tasks':[{'id':'T-1','status':'todo','priority':'high','estimate_hours':1,'depends_on':[]}]}),encoding='utf8')
        self.project.joinpath('project_constraints.json').write_text(json.dumps({'working_days':5,'hours_per_day':2,'max_extra_hours_for_approval':2,'optional_task_ids':[]}),encoding='utf8')
        self.project.joinpath('README.md').write_text('TaskFlow Mini sample',encoding='utf8')
        self.state=DashboardState(client_factory=FakeModel,project=self.project)

    def tearDown(self):
        self.tmp.cleanup()

    def wait_status(self, target, seconds=5):
        for _ in range(int(seconds*100)):
            if self.state.snapshot()['status']==target:
                return True
            time.sleep(.01)
        return False

    def test_approval_and_real_save(self):
        self.assertTrue(self.state.start('qwen3:8b','برنامه بده',600))
        self.assertFalse(self.state.start('qwen3:8b','دوباره',600))
        self.assertTrue(self.wait_status('approval'))
        self.assertFalse((self.project/'output'/'plan.md').exists())
        self.assertEqual(self.state.snapshot()['validation']['verdict'],'pass')
        self.assertTrue(self.state.make_decision(True))
        self.assertTrue(self.wait_status('done'))
        s=self.state.snapshot()
        self.assertTrue(s['saved'])
        self.assertTrue(s['output_exists'])
        self.assertIn('برنامه پنج‌روزه', (self.project/'output'/'plan.md').read_text(encoding='utf8'))
        self.assertEqual(json.loads((self.project/'tasks.json').read_text())['tasks'][0]['status'],'todo')
        self.assertGreater(len(s['logs']),5)

    def test_denial_then_explicit_save(self):
        self.assertTrue(self.state.start('qwen3:8b','برنامه بده',600))
        self.assertTrue(self.wait_status('approval'))
        self.assertTrue(self.state.make_decision(False))
        self.assertTrue(self.wait_status('done'))
        self.assertFalse((self.project/'output'/'plan.md').exists())
        self.assertTrue(self.state.save_final()['ok'])
        self.assertTrue((self.project/'output'/'plan.md').exists())

    def test_idle_cannot_write(self):
        self.assertFalse(self.state.save_final()['ok'])
        self.assertFalse(self.state.make_decision(True))


class ExtraModel:
    def __init__(self, model, timeout): self.calls=0
    def chat_structured(self, messages, schema):
        self.calls+=1
        proposal = {
            'days':[{'day':i,'tasks':([{'id':'T-1','hours':2.5}] if i==1 else [])} for i in range(1,6)],
            'deferred':[], 'commentary':'تأیید اضافه‌کاری لازم است.'
        }
        return {'content':json.dumps(proposal,ensure_ascii=False)}


class TestExtraApproval(unittest.TestCase):
    def setUp(self):
        TestDashboard.setUp(self)
        tasks={'tasks':[{'id':'T-1','status':'todo','priority':'high','estimate_hours':2.5,'depends_on':[]}]}
        self.project.joinpath('tasks.json').write_text(json.dumps(tasks), encoding='utf8')
        self.state=DashboardState(client_factory=ExtraModel,project=self.project)

    tearDown = TestDashboard.tearDown
    wait_status = TestDashboard.wait_status

    def test_overtime_confirmation_required(self):
        self.assertTrue(self.state.start('qwen3:8b','برنامه بده',600))
        self.assertTrue(self.wait_status('approval'))
        self.assertAlmostEqual(self.state.snapshot()['validation']['extra_hours'], .5)
        self.assertFalse(self.state.make_decision(True, False))
        self.assertTrue(self.state.make_decision(True, True))
        self.assertTrue(self.wait_status('done'))
        self.assertTrue(self.state.snapshot()['saved'])
