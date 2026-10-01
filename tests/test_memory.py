from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kk.memory.database import MemoryDatabase


class MemoryTests(unittest.TestCase):
    def test_roundtrip_all_entities(self):
        with TemporaryDirectory() as folder:
            db = MemoryDatabase(Path(folder) / "memory.db")
            db.add_conversation("s", "user", "привет")
            db.remember("telegram_alias", "Telegram Desktop", "alias")
            db.record_action("test", {}, {"ok": True}, True, "READ", 1)
            db.save_skill("evening_setup", "Открыть приложения", [{"tool": "open_application"}])
            self.assertEqual(db.recall("telegram")[0]["value"], "Telegram Desktop")
            self.assertEqual(db.counts(), {"conversations": 1, "memories": 1, "actions": 1, "skills": 1})
            db.close()


if __name__ == "__main__":
    unittest.main()

