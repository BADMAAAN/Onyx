import unittest

from kk.core.state import AgentState, StateMachine


class StateMachineTests(unittest.TestCase):
    def test_transitions_and_listener(self):
        state = StateMachine()
        seen = []
        state.subscribe(lambda current, detail: seen.append((current, detail)))
        state.set(AgentState.LISTENING, "test")
        state.set(AgentState.THINKING)
        self.assertEqual(state.state, AgentState.THINKING)
        self.assertEqual(seen[0], (AgentState.LISTENING, "test"))


if __name__ == "__main__":
    unittest.main()
