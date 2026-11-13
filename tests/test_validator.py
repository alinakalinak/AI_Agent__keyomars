"""Three camera-ready test stories plus negative safety checks."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from keyomars_agent.tools import ProjectTools, ToolError
from keyomars_agent.validator import validate_schedule

BASE = Path(__file__).resolve().parents[1] / "sample_project"


def correct_schedule():
    # 10.5h of mandatory work, with the fifth day requiring 0.5h extra.
    # Same-day prerequisites can run in order, and a task can span days.
    return {
        'days': [
            {'day': 1, 'tasks': [{'id': 'TF-01', 'hours': 1.5}, {'id': 'TF-02', 'hours': 0.5}]},
            {'day': 2, 'tasks': [{'id': 'TF-02', 'hours': 1.5}, {'id': 'TF-03', 'hours': 0.5}]},
            {'day': 3, 'tasks': [{'id': 'TF-03', 'hours': 2.0}]},
            {'day': 4, 'tasks': [{'id': 'TF-04', 'hours': 1.5}, {'id': 'TF-06', 'hours': 0.5}]},
            {'day': 5, 'tasks': [{'id': 'TF-05', 'hours': 2.0}, {'id': 'TF-08', 'hours': 0.5}]},
        ],
        'deferred': [{'id': 'TF-07', 'reason': 'قابلیت ظاهری اختیاری است و برای نسخه اولیه به تعویق می‌افتد.'}],
    }


class TestValidator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        for filename in ['tasks.json', 'project_constraints.json', 'README.md']:
            shutil.copy2(BASE / filename, self.project / filename)

    def tearDown(self):
        self.tmp.cleanup()

    def test_case_1_correct_plan_requires_extra_approval(self):
        v = validate_schedule(self.project, **correct_schedule())
        self.assertEqual(v['verdict'], 'needs_approval')
        self.assertEqual(v['error_count'], 0)
        self.assertAlmostEqual(v['mandatory_hours'], 10.5)
        self.assertAlmostEqual(v['base_capacity_hours'], 10.0)
        self.assertAlmostEqual(v['total_hours_including_optional'], 13.5)
        self.assertAlmostEqual(v['extra_hours'], 0.5)
        self.assertEqual(v['deferred'][0]['id'], 'TF-07')
        # No implicit writing before affirmative human response.
        project_tools = ProjectTools(self.project, approve=lambda _m, _path: False)
        project_tools.validate_plan(**correct_schedule())
        self.assertFalse(project_tools.save_plan('پیشنهاد برنامه پنج‌روزه')['saved'])
        self.assertFalse((self.project / 'output' / 'plan.md').exists())
        authorized = ProjectTools(self.project, approve=lambda _m, _path: True)
        authorized.validate_plan(**correct_schedule())
        self.assertTrue(authorized.save_plan('پیشنهاد پروژه')['saved'])
        result = (self.project / 'output' / 'plan.md').read_text(encoding='utf8')
        self.assertIn('روز 5 — 2.5 ساعت', result)
        self.assertIn('اضافه‌کاری', result)

    def test_case_2_bad_dependencies_and_missing_task_rejected(self):
        bad = correct_schedule()
        # Start TF-03 before TF-02 is finished.
        bad['days'][0]['tasks'] = [{'id': 'TF-03', 'hours': 2.5}]
        bad['days'][1]['tasks'] = [{'id': 'TF-01', 'hours': 1.5}, {'id': 'TF-02', 'hours': 2.0}]
        bad['days'][3]['tasks'] = [{'id': 'TF-04', 'hours': 1.5}]  # TF-06 missing
        result = validate_schedule(self.project, **bad)
        codes = {x['code'] for x in result['issues']}
        self.assertEqual(result['verdict'], 'invalid')
        self.assertIn('DEPENDENCY_ORDER', codes)
        self.assertIn('TASK_MISSING', codes)
        tools = ProjectTools(self.project, approve=lambda *_: True)
        tools.validate_plan(**bad)
        with self.assertRaises(ToolError):
            tools.save_plan('پیشنهاد اشتباه')
        self.assertFalse((self.project / 'output' / 'plan.md').exists())

    def test_case_3_unexplained_optional_and_over_capacity_rejected(self):
        bad = correct_schedule()
        bad['deferred'] = []
        bad['days'][0]['tasks'].append({'id': 'TF-07', 'hours': 3})
        v = validate_schedule(self.project, **bad)
        codes = {x['code'] for x in v['issues']}
        self.assertEqual(v['verdict'], 'invalid')
        self.assertIn('OVERTIME_TOO_HIGH', codes)
        self.assertFalse(v['can_save_with_approval'])

    def test_optional_must_be_explicitly_deferred(self):
        bad = correct_schedule()
        bad['deferred'] = []
        v = validate_schedule(self.project, **bad)
        self.assertIn('TASK_MISSING', {x['code'] for x in v['issues']})

    def test_unrecognized_task_and_nan_rejected(self):
        bad = correct_schedule()
        bad['days'][0]['tasks'].append({'id':'UNKNOWN', 'hours':1})
        self.assertEqual(validate_schedule(self.project, **bad)['verdict'],'invalid')
        bad['days'][0]['tasks'][-1]['hours']=float('nan')
        with self.assertRaises(ValueError):
            validate_schedule(self.project, **bad)

    def test_source_changed_after_validation_blocks_save(self):
        tools = ProjectTools(self.project, approve=lambda *_: True)
        tools.validate_plan(**correct_schedule())
        config = self.project / 'project_constraints.json'
        data = json.loads(config.read_text(encoding='utf8'))
        data['hours_per_day'] = 1
        config.write_text(json.dumps(data), encoding='utf8')
        with self.assertRaises(ToolError):
            tools.save_plan('پیشنهاد قدیمی')

    def test_hidden_shell_and_write_still_forbidden(self):
        tools = ProjectTools(self.project, approve=lambda *_: True)
        with self.assertRaises(ToolError):
            tools.execute('run_shell', {'command':'rm something'})
        with self.assertRaises(ToolError):
            tools.execute('write_file', {'path':'tasks.json', 'content':'broken'})

if __name__ == '__main__':
    unittest.main()
