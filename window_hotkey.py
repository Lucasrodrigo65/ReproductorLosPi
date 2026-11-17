"""Dedicated Windows hotkey message pump; window promotion stays on Tk thread."""
import ctypes
from ctypes import wintypes
import os
import queue
import threading


class WindowsHotkey:
    ID = 0x4C50

    def __init__(self):
        self.ready = threading.Event()
        self.events = queue.Queue()
        self.error = None
        self.active = False
        self.thread_id = None
        self.thread = threading.Thread(target=self._listen, daemon=True)
        self.thread.start()
        self.ready.wait()
        if self.error:
            raise self.error

    def _listen(self):
        user = ctypes.WinDLL('user32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentThreadId.restype = wintypes.DWORD
        user.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        self.thread_id = kernel.GetCurrentThreadId()
        if not user.RegisterHotKey(None, self.ID, 0x4000 | 0x0002 | 0x0001, ord('M')):
            self.error = OSError(ctypes.get_last_error(), 'Ctrl+Alt+M ya está ocupado o no pudo registrarse')
            self.ready.set()
            return
        self.active = True
        self.ready.set()
        try:
            message = wintypes.MSG()
            while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == 0x0312 and message.wParam == self.ID:
                    self.events.put(True)
        finally:
            user.UnregisterHotKey(None, self.ID)
            self.active = False

    def pending(self):
        pressed = False
        while True:
            try:
                self.events.get_nowait()
                pressed = True
            except queue.Empty:
                return pressed

    def stop(self):
        if self.active:
            user = ctypes.WinDLL('user32', use_last_error=True)
            user.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
            self.thread.join(timeout=2)


def window_api(root):
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    user.GetForegroundWindow.restype = wintypes.HWND
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    return user, user.GetAncestor(root.winfo_id(), 2) or root.winfo_id()


def is_foreground(root):
    if os.name != 'nt':
        return root.focus_displayof() is not None
    user, handle = window_api(root)
    return user.GetForegroundWindow() == handle


def promote(root, activate=True):
    root.attributes('-topmost', True)
    root.lift()
    if os.name == 'nt':
        user, handle = window_api(root)
        # Stay above ordinary/borderless windows; no game injection or input hacks.
        user.SetWindowPos(handle, wintypes.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040 | 0x0010)
        if activate:
            user.SetForegroundWindow(handle)
