"""Regression tests for Python-owned validation, JSON repair, fallback and approval."""
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from run_web import DashboardState
from keyomars_agent.scheduler import build_fallback_schedule, PlanningError
from keyomars_agent.validator import validate_schedule
from keyomars_agent.ollama_client import OllamaClient

SAMPLE = Path(__file__).resolve().parents[1] / 'sample_project'

class ModelValid:
    def __init__(self, model, timeout): self.calls = 0
    def chat_structured(self, messages, schema):
        self.calls += 1
        plan = build_fallback_schedule(SAMPLE)
        return {'content':json.dumps({**plan, 'commentary':'ریسک اصلی: نیم‌ساعت اضافه‌کاری. ابتدا دیتابیس و سپس API و تست.'},ensure_ascii=False)}

class ModelNeverJSON:
    def __init__(self, model, timeout): self.calls = 0
    def chat_structured(self, messages, schema):
        self.calls += 1
        return {'content':'من پاسخ می‌دهم ولی برنامه ساختاریافته نمی‌دهم.'}

class ModelBadThenGood:
    def __init__(self, model, timeout): self.calls = 0
    def chat_structured(self, messages, schema):
        self.calls += 1
        proposal = build_fallback_schedule(SAMPLE)
        if self.calls == 1:
            proposal['days'][0]['tasks'][0]['id'] = 'TF-08'  # violate prerequisites and omit TF-01
        return {'content':json.dumps({**proposal,'commentary':'برنامه بعد از بررسی اصلاح شد.'},ensure_ascii=False)}

class ModelTimeout:
    def __init__(self, model, timeout): self.calls = 0
    def chat_structured(self, messages, schema):
        self.calls += 1
        raise RuntimeError('زمان پاسخ Ollama بیشتر از 600 ثانیه شد.')

class WorkflowIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        for name in ['tasks.json', 'project_constraints.json', 'README.md', 'notes.md', 'app_stub.py']:
            shutil.copy2(SAMPLE/name, self.project/name)
    def tearDown(self):
        self.tmp.cleanup()
    def until(self, state, value):
        for _ in range(400):
            if state.snapshot()['status'] == value: return True
            time.sleep(.01)
        return False

    def test_valid_json_python_validation_and_human_extra_approval(self):
        state = DashboardState(client_factory=ModelValid, project=self.project)
        self.assertTrue(state.start('qwen3:8b', 'تحلیل کن', 600))
        self.assertTrue(self.until(state, 'approval'))
        v = state.snapshot()['validation']
        self.assertEqual(v['verdict'], 'needs_approval')
        self.assertAlmostEqual(v['extra_hours'], .5)
        self.assertIn('AI', state.snapshot()['plan_origin'])
        self.assertFalse((self.project/'output/plan.md').exists())
        self.assertFalse(state.make_decision(True, False))
        self.assertTrue(state.make_decision(True, True))
        self.assertTrue(self.until(state, 'done'))
        self.assertTrue((self.project/'output/plan.md').exists())
        self.assertEqual(json.loads((self.project/'tasks.json').read_text())['tasks'][0]['status'], 'todo')
        self.assertIn('TF-07', (self.project/'output/plan.md').read_text())

    def test_model_invalid_json_uses_labelled_deterministic_fallback(self):
        state = DashboardState(client_factory=ModelNeverJSON, project=self.project)
        self.assertTrue(state.start('qwen3:8b', 'تحلیل کن', 600))
        self.assertTrue(self.until(state, 'approval'))
        self.assertIn('Python', state.snapshot()['plan_origin'])
        self.assertEqual(state.snapshot()['validation']['verdict'], 'needs_approval')
        self.assertFalse((self.project/'output/plan.md').exists())
        self.assertTrue(state.make_decision(False))
        self.assertTrue(self.until(state, 'done'))
        self.assertFalse((self.project/'output/plan.md').exists())
        self.assertIn('ذخیره با تأیید انجام نشد', state.snapshot()['final'])

    def test_bad_json_plan_is_repaired_then_validated(self):
        state = DashboardState(client_factory=ModelBadThenGood, project=self.project)
        self.assertTrue(state.start('qwen3:8b', 'تحلیل کن', 600))
        self.assertTrue(self.until(state, 'approval'))
        self.assertIn('AI', state.snapshot()['plan_origin'])
        self.assertEqual(state.snapshot()['validation']['verdict'], 'needs_approval')
        self.assertTrue(any('اعتبارسنجی برنامه — رد شد' in x['message'] for x in state.snapshot()['logs']))
        self.assertTrue(state.make_decision(False))
        self.assertTrue(self.until(state, 'done'))

    def test_timeout_fallback_is_safe_and_marked(self):
        state = DashboardState(client_factory=ModelTimeout, project=self.project)
        self.assertTrue(state.start('qwen3:8b', 'تحلیل کن', 600))
        self.assertTrue(self.until(state, 'approval'))
        self.assertIn('Python',state.snapshot()['plan_origin'])
        self.assertTrue(state.make_decision(False))
        self.assertTrue(self.until(state, 'done'))

    def test_invalid_source_capacity_cannot_be_saved(self):
        data=json.loads((self.project/'project_constraints.json').read_text())
        data['max_extra_hours_for_approval'] = 0
        (self.project/'project_constraints.json').write_text(json.dumps(data))
        state = DashboardState(client_factory=ModelNeverJSON, project=self.project)
        self.assertTrue(state.start('qwen3:8b', 'تحلیل کن', 600))
        self.assertTrue(self.until(state,'error'))
        self.assertFalse((self.project/'output/plan.md').exists())

    def test_stale_contract_after_user_approval_blocks_save(self):
        state = DashboardState(client_factory=ModelValid,project=self.project)
        self.assertTrue(state.start('qwen3:8b', 'تحلیل کن', 600))
        self.assertTrue(self.until(state,'approval'))
        data=json.loads((self.project/'project_constraints.json').read_text())
        data['hours_per_day'] = 1.8
        (self.project/'project_constraints.json').write_text(json.dumps(data))
        self.assertTrue(state.make_decision(True,True))
        self.assertTrue(self.until(state,'error'))
        self.assertFalse((self.project/'output/plan.md').exists())

class ClientPayload(unittest.TestCase):
    def test_structured_request_uses_ollama_format_and_no_tools(self):
        from keyomars_agent.orchestrator import PLAN_SCHEMA
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self): return b'{"message":{"role":"assistant","content":"{}"}}'
        with patch('keyomars_agent.ollama_client.urlopen',return_value=FakeResponse()) as req:
            result=OllamaClient('qwen3:8b',timeout=600).chat_structured([{'role':'user','content':'Hi'}],PLAN_SCHEMA)
            body=json.loads(req.call_args.args[0].data.decode('utf8'))
            self.assertEqual(body['format'],PLAN_SCHEMA)
            self.assertNotIn('tools',body)
            self.assertIs(body['think'],False)
            self.assertEqual(result['content'],'{}')

if __name__ == '__main__': unittest.main()
