"""Inline/background Live uses one capture owner and transient presentation."""
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest import TestCase

from kk.core.control import request_control
from kk.core.state import AgentState, StateMachine
from kk.ui.gpu_bridge import visual_packet
from kk.voice.conversation import ConversationSession
from tests.test_final_integration import FixtureRuntime


class InlineLivePolicyTests(TestCase):
    def test_inline_suppresses_aura_and_background_ack_preserves_session_and_prefs(self):
        with TemporaryDirectory() as folder:
            runtime = FixtureRuntime(Path(folder))
            try:
                loop=runtime.loop; capture=loop.capture; capture.controls=Queue()
                loop.conversation=ConversationSession(25, runtime.state)
                cid=runtime.control.desktop.create()['conversation_id']
                runtime.state.set_visual_visible(True)
                preference=runtime.memory.get('ui.visual_visible')
                result=request_control(folder,'voice.live',conversation_id=cid)
                self.assertTrue(result['started'])
                self.assertTrue(runtime.state.desktop_inline_live)
                self.assertTrue(loop.conversation.active)
                packet=visual_packet(runtime.state,{})
                self.assertTrue(packet['user_visual_hidden'])
                self.assertFalse(packet['visible'])
                self.assertTrue(runtime.state.orb_visible)
                serial=packet['activation_serial']
                with self.assertRaises(RuntimeError):
                    request_control(folder,'voice.liveBackground',conversation_id='wrong-conversation')
                self.assertTrue(runtime.state.desktop_inline_live)
                result=request_control(folder,'voice.liveBackground',conversation_id=cid)
                self.assertTrue(result['background'])
                self.assertFalse(runtime.state.desktop_inline_live)
                packet=visual_packet(runtime.state,{})
                self.assertTrue(packet['visible'])
                self.assertGreater(packet['activation_serial'],serial)
                self.assertEqual(packet['activation_source'],'desktop_live_background')
                self.assertIs(loop.capture,capture)
                self.assertEqual(loop.desktop_live_scope,cid)
                self.assertEqual(runtime.memory.get('ui.visual_visible'),preference)
                request_control(folder,'voice.stopLive')
                self.assertFalse(runtime.state.desktop_inline_live)
                self.assertFalse(loop.conversation.active)
                with self.assertRaises(RuntimeError):
                    request_control(folder,'voice.liveBackground',conversation_id=cid)
            finally:
                runtime.close()

    def test_hidden_aura_preference_survives_inline_background_and_new_state(self):
        with TemporaryDirectory() as folder:
            runtime=FixtureRuntime(Path(folder))
            try:
                loop=runtime.loop;loop.capture.controls=Queue()
                loop.conversation=ConversationSession(25,runtime.state)
                cid=runtime.control.desktop.create()['conversation_id']
                runtime.state.set_visual_visible(False)
                preference=runtime.memory.get('ui.visual_visible')
                request_control(folder,'voice.live',conversation_id=cid)
                request_control(folder,'voice.liveBackground',conversation_id=cid)
                self.assertFalse(visual_packet(runtime.state,{})['visible'])
                self.assertEqual(runtime.memory.get('ui.visual_visible'),preference)
                restored=StateMachine(runtime.memory)
                self.assertFalse(restored.desktop_inline_live)
                self.assertFalse(restored.orb_visible)
            finally:
                runtime.close()

    def test_expiry_clears_inline_flag_for_later_wake_sessions(self):
        state=StateMachine();conversation=ConversationSession(1,state)
        state.set_desktop_inline_live(True);conversation.touch(now=0)
        self.assertTrue(conversation.expire(now=2))
        self.assertFalse(state.desktop_inline_live)
        conversation.touch(now=3);state.activate_visual('wake')
        self.assertTrue(visual_packet(state,{})['visible'])
