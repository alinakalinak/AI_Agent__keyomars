import json
import tempfile
import unittest
from pathlib import Path
from keyomars_agent.tools import ProjectTools, ToolError
from keyomars_agent.agent import run_agent


class TestSafety(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        (self.root/'README.md').write_text('Demo project', encoding='utf8')
        (self.root/'tasks.json').write_text(json.dumps({'tasks':[{'id':'T-1','status':'todo','priority':'high','estimate_hours':1,'depends_on':[]}]}),encoding='utf8')
        (self.root/'project_constraints.json').write_text(json.dumps({'working_days':5,'hours_per_day':2,'max_extra_hours_for_approval':2,'optional_task_ids':[]}),encoding='utf8')
        self.t=ProjectTools(self.root, approve=lambda content,dest:False)
        self.schedule={'days':[{'day':i,'tasks':([{'id':'T-1','hours':1}] if i==1 else [])} for i in range(1,6)],'deferred':[]}

    def tearDown(self): self.temp.cleanup()

    def test_read_only_tools(self):
        self.assertEqual(self.t.list_tasks()['count'],1)
        self.assertEqual(self.t.read_project_file('README.md')['content'],'Demo project')
        self.assertFalse((self.root/'output').exists())

    def test_traversal_and_absolute_paths_blocked(self):
        for bad in ['../outside.txt','/etc/passwd','C:\\Windows\\win.ini','output/plan.md','.secrets']:
            with self.subTest(bad=bad), self.assertRaises((ToolError,FileNotFoundError)):
                self.t.read_project_file(bad)

    def test_nontext_file_blocked(self):
        (self.root/'photo.png').write_bytes(b'fake')
        with self.assertRaises(ToolError):self.t.read_project_file('photo.png')

    def test_symlink_blocked(self):
        p=self.root/'symlink.md'
        try:p.symlink_to(self.root.parent/'outside.txt')
        except OSError:self.skipTest('No symlink creation')
        with self.assertRaises((ToolError,FileNotFoundError)):self.t.read_project_file('symlink.md')

    def test_save_requires_validation(self):
        with self.assertRaises(ToolError):self.t.save_plan('# An unchecked plan')
        self.assertFalse((self.root/'output'/'plan.md').exists())

    def test_save_denied_by_default(self):
        self.t.validate_plan(**self.schedule)
        self.assertFalse(self.t.save_plan('# A plan')['saved'])
        self.assertFalse((self.root/'output'/'plan.md').exists())

    def test_save_only_approved_plan(self):
        approved=ProjectTools(self.root, approve=lambda content,dest:True)
        approved.validate_plan(**self.schedule)
        self.assertTrue(approved.save_plan('# Safe plan\n- One task')['saved'])
        text=(self.root/'output'/'plan.md').read_text(encoding='utf8')
        self.assertIn('T-1',text)
        self.assertEqual(json.loads((self.root/'tasks.json').read_text())['tasks'][0]['status'],'todo')

    def test_extra_args_and_unknown_tool_blocked(self):
        with self.assertRaises(ToolError):self.t.execute('save_plan',{'markdown':'# A','path':'../../bad'})
        with self.assertRaises(ToolError):self.t.execute('run_shell',{'command':'echo test'})


class FakeOllama:
    def __init__(self):self.calls=0
    def chat(self,messages,tools):
        self.calls+=1
        if self.calls==1:
            return {'content':'','tool_calls':[{'function':{'name':'list_tasks','arguments':{}}},{'function':{'name':'validate_plan','arguments':{'days':[{'day':i,'tasks':([{'id':'T-1','hours':1}] if i==1 else [])} for i in range(1,6)],'deferred':[]}}}]}
        if self.calls==2:
            return {'content':'','tool_calls':[{'function':{'name':'save_plan','arguments':{'markdown':'# اول دیتابیس و بعد تست'}}}]}
        return {'content':'یک برنامه معتبر ساخته شد، اما کارها انجام نشده‌اند.', 'tool_calls': []}


class TestAgentLoop(TestSafety):
    def test_mock_tool_calling_loop(self):
        fake=FakeOllama()
        output=[]
        result=run_agent(fake,self.t,'اولویت بندی کن',printer=output.append)
        self.assertEqual(fake.calls,3)
        self.assertIn('برنامه معتبر',result)
        self.assertFalse((self.root/'output'/'plan.md').exists())
        self.assertTrue(any('validate_plan' in line for line in output))

if __name__=='__main__':unittest.main()
