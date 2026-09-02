# -*- coding: utf-8 -*-
"""
M59 AppBar Integration Module
Handles Windows Application Desktop Toolbar (AppBar) API Registration,
docking edge negotiation, and workspace area reservations.
"""

import sys
import os
import ctypes

if sys.platform == 'win32':
    try:
        from ctypes import wintypes
    except ImportError:
        wintypes = None
else:
    wintypes = None

try:
    import win32gui
    import win32con
    import win32process
except Exception:
    win32gui = None
    win32con = None
    win32process = None

if sys.platform == 'win32' and wintypes:
    class APPBARDATA(ctypes.Structure):
        _fields_ = [
            ('cbSize', wintypes.DWORD),
            ('hWnd', wintypes.HWND),
            ('uCallbackMessage', wintypes.UINT),
            ('uEdge', wintypes.UINT),
            ('rc', wintypes.RECT),
            ('lParam', wintypes.LPARAM),
        ]

    class MSG(ctypes.Structure):
        _fields_ = [
            ('hwnd', wintypes.HWND),
            ('message', wintypes.UINT),
            ('wParam', wintypes.WPARAM),
            ('lParam', wintypes.LPARAM),
            ('time', wintypes.DWORD),
            ('pt', wintypes.POINT),
        ]

    try:
        shell32 = ctypes.windll.shell32
        user32 = ctypes.windll.user32

        shell32.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.POINTER(APPBARDATA)]
        shell32.SHAppBarMessage.restype = ctypes.c_size_t

        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.UINT
        ]
        user32.SetWindowPos.restype = wintypes.BOOL

        user32.SystemParametersInfoW.argtypes = [
            wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT
        ]
        user32.SystemParametersInfoW.restype = wintypes.BOOL
    except Exception as e:
        print(f"[APPBAR-INIT] Error setting win32 function prototypes: {e}", flush=True)

ABM_NEW = 0x00000000
ABM_REMOVE = 0x00000001
ABM_QUERYPOS = 0x00000002
ABM_SETPOS = 0x00000003
ABE_RIGHT = 2
APPBAR_CALLBACK = 0x0400 + 101

# Track active registered AppBar HWNDs
registered_appbar_hwnds = set()

def reset_desktop_workarea():
    """Restores the primary monitor desktop work area to full dimensions if it was left corrupted by a killed/halted process."""
    if sys.platform != 'win32' or not wintypes:
        return
    try:
        user32 = ctypes.windll.user32
        SPI_GETWORKAREA = 0x0030
        SPI_SETWORKAREA = 0x002F
        SPIF_UPDATEINIFILE = 0x0001
        SPIF_SENDCHANGE = 0x0002

        screen_w = user32.GetSystemMetrics(0) # SM_CXSCREEN = 0
        screen_h = user32.GetSystemMetrics(1) # SM_CYSCREEN = 1

        class RECT(ctypes.Structure):
            _fields_ = [('left', wintypes.LONG), ('top', wintypes.LONG),
                        ('right', wintypes.LONG), ('bottom', wintypes.LONG)]

        rc = RECT()
        user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rc), 0)

        # If right edge is constricted (less than screen_w), reset it to full screen_w
        if rc.right < screen_w:
            print(f"[APPBAR-RESET] Detected restricted work area (right was {rc.right}, screen width is {screen_w}). Restoring...", flush=True)
            rc.right = screen_w
            user32.SystemParametersInfoW(SPI_SETWORKAREA, 0, ctypes.byref(rc), SPIF_UPDATEINIFILE | SPIF_SENDCHANGE)
        else:
            user32.SystemParametersInfoW(0x0014, 0, None, 0x0001 | 0x0002)
    except Exception as e:
        print(f"[APPBAR-RESET] Error resetting desktop work area: {e}", flush=True)

def cleanup_all_appbars():
    """Unregisters all active AppBars and restores desktop work area on exit or crash."""
    for h in list(registered_appbar_hwnds):
        try:
            unregister_window_appbar(h)
        except Exception:
            pass
    reset_desktop_workarea()

import atexit
atexit.register(cleanup_all_appbars)

def _appbar_sig_handler(sig, frame):
    cleanup_all_appbars()
    sys.exit(0)

try:
    import signal
    signal.signal(signal.SIGINT, _appbar_sig_handler)
    signal.signal(signal.SIGTERM, _appbar_sig_handler)
except Exception:
    pass

def register_window_appbar(hwnd_int, width=290):
    """Registers HWND as a native Windows AppBar on the right screen edge.
    Adjusts Windows desktop work area so all maximized windows resize around it."""
    if sys.platform != 'win32' or not wintypes:
        return False
    try:
        shell32 = ctypes.windll.shell32
        user32 = ctypes.windll.user32

        # 0. Ensure clean state by resetting any stale/corrupted work area
        reset_desktop_workarea()

        # Remove any existing AppBar registration for this HWND
        abd_rm = APPBARDATA()
        abd_rm.cbSize = ctypes.sizeof(APPBARDATA)
        abd_rm.hWnd = wintypes.HWND(hwnd_int)
        shell32.SHAppBarMessage(ABM_REMOVE, ctypes.byref(abd_rm))

        abd = APPBARDATA()
        abd.cbSize = ctypes.sizeof(APPBARDATA)
        abd.hWnd = wintypes.HWND(hwnd_int)
        abd.uCallbackMessage = APPBAR_CALLBACK

        # 1. Register AppBar with Windows Shell
        shell32.SHAppBarMessage(ABM_NEW, ctypes.byref(abd))

        # 2. Get physical screen resolution (NOT work area!)
        screen_w = user32.GetSystemMetrics(0) # SM_CXSCREEN = 0
        screen_h = user32.GetSystemMetrics(1) # SM_CYSCREEN = 1

        abd.uEdge = ABE_RIGHT
        abd.rc.left = screen_w - width
        abd.rc.top = 0
        abd.rc.right = screen_w
        abd.rc.bottom = screen_h

        # 3. Request & Set Position (Always anchor right edge strictly to screen_w)
        shell32.SHAppBarMessage(ABM_QUERYPOS, ctypes.byref(abd))
        abd.rc.right = screen_w
        abd.rc.left = screen_w - width
        abd.rc.top = 0
        abd.rc.bottom = screen_h
        shell32.SHAppBarMessage(ABM_SETPOS, ctypes.byref(abd))

        # 4. Position Window accurately via SetWindowPos
        SWP_NOACTIVATE = 0x0010
        SWP_SHOWWINDOW = 0x0040
        SWP_FRAMECHANGED = 0x0020
        user32.SetWindowPos(
            wintypes.HWND(hwnd_int),
            wintypes.HWND(-1), # HWND_TOPMOST
            screen_w - width,
            0,
            width,
            screen_h,
            SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_FRAMECHANGED
        )
        registered_appbar_hwnds.add(hwnd_int)
        print(f"[APPBAR] Successfully registered HWND {hwnd_int} as right-edge AppBar (width={width}).", flush=True)
        return True
    except Exception as e:
        print(f"[APPBAR-ERR] Failed to register AppBar: {e}", flush=True)
        return False

def update_window_appbar_pos(hwnd_int, width=290):
    """Updates position of an ALREADY REGISTERED AppBar when resized without calling ABM_NEW."""
    if sys.platform != 'win32' or not wintypes:
        return False
    try:
        shell32 = ctypes.windll.shell32
        user32 = ctypes.windll.user32

        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)

        abd = APPBARDATA()
        abd.cbSize = ctypes.sizeof(APPBARDATA)
        abd.hWnd = wintypes.HWND(hwnd_int)
        abd.uEdge = ABE_RIGHT
        abd.rc.left = screen_w - width
        abd.rc.top = 0
        abd.rc.right = screen_w
        abd.rc.bottom = screen_h

        shell32.SHAppBarMessage(ABM_QUERYPOS, ctypes.byref(abd))
        abd.rc.right = screen_w
        abd.rc.left = screen_w - width
        abd.rc.top = 0
        abd.rc.bottom = screen_h
        shell32.SHAppBarMessage(ABM_SETPOS, ctypes.byref(abd))

        SWP_NOACTIVATE = 0x0010
        SWP_SHOWWINDOW = 0x0040
        SWP_FRAMECHANGED = 0x0020
        user32.SetWindowPos(
            wintypes.HWND(hwnd_int),
            wintypes.HWND(-1),
            screen_w - width,
            0,
            width,
            screen_h,
            SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_FRAMECHANGED
        )
        return True
    except Exception as e:
        print(f"[APPBAR-ERR] Failed to update AppBar pos: {e}", flush=True)
        return False

def unregister_window_appbar(hwnd_int):
    """Unregisters HWND from Windows AppBar system, restoring desktop work area for all windows."""
    if sys.platform != 'win32' or not wintypes:
        return False
    try:
        shell32 = ctypes.windll.shell32
        user32 = ctypes.windll.user32
        abd = APPBARDATA()
        abd.cbSize = ctypes.sizeof(APPBARDATA)
        abd.hWnd = wintypes.HWND(hwnd_int)
        shell32.SHAppBarMessage(ABM_REMOVE, ctypes.byref(abd))

        registered_appbar_hwnds.discard(hwnd_int)
        reset_desktop_workarea()
        print(f"[APPBAR] Unregistered HWND {hwnd_int} as AppBar.", flush=True)
        return True
    except Exception as e:
        print(f"[APPBAR-ERR] Failed to unregister AppBar: {e}", flush=True)
        return False

