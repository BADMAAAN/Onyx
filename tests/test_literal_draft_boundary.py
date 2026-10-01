import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from kk.core.task import CancellationToken
from kk.memory.database import MemoryDatabase
from kk.permissions.policy import PermissionLevel, PermissionPolicy
from kk.tools.registry import Tool, ToolRegistry


class LiteralDraftBoundaryTests(TestCase):
    def test_unresolved_target_blocks_every_mutation_until_new_user_turn(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            memory = MemoryDatabase(root / 'test.db')
            try:
                registry = ToolRegistry(PermissionPolicy(PermissionLevel.ACTION, root / 'STOP'),
                                        memory, logging.getLogger('target_boundary'))
                mutate, read = Mock(return_value={'verified':True}), Mock(return_value={'items':[]})
                registry.register(Tool('open_folder', 'Fixture', {'type':'object'}, PermissionLevel.ACTION, mutate))
                registry.register(Tool('list_fixture', 'Fixture', {'type':'object'}, PermissionLevel.READ, read))
                token = CancellationToken()
                token.user_request = 'не закрывай VS Code, открой папку Onyx на рабочем столе'
                token.target_confirmation_required = {'question':'Открыть похожее имя?'}
                context = registry.cancellation.set(token)
                try:
                    denied = registry.execute('open_folder')
                    self.assertFalse(denied.ok)
                    self.assertEqual(denied.data['status'], 'needs_clarification')
                    mutate.assert_not_called()
                    self.assertTrue(registry.execute('list_fixture').ok)
                    read.assert_called_once()
                finally:
                    registry.cancellation.reset(context)
                next_turn = CancellationToken()
                next_turn.user_request = 'открой папку с точным именем'
                context = registry.cancellation.set(next_turn)
                try:
                    self.assertTrue(registry.execute('open_folder').ok)
                    mutate.assert_called_once()
                finally:
                    registry.cancellation.reset(context)
            finally:
                memory.close()

    def test_planner_cannot_change_payload_of_safe_draft(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            memory = MemoryDatabase(root / 'test.db')
            try:
                registry = ToolRegistry(PermissionPolicy(PermissionLevel.ACTION, root / 'STOP'),
                                        memory, logging.getLogger('literal_boundary'))
                create = Mock(return_value={'verified': True})
                registry.register(Tool('open_obsidian_draft', 'Fixture',
                    {'type':'object', 'properties':{'text':{'type':'string'}}, 'required':['text']},
                    PermissionLevel.ACTION, create))
                token = CancellationToken()
                token.user_request = 'открой Obsidian и напиши: «Onyx, мне нужен ifconfig»'
                context = registry.cancellation.set(token)
                try:
                    self.assertFalse(registry.execute('open_obsidian_draft', {'text':'ipconfig'}).ok)
                    create.assert_not_called()
                    self.assertTrue(registry.execute('open_obsidian_draft', {'text':'Onyx, мне нужен ifconfig'}).ok)
                    create.assert_called_once_with(text='Onyx, мне нужен ifconfig')
                    self.assertNotIn('ifconfig', str(registry._safe_arguments('open_obsidian_draft',
                        {'text':'Onyx, мне нужен ifconfig'})))
                finally:
                    registry.cancellation.reset(context)
            finally:
                memory.close()
