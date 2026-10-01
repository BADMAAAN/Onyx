import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from kk.core.orchestrator import Orchestrator
from kk.core.state import StateMachine
from kk.core.task import TaskStatus
from kk.memory.database import MemoryDatabase
from kk.permissions.policy import PermissionLevel, PermissionPolicy
from kk.tools.registry import Tool, ToolRegistry
from kk.tools.windows.browser import BrowserTabs


class CollectionStatusTests(unittest.TestCase):
    def test_unavailable_enumeration_remains_unavailable_after_full_turn(self):
        for allowed, status in ((False, TaskStatus.BLOCKED), (True, TaskStatus.UNVERIFIED)):
            with self.subTest(status=status), TemporaryDirectory() as directory:
                root = Path(directory)
                memory = MemoryDatabase(root/'memory.db')
                try:
                    tools = ToolRegistry(PermissionPolicy(PermissionLevel.DANGEROUS, root/'STOP'),
                                         memory, logging.getLogger('test'))
                    adapter = BrowserTabs(policy_allowed=allowed)
                    tools.register(Tool('list_browser_tabs', '', {'type':'object',
                        'properties':{'scope':{'type':'string'}}, 'required':['scope']},
                        PermissionLevel.READ, adapter.list))
                    provider = SimpleNamespace(chat=Mock(side_effect=AssertionError('No semantic fallback')))
                    agent = Orchestrator(provider, 'test', tools, memory,
                                         logging.getLogger('test'), StateMachine())
                    answer = agent.respond('Какие вкладки открыты?')
                    self.assertEqual(agent.last_task.status, status)
                    self.assertEqual(agent.last_task.metrics['collection_observation']['status'], status.value)
                    self.assertFalse(agent.last_task.actions[0]['verification']['verified'])
                    self.assertIn('Полный список не подтверждён', answer)
                    self.assertEqual([action['tool'] for action in agent.last_task.actions], ['list_browser_tabs'])
                    with self.assertRaises(ValueError):
                        agent.skills.propose_task(agent.last_task)
                    provider.chat.assert_not_called()
                finally:
                    memory.close()


if __name__ == '__main__':
    unittest.main()
