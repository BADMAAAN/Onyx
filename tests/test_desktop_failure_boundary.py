from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from kk.core.desktop_commands import DesktopContext, run_desktop
from kk.core.state import StateMachine
from kk.core.task import AgentTask, TaskStatus
from kk.tools.registry import ToolResult


class DesktopFailureBoundaryTests(TestCase):
    def test_raw_os_network_errors_are_logged_but_not_returned_to_chat_or_speech(self):
        for error in ('<urlopen error [WinError 10061] C:\\Users\\person\\secret>', 'RuntimeError: internal provider traceback'):
            tools=SimpleNamespace(execute=Mock(return_value=ToolResult(False,error=error)),personal_intents=None)
            task=AgentTask('Открой Firefox')
            with self.assertLogs('kk',level='ERROR') as logs:
                answer=run_desktop('Открой Firefox',task,tools,StateMachine(),DesktopContext())
            self.assertEqual(task.status,TaskStatus.FAILED)
            self.assertIn('Не удалось выполнить действие',answer)
            for raw in ('WinError','C:\\','traceback','urlopen','RuntimeError'):
                self.assertNotIn(raw,answer)
            self.assertIn(task.id,'\n'.join(logs.output))
            self.assertIn(error,'\n'.join(logs.output))
