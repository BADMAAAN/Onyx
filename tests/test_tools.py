from pathlib import Path
from tempfile import TemporaryDirectory
import logging
import unittest

from kk.memory.database import MemoryDatabase
from kk.permissions.policy import PermissionLevel, PermissionPolicy
from kk.tools.registry import Tool, ToolRegistry


class ToolRegistryTests(unittest.TestCase):
    def test_validation_execution_and_audit(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            memory = MemoryDatabase(root / "test.db")
            policy = PermissionPolicy(PermissionLevel.ACTION, root / "STOP.KK")
            registry = ToolRegistry(policy, memory, logging.getLogger("test"))
            registry.register(Tool("echo", "echo", {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}, PermissionLevel.READ, lambda text: text))
            self.assertEqual(registry.execute("echo", {"text": "ok"}).data, "ok")
            self.assertFalse(registry.execute("echo", {}).ok)
            self.assertEqual(len(memory.recent_actions()), 2)
            memory.close()

    def test_emergency_stop(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            memory = MemoryDatabase(root / "test.db")
            policy = PermissionPolicy(PermissionLevel.ACTION, root / "STOP.KK")
            registry = ToolRegistry(policy, memory, logging.getLogger("test"))
            registry.register(Tool("act", "act", {"type": "object", "properties": {}}, PermissionLevel.ACTION, lambda: True))
            policy.trigger_stop()
            self.assertFalse(registry.execute("act").ok)
            memory.close()


if __name__ == "__main__":
    unittest.main()

