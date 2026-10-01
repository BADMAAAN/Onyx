import unittest
from unittest.mock import patch

from kk.tools.windows.native import foreground_window, list_monitors, list_processes
from kk.vision.screen_capture import ScreenCapture
from kk.tools.windows.windows import find_window_across_monitors


class WindowsIntegrationTests(unittest.TestCase):
    def test_foreground_and_processes(self):
        self.assertIn("title", foreground_window())
        self.assertGreater(len(list_processes()), 0)
        monitors = list_monitors()
        self.assertGreater(len(monitors), 0)
        self.assertGreater(monitors[0]["width"], 0)

    def test_temporary_screenshot(self):
        shot = ScreenCapture().capture("all")
        try:
            self.assertTrue(shot.path.exists())
            self.assertGreater(shot.width, 0)
            self.assertGreater(shot.height, 0)
        finally:
            shot.cleanup()
        self.assertFalse(shot.path.exists())

    def test_notepad_is_found_on_second_monitor_when_foreground_is_elsewhere(self):
        windows = [
            {'hwnd': 10, 'title': 'Browser', 'process': 'firefox.exe', 'monitor_index': 1, 'minimized': False},
            {'hwnd': 20, 'title': 'notes.txt — Блокнот', 'process': 'Notepad.exe', 'monitor_index': 2, 'minimized': False},
        ]
        monitors = [{'index':1}, {'index':2}]
        with patch('kk.tools.windows.windows.list_visible_windows',return_value=windows), \
             patch('kk.tools.windows.windows.list_monitors',return_value=monitors):
            result = find_window_across_monitors('Блокнот')
        self.assertTrue(result['found'])
        self.assertEqual(result['matches'][0]['hwnd'],20)
        self.assertEqual(result['matches'][0]['monitor_index'],2)
        self.assertEqual(result['monitors_checked'],monitors)


if __name__ == "__main__":
    unittest.main()
