"""Integration of the actual 8-task BrainByte sample with the UI state machine.
No Ollama call; the model returns exactly the tool-calling structure Qwen should produce.
"""
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from run_web import DashboardState
from test_validator import correct_schedule

SAMPLE = Path(__file__).resolve().parents[1] / 'sample_project'

class MockSequence:
    def __init__(self,model,timeout):self.round=0
    def chat(self,messages,tools):
        self.round += 1
        if self.round==1:
            return {'content':'','tool_calls':[
                {'function':{'name':'list_project_files','arguments':{}}},
                {'function':{'name':'list_tasks','arguments':{}}}]}
        if self.round==2:
            return {'content':'','tool_calls':[
                {'function':{'name':'read_project_file','arguments':{'path':'project_constraints.json'}}},
                {'function':{'name':'validate_plan','arguments':correct_schedule()}}]}
        if self.round==3:
            return {'content':'','tool_calls':[{'function':{'name':'save_plan','arguments':{'markdown':'# تحلیل کیومرث\nمحدودیت اصلی نیم ساعت کسری ظرفیت است.'}}}]}
        return {'content':'پیشنهاد آماده شد. برای برنامه به نیم ساعت اضافه‌کاری نیاز است.','tool_calls':[]}

class TestSampleEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.project=Path(self.tmp.name)
        for f in ['tasks.json','project_constraints.json','README.md','notes.md','app_stub.py']:
            shutil.copy2(SAMPLE / f, self.project / f)
        self.state=DashboardState(client_factory=MockSequence,project=self.project)

    def tearDown(self):self.tmp.cleanup()

    def wait_status(self, status):
        for _ in range(300):
            if self.state.snapshot()['status']==status:return True
            time.sleep(.01)
        return False

    def test_sample_approval_and_preservation(self):
        before=(self.project/'tasks.json').read_bytes()
        self.assertTrue(self.state.start('qwen3:8b','پروژه را تحلیل کن',600))
        self.assertTrue(self.wait_status('approval'))
        self.assertFalse(self.state.make_decision(True,False))
        self.assertFalse((self.project/'output'/'plan.md').exists())
        self.assertEqual(self.state.snapshot()['validation']['verdict'],'needs_approval')
        self.assertAlmostEqual(self.state.snapshot()['validation']['extra_hours'],.5)
        self.assertTrue(self.state.make_decision(True,True))
        self.assertTrue(self.wait_status('done'))
        self.assertTrue(self.state.snapshot()['saved'])
        self.assertEqual((self.project/'tasks.json').read_bytes(), before)
        output=(self.project/'output'/'plan.md').read_text(encoding='utf8')
        self.assertIn('TF-07',output)
        self.assertIn('روز 5',output)
        self.assertIn('0.5 ساعت',output)

    def test_stale_source_before_save_blocks_writing(self):
        self.assertTrue(self.state.start('qwen3:8b','پروژه را تحلیل کن',600))
        self.assertTrue(self.wait_status('approval'))
        self.project.joinpath('notes.md').write_text('changed', encoding='utf8')  # unrelated read-only note
        # Risk model may be stale in narrative, but tasks/constraints are source of arithmetic.
        self.assertTrue(self.state.make_decision(False))
        self.assertTrue(self.wait_status('done'))
        self.assertFalse((self.project/'output'/'plan.md').exists())
