import queue
import os
import time
import unittest
from unittest.mock import MagicMock, patch

from overlay import StreamingMusicPlayerEnhanced
from window_hotkey import WindowsHotkey


class WindowHotkeyTests(unittest.TestCase):
    def setUp(self):
        self.app = object.__new__(StreamingMusicPlayerEnhanced)
        self.app.root = MagicMock()
        self.app.root.state.return_value = 'normal'
        self.app.is_visible = True
        self.app.hotkey_closed = False
        self.app.hotkey_events = queue.Queue()
        self.app.hotkey_listener = None

    def test_visible_but_behind_game_is_shown_instead_of_hidden(self):
        with patch('overlay.is_foreground', return_value=False), patch('overlay.promote') as promote:
            self.app.toggle_window()
            self.app.root.deiconify.assert_called_once()
            self.app.root.withdraw.assert_not_called()
            promote.assert_called_once_with(self.app.root)
            self.app._keep_overlay_above()
            promote.assert_called_with(self.app.root, activate=False)

    def test_foreground_hotkey_hides_and_removes_topmost(self):
        with patch('overlay.is_foreground', return_value=True):
            self.app.toggle_window()
        self.app.root.withdraw.assert_called_once()
        self.app.root.attributes.assert_called_with('-topmost', False)
        self.assertFalse(self.app.is_visible)

    def test_minimized_window_is_restored(self):
        self.app.root.state.return_value = 'iconic'
        with patch('overlay.promote') as promote:
            self.app.toggle_window()
            promote.assert_called_once()
        self.assertTrue(self.app.is_visible)

    def test_listener_events_are_consumed_by_ui_poll(self):
        self.app.hotkey_events.put(True)
        self.app.toggle_window = MagicMock()
        self.app._poll_hotkey()
        self.app.toggle_window.assert_called_once()
        self.app.root.after.assert_called_with(50, self.app._poll_hotkey)

    def test_delayed_raise_cannot_reopen_hidden_or_closed_window(self):
        with patch('overlay.promote') as promote:
            self.app.is_visible = False
            self.app._keep_overlay_above()
            self.app.is_visible = True
            self.app.hotkey_closed = True
            self.app._keep_overlay_above()
            promote.assert_not_called()


@unittest.skipUnless(os.name == 'nt', 'Requires the Windows message queue')
class NativeMessageTests(unittest.TestCase):
    def test_message_reaches_worker_while_tk_processes_events(self):
        import ctypes
        from ctypes import wintypes
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        listener = None
        try:
            listener = WindowsHotkey()
            user = ctypes.WinDLL('user32')
            user.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            self.assertTrue(user.PostThreadMessageW(listener.thread_id, 0x0312, listener.ID, 0))
            deadline = time.monotonic() + 2
            received = False
            while time.monotonic() < deadline:
                root.update()
                if listener.pending():
                    received = True
                    break
                time.sleep(.01)
            self.assertTrue(received)
        finally:
            if listener:
                listener.stop()
                self.assertFalse(listener.thread.is_alive())
            root.destroy()


if __name__ == '__main__':
    unittest.main()
