import logging
import sys
import unittest
from kk.logging_setup import RedactingFormatter, RedactingFilter


class LoggingRepairTests(unittest.TestCase):
    def test_exception_secrets_redacted_after_traceback_formatting(self):
        try:
            raise ValueError('{"token": "never-persist-this", "password": "nor-this"} authorization=Bearer private-value')
        except ValueError:
            record=logging.LogRecord('kk',logging.ERROR,__file__,12,'worker failed',(),sys.exc_info())
        output=RedactingFormatter('%(message)s').format(record)
        self.assertIn('Traceback',output)
        self.assertIn('ValueError',output)
        for secret in ['never-persist-this','nor-this','private-value']:
            self.assertNotIn(secret,output)

    def test_conversation_stays_private(self):
        record=logging.LogRecord('kk',logging.INFO,__file__,20,'[Onyx] User: private conversation',(),None)
        RedactingFilter().filter(record)
        self.assertNotIn('private conversation',record.getMessage())


class ToolTraceRepairTests(unittest.TestCase):
    def test_fast_tool_boundaries_share_turn_ids_without_logging_payload(self):
        from unittest.mock import Mock
        from kk.tools.registry import ToolRegistry, Tool
        from kk.permissions.policy import PermissionLevel
        from kk.core.task import CancellationToken
        token=CancellationToken();token.trace_id='verified-turn'
        registry=ToolRegistry(Mock(),Mock(),logging.getLogger('kk.trace-test'))
        registry.register(Tool('read_probe','probe',{'type':'object','properties':{}},PermissionLevel.READ,lambda:{'verified':True}))
        context=registry.cancellation.set(token)
        try:result=registry.execute('read_probe')
        finally:registry.cancellation.reset(context)
        self.assertTrue(result.ok)
        self.assertEqual([e['event'] for e in token.trace],['tool_execute_start','tool_execute_end'])
        self.assertTrue(all(e['task_id']=='verified-turn' and e['session_id']==token.session_id for e in token.trace))
        self.assertTrue(token.trace[-1]['ok'])
