import sys
import os
import time
import math
import json
import csv
import re
import threading
import ctypes
from collections import deque
from datetime import datetime
from m59_logging import (
    setup_logging, get_logger, clear_log_files,
    is_frida_debug_enabled, set_frida_debug, ENABLE_FRIDA_DEBUG
)

# ----------------------------------------------------------------------
# Frida Diagnostics Control Flag:
# Set to True manually in code or run application/executable with '/frida_debug'
# ----------------------------------------------------------------------
FRIDA_DEBUG = False
if FRIDA_DEBUG:
    set_frida_debug(True)

setup_logging(debug_enabled=True)
prog_logger = get_logger("progression")

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

# ----------------------------------------------------------------------
# Windows Application Desktop Toolbar (AppBar) API Registration
# ----------------------------------------------------------------------
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

# ----------------------------------------------------------------------
# Audio & Sound Alert Helpers
# ----------------------------------------------------------------------
def ensure_default_sounds():
    """Generates default alert WAV files if missing."""
    import wave, math, struct
    os.makedirs("sound", exist_ok=True)
    
    pk_path = os.path.join("sound", "alert.wav")
    if not os.path.exists(pk_path):
        try:
            sample_rate = 22050
            duration = 0.4
            n_samples = int(sample_rate * duration)
            with wave.open(pk_path, 'wb') as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(sample_rate)
                for i in range(n_samples):
                    freq = 880 if (i / sample_rate) < 0.2 else 440
                    t = i / sample_rate
                    val = int(16000 * math.sin(2 * math.pi * freq * t))
                    f.writeframesraw(struct.pack('<h', val))
        except Exception as e:
            print(f"[M59-SOUND] Could not generate alert.wav: {e}", flush=True)

    tell_path = os.path.join("sound", "dm_chime.wav")
    if not os.path.exists(tell_path) and not os.path.exists(os.path.join("sound", "dm_chime.mp3")):
        try:
            sample_rate = 22050
            duration = 0.35
            n_samples = int(sample_rate * duration)
            with wave.open(tell_path, 'wb') as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(sample_rate)
                for i in range(n_samples):
                    t = i / sample_rate
                    if t < 0.1: freq = 523.25
                    elif t < 0.2: freq = 659.25
                    else: freq = 783.99
                    val = int(15000 * math.sin(2 * math.pi * freq * t) * (1.0 - t/duration))
                    f.writeframesraw(struct.pack('<h', val))
        except Exception as e:
            print(f"[M59-SOUND] Could not generate dm_chime.wav: {e}", flush=True)

def play_audio_file(filepath):
    """Plays audio file or Windows system sound alias asynchronously."""
    try:
        if not filepath:
            return
        if filepath.startswith("System"):
            if sys.platform == 'win32':
                try:
                    import winsound
                    winsound.PlaySound(filepath, winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                    return
                except Exception:
                    pass
        
        target = filepath
        if not os.path.isabs(target):
            cwd_p = os.path.join(os.getcwd(), target)
            if os.path.exists(cwd_p):
                target = cwd_p
            else:
                alt_p = os.path.join("sound", os.path.basename(target))
                if os.path.exists(alt_p):
                    target = alt_p

        if sys.platform == 'win32':
            import ctypes
            try:
                ctypes.windll.winmm.mciSendStringW('close m59_audio', None, 0, None)
                res = ctypes.windll.winmm.mciSendStringW(f'open "{target}" alias m59_audio', None, 0, None)
                if res != 0:
                    import winsound
                    winsound.PlaySound(target, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                else:
                    ctypes.windll.winmm.mciSendStringW('play m59_audio', None, 0, None)
                return
            except Exception:
                pass

            try:
                import winsound
                winsound.PlaySound(target, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            except Exception:
                pass
    except Exception as ex:
        print(f"[M59-SOUND] Audio playback error: {ex}", flush=True)

from m59_utils import GAME_EXE, get_safe_name, find_game_hwnd, resource_path
from m59_time import get_game_time, format_game_time
from m59_tracker import SessionTracker
from m59_combat import CombatMonitor
from m59_bank import BankManager
from m59_lifecycle import InstanceManager
from m59_scraper import capture_identity, cycle_tabs_and_scrape, MemoryReader, get_blakgraph_stats, get_text_from_hwnd
from m59_wholist import WhoListMonitor
from m59_inventory import InventoryScraper, process_inventory
from m59_vault import perform_vault_scan, send_chat_command
from m59_commalias import parse_config_ini
from m59_gps import GPSManager
from m59_calculator import SchoolCalculator
from m59_spells import SpellManager
from m59_uw_node import UWNodeSolverWidget
import keyboard
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QListWidget, QListWidgetItem,
    QTableWidget, QTableWidgetItem, QFrame, QSplitter, QStackedWidget, QTabWidget, QTabBar,
    QHeaderView, QProgressBar, QTextEdit, QFileDialog, QSlider, QSpinBox, QScrollArea, QGroupBox,
    QSplashScreen, QSizePolicy, QComboBox, QDialog, QCheckBox, QFormLayout, QMessageBox, QAbstractItemView,
    QCompleter, QTreeWidget, QTreeWidgetItem, QSizeGrip, QStyleOptionComboBox, QStyle, QMenu
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QSize, QMimeData, QPoint, QRect
from PySide6.QtGui import (
    QFont, QIcon, QColor, QTextCursor, QPixmap, QImage, QDrag, QPainter, QPen, QBrush,
    QGuiApplication, QLinearGradient, QRadialGradient, QConicalGradient, QGradient
)

# ----------------------------------------------------------------------
# PK / PvP Alert Red Box Overlay Window around Game Client
# ----------------------------------------------------------------------
class PKFrame(QWidget):
    """Overlay window that flashes a high-visibility red border box around the Meridian 59 game client window or active screen."""
    def __init__(self, target_hwnd=None, dashboard=None):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowTransparentForInput)
        self.target_hwnd = target_hwnd
        self.dashboard = dashboard
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("background: transparent;")

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide_bars)

        self.dock_timer = QTimer(self)
        self.dock_timer.setInterval(50)
        self.dock_timer.timeout.connect(self.update_docking)

    def set_target_hwnd(self, hwnd):
        self.target_hwnd = hwnd

    def get_target_hwnd(self):
        if self.target_hwnd and win32gui and win32gui.IsWindow(self.target_hwnd):
            return self.target_hwnd
        if self.dashboard and getattr(self.dashboard, 'main_hwnd', None):
            dh = self.dashboard.main_hwnd
            if win32gui and win32gui.IsWindow(dh):
                return dh
        if sys.platform == 'win32':
            pid = getattr(self.dashboard, 'target_pid', None) if self.dashboard else None
            if pid:
                hwnd = find_game_hwnd(pid)
                if hwnd and win32gui and win32gui.IsWindow(hwnd):
                    return hwnd
            try:
                def _find_m59_win(h, extra):
                    if win32gui.IsWindowVisible(h):
                        t = win32gui.GetWindowText(h)
                        if "meridian" in t.lower():
                            extra.append(h)
                            return False
                    return True
                res = []
                win32gui.EnumWindows(_find_m59_win, res)
                if res:
                    return res[0]
            except Exception:
                pass
        return None

    def get_game_viewport_rect(self, hwnd):
        """Returns (x, y, w, h) of the exact game client rendering area in physical screen coordinates."""
        if not hwnd or sys.platform != 'win32' or not win32gui or not win32gui.IsWindow(hwnd):
            return None
        try:
            # 1. Primary: Use ClientToScreen + GetClientRect for exact in-game viewport area
            tl = win32gui.ClientToScreen(hwnd, (0, 0))
            cr = win32gui.GetClientRect(hwnd)
            cw = cr[2] - cr[0]
            ch = cr[3] - cr[1]
            if cw > 50 and ch > 50:
                return (tl[0], tl[1], cw, ch)
        except Exception:
            pass

        # 2. Secondary: DWM Extended Frame Bounds (removes invisible Windows 10/11 drop shadows)
        try:
            import ctypes
            from ctypes import wintypes
            class RECT(ctypes.Structure):
                _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                            ('right', ctypes.c_long), ('bottom', ctypes.c_long)]
            r = RECT()
            res = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(hwnd), wintypes.DWORD(9), ctypes.byref(r), ctypes.sizeof(r)
            )
            if res == 0:
                dw = r.right - r.left
                dh = r.bottom - r.top
                if dw > 50 and dh > 50:
                    return (r.left, r.top, dw, dh)
        except Exception:
            pass

        # 3. Fallback: GetWindowRect
        try:
            rect = win32gui.GetWindowRect(hwnd)
            return (rect[0], rect[1], max(0, rect[2] - rect[0]), max(0, rect[3] - rect[1]))
        except Exception:
            return None

    def update_docking(self):
        hwnd = self.get_target_hwnd()
        if not hwnd or not self.isVisible():
            return
        if win32gui and win32gui.IsIconic(hwnd):
            self.hide()
            return
        rect = self.get_game_viewport_rect(hwnd)
        if rect:
            x, y, w, h = rect
            try:
                my_hwnd = int(self.winId())
                if win32con:
                    win32gui.SetWindowPos(
                        my_hwnd, 0, x, y, w, h,
                        win32con.SWP_NOSIZE if (w == self.width() and h == self.height()) else 0 |
                        win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER
                    )
            except Exception:
                pass

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        pen_width = 8
        pen = QPen(QColor(239, 68, 68, 240))  # Vivid red #ef4444
        pen.setWidth(pen_width)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        half = pen_width // 2
        painter.drawRect(half, half, self.width() - pen_width, self.height() - pen_width)

    def flash(self, duration=5):
        try:
            hwnd = self.get_target_hwnd()
            x, y, w, h = 0, 0, 0, 0

            if hwnd and sys.platform == 'win32' and win32gui and win32gui.IsWindow(hwnd):
                self.target_hwnd = hwnd
                rect_tuple = self.get_game_viewport_rect(hwnd)
                if rect_tuple:
                    x, y, w, h = rect_tuple

            # Fallback if no game HWND or non-Windows / test preview mode
            if w <= 0 or h <= 0:
                screen = QApplication.primaryScreen()
                if screen:
                    geom = screen.geometry()
                    w = min(1024, int(geom.width() * 0.8))
                    h = min(768, int(geom.height() * 0.8))
                    x = geom.x() + (geom.width() - w) // 2
                    y = geom.y() + (geom.height() - h) // 2
                else:
                    w, h = 800, 600
                    x, y = 100, 100

            if w > 0 and h > 0:
                self.show()
                my_hwnd = int(self.winId())
                if hwnd and sys.platform == 'win32' and win32gui and win32gui.IsWindow(hwnd):
                    try:
                        if win32con:
                            # Attach ownership to game window so it lives at same Z-level as game/buttons/chat
                            win32gui.SetWindowLong(my_hwnd, win32con.GWL_HWNDPARENT, hwnd)
                            win32gui.SetWindowPos(
                                my_hwnd, 0, x, y, w, h,
                                win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW
                            )
                    except Exception as err:
                        print(f"[PKFrame] SetWindowPos error: {err}", flush=True)
                        self.setGeometry(x, y, w, h)
                else:
                    self.setGeometry(x, y, w, h)

                self.raise_()
                self.update()
                print(f"[PKFrame] Flashing red box overlay over game client at ({x}, {y}, {w}x{h}) for {duration}s", flush=True)
                self.dock_timer.start()
                self.hide_timer.start(int(duration * 1000))
        except Exception as e:
            print(f"[PKFrame] Error flashing red box overlay: {e}", flush=True)

    def hide_bars(self):
        self.dock_timer.stop()
        self.hide()

    def closeEvent(self, event):
        if hasattr(self, 'dock_timer') and self.dock_timer:
            self.dock_timer.stop()
        if hasattr(self, 'hide_timer') and self.hide_timer:
            self.hide_timer.stop()
        event.accept()

def pil_image_to_qpixmap(pil_img):
    """Converts a PIL RGBA Image to a PySide6 QPixmap."""
    if pil_img is None:
        return None
    try:
        if hasattr(pil_img, 'mode') and pil_img.mode != "RGBA":
            pil_img = pil_img.convert("RGBA")
        data = pil_img.tobytes("raw", "RGBA")
        qimg = QImage(data, pil_img.width, pil_img.height, QImage.Format_RGBA8888)
        return QPixmap.fromImage(qimg)
    except Exception as e:
        print(f"[BGF-ERR] Error converting PIL image to QPixmap: {e}", flush=True)
        return None

# ----------------------------------------------------------------------
# Floating Action Button Widget (Sticks & Docks to Game UI)
# ----------------------------------------------------------------------
class QtFloatingHotkeyButton(QWidget):
    def __init__(self, alias_name, command1, send_enter=True, alias_dict=None, parent=None, target_hwnd=None, dashboard=None, x_offset=30, y_offset=60):
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.dashboard = dashboard
        self.target_hwnd = target_hwnd
        self.alias_name = alias_name
        self.command1 = command1
        self.send_enter = send_enter
        self.alias_dict = alias_dict
        self.setWindowTitle(f"Macro: {alias_name}")
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        self.offset_x = x_offset
        self.offset_y = y_offset
        self.drag_position = QPoint()
        self.is_dragging = False

        self.setObjectName("FloatContainer")

        self.setStyleSheet("""
            QWidget#FloatContainer {
                background-color: #0f172a;
                color: #f8fafc;
                border: 1px solid #64748b;
                border-radius: 6px;
            }
            QLabel#Grip {
                color: #94a3b8;
                font-weight: bold;
                font-size: 11px;
                padding: 0 2px;
            }
            QPushButton#ActionBtn {
                background-color: #475569;
                color: #ffffff;
                font-weight: 800;
                border: none;
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 11px;
            }
            QPushButton#ActionBtn:hover {
                background-color: #94a3b8;
                color: #0f172a;
            }
            QPushButton#CloseBtn {
                background-color: #7f1d1d;
                color: #ffffff;
                font-weight: 800;
                border: none;
                border-radius: 4px;
                padding: 2px 5px;
                font-size: 10px;
            }
            QPushButton#CloseBtn:hover {
                background-color: #dc2626;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 3, 5, 3)
        layout.setSpacing(5)

        # Grip
        self.grip = QLabel("::")
        self.grip.setObjectName("Grip")
        self.grip.setCursor(Qt.SizeAllCursor)
        layout.addWidget(self.grip)

        # Action Button
        self.act_btn = QPushButton(alias_name)
        self.act_btn.setObjectName("ActionBtn")
        self.act_btn.clicked.connect(self.execute_macro)
        layout.addWidget(self.act_btn)

        # Close Button
        close_btn = QPushButton("✕")
        close_btn.setObjectName("CloseBtn")
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

        self.adjustSize()
        self.init_docking()

        self.dock_timer = QTimer(self)
        self.dock_timer.setInterval(50)
        self.dock_timer.timeout.connect(self.check_docking)
        self.dock_timer.start()

    def get_target_hwnd(self):
        if self.target_hwnd and win32gui and win32gui.IsWindow(self.target_hwnd):
            return self.target_hwnd
        if self.dashboard and getattr(self.dashboard, 'main_hwnd', None):
            dh = self.dashboard.main_hwnd
            if win32gui and win32gui.IsWindow(dh):
                return dh
        return None

    def init_docking(self):
        target = self.get_target_hwnd()
        if target and win32gui and win32gui.IsWindow(target):
            try:
                rect = win32gui.GetWindowRect(target)
                target_x = rect[0] + self.offset_x
                target_y = rect[1] + self.offset_y
                hwnd = int(self.winId())
                if win32gui and win32con:
                    win32gui.SetWindowPos(hwnd, 0, target_x, target_y, 0, 0,
                                         win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER)
                    win32gui.SetWindowLong(hwnd, win32con.GWL_HWNDPARENT, target)
            except Exception as ex:
                print(f"Hotkey button docking init error: {ex}")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = True
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            new_pos = event.globalPosition().toPoint() - self.drag_position
            self.move(new_pos)
            target = self.get_target_hwnd()
            if target and win32gui and win32gui.IsWindow(target):
                try:
                    my_hwnd = int(self.winId())
                    my_rect = win32gui.GetWindowRect(my_hwnd)
                    target_rect = win32gui.GetWindowRect(target)
                    self.offset_x = my_rect[0] - target_rect[0]
                    self.offset_y = my_rect[1] - target_rect[1]
                except Exception:
                    pass
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = False
            if self.dashboard:
                aliases = self.dashboard.load_commaliases()
                updated = False
                for alias in aliases:
                    if alias.get('name') == self.alias_name:
                        alias['x_offset'] = self.offset_x
                        alias['y_offset'] = self.offset_y
                        updated = True
                        break
                if updated:
                    self.dashboard.save_commaliases(aliases, rebuild_buttons=False)
            event.accept()

    def check_docking(self):
        target = self.get_target_hwnd()
        if not target or not win32gui or not win32gui.IsWindow(target):
            if self.isVisible():
                self.hide()
            return

        if win32gui.IsIconic(target):
            if self.isVisible():
                self.hide()
            return

        fg = win32gui.GetForegroundWindow()
        dash_hwnd = None
        if self.dashboard and hasattr(self.dashboard, 'winId'):
            try:
                dash_hwnd = int(self.dashboard.winId())
            except Exception:
                pass
        my_hwnd = int(self.winId())

        is_game_active = False
        if self.isActiveWindow() or self.underMouse() or getattr(self, 'is_dragging', False):
            is_game_active = True
        elif self.dashboard and hasattr(self.dashboard, 'isActiveWindow') and self.dashboard.isActiveWindow():
            is_game_active = True
        elif fg in (target, dash_hwnd, my_hwnd):
            is_game_active = True
        elif fg:
            try:
                if win32process:
                    _, fg_pid = win32process.GetWindowThreadProcessId(fg)
                    if fg_pid == os.getpid():
                        is_game_active = True
                    else:
                        _, target_pid = win32process.GetWindowThreadProcessId(target)
                        if fg_pid == target_pid:
                            is_game_active = True

                if not is_game_active:
                    cur = fg
                    for _ in range(6):
                        if not cur or cur == 0:
                            break
                        if cur in (target, dash_hwnd, my_hwnd):
                            is_game_active = True
                            break
                        cur = win32gui.GetParent(cur)
            except Exception:
                pass

        if not is_game_active:
            if self.isVisible():
                self.hide()
            return

        if not self.isVisible():
            self.show()

        if not getattr(self, 'is_dragging', False):
            try:
                rect = win32gui.GetWindowRect(target)
                target_x = rect[0] + self.offset_x
                target_y = rect[1] + self.offset_y
                my_rect = win32gui.GetWindowRect(my_hwnd)
                if my_rect[0] != target_x or my_rect[1] != target_y:
                    win32gui.SetWindowPos(my_hwnd, 0, target_x, target_y, 0, 0,
                                         win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER)
            except Exception:
                pass

    def execute_macro(self):
        target = self.get_target_hwnd()
        if not self.command1:
            return
        def _run():
            try:
                if target:
                    send_chat_command(target, self.command1, send_enter=self.send_enter)
            except Exception as ex:
                print(f"Hotkey execution failed: {ex}")
        threading.Thread(target=_run, daemon=True).start()

    def closeEvent(self, event):
        if hasattr(self, 'dock_timer') and self.dock_timer:
            self.dock_timer.stop()
        if self.dashboard and not getattr(self.dashboard, '_is_shutting_down', False):
            try:
                aliases = self.dashboard.load_commaliases()
                updated = False
                for alias in aliases:
                    if alias.get('name') == self.alias_name:
                        alias['show_float'] = False
                        updated = True
                        break
                if updated:
                    self.dashboard.save_commaliases(aliases, rebuild_buttons=False)
            except Exception as ex:
                print(f"[M59-HOTKEY] Error updating alias float state on user close: {ex}", flush=True)
        event.accept()


# ----------------------------------------------------------------------
# Morph Creature Form List & Data Engine
# ----------------------------------------------------------------------
MORPH_CREATURES_FALLBACK = [
    {"level": 25, "en_name": "baby spider", "ko_catan": "imixkinich", "description": "Smaller and weaker than his larger kin, this baby spider"},
    {"level": 25, "en_name": "mummy", "ko_catan": "napleoc", "description": "This evil being has been brought to life by dark magics of unknown origin."},
    {"level": 30, "en_name": "centipede", "ko_catan": "puuckinich", "description": "Bright red plates make up the exoskeleton of this"},
    {"level": 30, "en_name": "giant rat", "ko_catan": "napyijoa", "description": "The giant rat bares its yellow teeth in defiance.  A"},
    {"level": 30, "en_name": "shadow mummy", "ko_catan": "teotnapleoc", "description": "This evil being has been brought to life by dark magics of unknown origin."},
    {"level": 35, "en_name": "groundworm larva", "ko_catan": "imixslithic", "description": "A younger form of its mature relatives, the larva is a bit weaker than"},
    {"level": 35, "en_name": "slime", "ko_catan": "kinachot", "description": "A mass of quivering goo, the slime inches forward"},
    {"level": 40, "en_name": "ant", "ko_catan": "yokinich", "description": "Snapping pincers, bloody from the ant's last meal, lash"},
    {"level": 40, "en_name": "spectral mummy", "ko_catan": "kosnapleoc", "description": "This poor creature was once a person who was mummified in an ancient ritual and put"},
    {"level": 45, "en_name": "orc", "ko_catan": "utomca", "description": "This foul servant of Qor, body covered in filth, towers"},
    {"level": 50, "en_name": "diseased tree", "ko_catan": "teotezmecya", "description": "Feeding upon the dredges of the tainted soil in the land, this"},
    {"level": 50, "en_name": "dusk rat", "ko_catan": "teotnapyijoa", "description": "The air around the rat is dark and thick with evil.  The smell of death"},
    {"level": 50, "en_name": "fungus beast", "ko_catan": "puucmecmoch", "description": "This strange creature, made up of tender, pulpy flesh,"},
    {"level": 50, "en_name": "living tree", "ko_catan": "tezmecya", "description": "A primordial spirit flows through the dark branches of"},
    {"level": 50, "en_name": "rebel soldier", "ko_catan": "moch", "description": "This soldier proudly bears the colors of the Jasper militia."},
    {"level": 50, "en_name": "soldier of the Duke's army", "ko_catan": "moch", "description": "This soldier proudly bears the colors of the Duke."},
    {"level": 50, "en_name": "soldier of the Princess' army", "ko_catan": "moch", "description": "This soldier proudly bears the colors of the Princess."},
    {"level": 50, "en_name": "spider", "ko_catan": "teotkauilkinich", "description": "This strangely delicate creature moves with stealth and strikes with deadly precision."},
    {"level": 55, "en_name": "giant scorpion", "ko_catan": "kinkauikinich", "description": "The scorpion's deadly stinger rises high in the air"},
    {"level": 55, "en_name": "zombie", "ko_catan": "ixleoc", "description": "Unfettered evil has brought the dead back to life in the"},
    {"level": 60, "en_name": "battered skeleton", "ko_catan": "cha'oleoc", "description": "A few scraps of rotten flesh cling to this collection of human"},
    {"level": 60, "en_name": "necromancer", "ko_catan": "moch", "description": "Transfixed under the unearthly stare of this foul being, your mind skitters"},
    {"level": 60, "en_name": "snow rat", "ko_catan": "shonapyijoa", "description": "This is the ice-born cousin of the mainland vermin.  It is"},
    {"level": 65, "en_name": "mutant ant", "ko_catan": "kawilkinich", "description": "The exoskeleton of the mutant ant is thick and hard and"},
    {"level": 75, "en_name": "black spider", "ko_catan": "na'arkinich", "description": "Once thought to be a creature made up to frighten children, the black spider"},
    {"level": 75, "en_name": "skeleton", "ko_catan": "chaleoc", "description": "A few scraps of rotten flesh cling to this collection of"},
    {"level": 80, "en_name": "cave orc", "ko_catan": "utom", "description": "This dirty orc grunt wanders through the caves hungrily in search"},
    {"level": 80, "en_name": "orc wizard", "ko_catan": "utomya", "description": "Harnessing dark powers from deep underground, the Orc Wizards"},
    {"level": 90, "en_name": "troll", "ko_catan": "humoch", "description": "Covered with knots of lumpy flesh, the troll has"},
    {"level": 100, "en_name": "guard cow", "ko_catan": "tanahyijoa", "description": "This is the vicious guardcow, which punishes all wrongdoers."},
    {"level": 100, "en_name": "peet-seeeep avar shaman", "ko_catan": "avarya", "description": "This is a shaman of the Peet-Seeeep clan."},
    {"level": 100, "en_name": "tusked skeleton", "ko_catan": "ha'chaleoc", "description": "A few scraps of rotten flesh cling to this collection of human"},
    {"level": 105, "en_name": "lupogg", "ko_catan": "tez", "description": "This underwater denizen, although hideous and brutish, is rumored to be"},
    {"level": 115, "en_name": "orc pit boss", "ko_catan": "koutom", "description": "In order to gain the ominous title and station of Pit Boss,"},
    {"level": 120, "en_name": "narthyl worm", "ko_catan": "tepna'arthyl", "description": "In spite of its otherworldy hideousness, the narthyl worm's lithe movements are"},
    {"level": 120, "en_name": "peet-seeeep avar warrior", "ko_catan": "avar", "description": "Fierce warriors, these flightless bird men demand caution as you"},
    {"level": 130, "en_name": "daemon skeleton", "ko_catan": "kochaleoc", "description": "The sight alone of this abomination of nature is often enough to"},
    {"level": 130, "en_name": "groundworm queen", "ko_catan": "koslithic", "description": "The largest of the groundworms, the queen is a feared monster."},
    {"level": 135, "en_name": "peet-seeeep avar chieftain", "ko_catan": "koavar", "description": "Known for their noble grace, the Peet-Seeeep tribe"},
    {"level": 150, "en_name": "mollusk creature", "ko_catan": "kolisith", "description": "A recent arrival to the lands, this giant creature is rather"},
    {"level": 150, "en_name": "thrasher", "ko_catan": "teotixleoc", "description": "The powerful smell of rotted flesh and embalming herbs assault your"},
    {"level": 150, "en_name": "ve'xeochicatl", "ko_catan": "ve'xeo'chicatl", "description": "Before you is a ve'xeochicatl, a fearsome monster given life by the chaotic"},
    {"level": 165, "en_name": "queen spider", "ko_catan": "kokinich", "description": "The huge egg sac of the queen spider makes her slow and heavy, but"},
    {"level": 170, "en_name": "ro'xeochicatl", "ko_catan": "ro'xeo'chicatl", "description": "Before you is a ro'xeochicatl, a fearsome monster given life by the chaotic"},
    {"level": 190, "en_name": "ma'xeochicatl", "ko_catan": "ma'xeo'chicatl", "description": "Before you is a ma'xeochicatl, a fearsome monster given life by the chaotic"},
    {"level": 200, "en_name": "cow", "ko_catan": "nahyijoa", "description": "Alchemists and farmers endeavored to breed the perfect cow for sweet"},
    {"level": 200, "en_name": "ghost of Far'Nohl", "ko_catan": "far'nohl kotezleoc", "description": "Rags and rotten flesh hang on the bones of this magical"},
    {"level": 200, "en_name": "shadowbeast", "ko_catan": "teotkriipa", "description": "A magical abomination that hybrids the jungle kriipa with some quasi-substantial"},
    {"level": 200, "en_name": "te'xeochicatl", "ko_catan": "te'xeo'chicatl", "description": "Before you is a te'xeochicatl, a fearsome monster given life by the chaotic"}
]

def get_morph_creatures():
    """Returns list of morphable creatures from settings/morph_creatures.csv or fallback dataset."""
    p = resource_path("settings/morph_creatures.csv")
    if os.path.exists(p):
        try:
            creatures = []
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("en_name") and row.get("ko_catan"):
                        creatures.append({
                            "level": int(row.get("level", 0)) if str(row.get("level", "0")).strip().isdigit() else 0,
                            "en_name": row.get("en_name", "").strip(),
                            "ko_catan": row.get("ko_catan", "").strip(),
                            "description": row.get("description", "").strip()
                        })
            if creatures:
                return sorted(creatures, key=lambda x: (x["level"], x["en_name"]))
        except Exception:
            pass
    return sorted(MORPH_CREATURES_FALLBACK, key=lambda x: (x["level"], x["en_name"]))


# ----------------------------------------------------------------------
# Floating Elude Teleport Bar Widget (Sticks & Docks to Game UI)
# ----------------------------------------------------------------------
class QtFloatingEludeBar(QWidget):
    def __init__(self, parent=None, target_hwnd=None, dashboard=None):
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.dashboard = dashboard
        self.target_hwnd = target_hwnd
        self.setWindowTitle("M59 Elude")
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        self.offset_x = 20
        self.offset_y = 50
        if self.dashboard:
            try:
                s = self.dashboard.load_gui_settings()
                if 'elusion_x_offset' in s and 'elusion_y_offset' in s:
                    self.offset_x = s['elusion_x_offset']
                    self.offset_y = s['elusion_y_offset']
            except Exception:
                pass

        self.drag_position = QPoint()
        self.is_dragging = False

        self.setObjectName("EludeFloatContainer")

        self.setStyleSheet("""
            QWidget#EludeFloatContainer {
                background-color: #0f172a;
                color: #f8fafc;
                border: 1px solid #94a3b8;
                border-radius: 6px;
            }
            QLabel#Grip {
                color: #c084fc;
                font-weight: bold;
                font-size: 12px;
                padding: 0 3px;
            }
            QComboBox {
                background-color: #1e293b;
                border: 1px solid #94a3b8;
                color: #f8fafc;
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 11px;
            }
            QComboBox QAbstractItemView {
                background-color: #0f172a;
                color: #f8fafc;
                selection-background-color: #9333ea;
                selection-color: #ffffff;
                border: 1px solid #94a3b8;
                padding: 4px;
                outline: none;
            }
            QComboBox QAbstractItemView::item {
                color: #f8fafc;
                min-height: 22px;
                padding: 4px 6px;
            }
            QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {
                background-color: #9333ea;
                color: #ffffff;
            }
            QPushButton#CastBtn {
                background-color: #9333ea;
                color: #ffffff;
                font-weight: 800;
                border: none;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 11px;
            }
            QPushButton#CastBtn:hover {
                background-color: #94a3b8;
            }
            QPushButton#CloseBtn {
                background-color: #7f1d1d;
                color: #ffffff;
                font-weight: 800;
                border: none;
                border-radius: 4px;
                padding: 2px 5px;
                font-size: 10px;
            }
            QPushButton#CloseBtn:hover {
                background-color: #dc2626;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        # Drag grip
        self.grip = QLabel("::")
        self.grip.setObjectName("Grip")
        self.grip.setCursor(Qt.SizeAllCursor)
        layout.addWidget(self.grip)

        # Location combo box
        self.combo = QComboBox()
        self.refresh_locations()
        layout.addWidget(self.combo)

        # Cast button
        cast_btn = QPushButton("Cast")
        cast_btn.setObjectName("CastBtn")
        cast_btn.clicked.connect(self.do_elude)
        layout.addWidget(cast_btn)

        # Close button
        close_btn = QPushButton("✕")
        close_btn.setObjectName("CloseBtn")
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

        self.adjustSize()
        self.init_docking()

        self.dock_timer = QTimer(self)
        self.dock_timer.setInterval(50)
        self.dock_timer.timeout.connect(self.check_docking)
        self.dock_timer.start()

    def get_target_hwnd(self):
        if self.target_hwnd and win32gui and win32gui.IsWindow(self.target_hwnd):
            return self.target_hwnd
        if self.dashboard and getattr(self.dashboard, 'main_hwnd', None):
            dh = self.dashboard.main_hwnd
            if win32gui and win32gui.IsWindow(dh):
                return dh
        return None

    def refresh_locations(self):
        locations = [
            "The Streets of Tos",
            "Marion",
            "South Barloque",
            "Cor Noth",
            "East Jasper",
            "The Aerie Guest House",
            "Guild Hall"
        ]
        if self.dashboard and hasattr(self.dashboard, 'guildhall_name_val'):
            gh = getattr(self.dashboard, 'guildhall_name_val', '').strip()
            if gh and gh not in locations:
                locations.append(gh)
        self.combo.clear()
        self.combo.addItems(locations)

    def init_docking(self):
        target = self.get_target_hwnd()
        if target and win32gui and win32gui.IsWindow(target):
            try:
                rect = win32gui.GetWindowRect(target)
                target_x = rect[0] + self.offset_x
                target_y = rect[1] + self.offset_y
                hwnd = int(self.winId())
                if win32gui and win32con:
                    win32gui.SetWindowPos(hwnd, 0, target_x, target_y, 0, 0,
                                         win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER)
                    win32gui.SetWindowLong(hwnd, win32con.GWL_HWNDPARENT, target)
            except Exception as ex:
                print(f"Elude bar docking init error: {ex}")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = True
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            new_pos = event.globalPosition().toPoint() - self.drag_position
            self.move(new_pos)
            target = self.get_target_hwnd()
            if target and win32gui and win32gui.IsWindow(target):
                try:
                    my_hwnd = int(self.winId())
                    my_rect = win32gui.GetWindowRect(my_hwnd)
                    target_rect = win32gui.GetWindowRect(target)
                    self.offset_x = my_rect[0] - target_rect[0]
                    self.offset_y = my_rect[1] - target_rect[1]
                except Exception:
                    pass
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = False
            if self.dashboard:
                s = self.dashboard.load_gui_settings()
                s['elusion_x_offset'] = self.offset_x
                s['elusion_y_offset'] = self.offset_y
                s['elusion_geometry'] = f"{self.width()}x{self.height()}+{self.offset_x}+{self.offset_y}"
                self.dashboard.save_gui_settings(s)
            event.accept()

    def check_docking(self):
        target = self.get_target_hwnd()
        if not target or not win32gui or not win32gui.IsWindow(target):
            if self.isVisible():
                self.hide()
            return

        if win32gui.IsIconic(target):
            if self.isVisible():
                self.hide()
            return

        fg = win32gui.GetForegroundWindow()
        dash_hwnd = None
        if self.dashboard and hasattr(self.dashboard, 'winId'):
            try:
                dash_hwnd = int(self.dashboard.winId())
            except Exception:
                pass
        my_hwnd = int(self.winId())

        is_game_active = False
        if self.isActiveWindow() or self.underMouse() or getattr(self, 'is_dragging', False):
            is_game_active = True
        elif self.dashboard and hasattr(self.dashboard, 'isActiveWindow') and self.dashboard.isActiveWindow():
            is_game_active = True
        elif fg in (target, dash_hwnd, my_hwnd):
            is_game_active = True
        elif fg:
            try:
                if win32process:
                    _, fg_pid = win32process.GetWindowThreadProcessId(fg)
                    if fg_pid == os.getpid():
                        is_game_active = True
                    else:
                        _, target_pid = win32process.GetWindowThreadProcessId(target)
                        if fg_pid == target_pid:
                            is_game_active = True

                if not is_game_active:
                    cur = fg
                    for _ in range(6):
                        if not cur or cur == 0:
                            break
                        if cur in (target, dash_hwnd, my_hwnd):
                            is_game_active = True
                            break
                        cur = win32gui.GetParent(cur)
            except Exception:
                pass

        if not is_game_active:
            if self.isVisible():
                self.hide()
            return

        if not self.isVisible():
            self.show()

        if not getattr(self, 'is_dragging', False):
            try:
                rect = win32gui.GetWindowRect(target)
                target_x = rect[0] + self.offset_x
                target_y = rect[1] + self.offset_y
                my_rect = win32gui.GetWindowRect(my_hwnd)
                if my_rect[0] != target_x or my_rect[1] != target_y:
                    win32gui.SetWindowPos(my_hwnd, 0, target_x, target_y, 0, 0,
                                         win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER)
            except Exception:
                pass

    def do_elude(self):
        loc = self.combo.currentText()
        if not loc:
            return
        phrase = 'say I wish to travel to {loc}.'
        if self.dashboard and hasattr(self.dashboard, 'shortcut_phrase_combo'):
            phrase = self.dashboard.shortcut_phrase_combo.currentText()
        formatted = phrase.replace("{loc}", loc)
        target = self.get_target_hwnd()

        if self.dashboard and hasattr(self.dashboard, 'cast_spell_with_trance'):
            self.dashboard.cast_spell_with_trance("elusion", formatted, target_hwnd=target)
        else:
            def _run():
                try:
                    if target:
                        send_chat_command(target, 'cast "elusion"')
                except Exception as ex:
                    print(f"Elude macro execution failed: {ex}")
            threading.Thread(target=_run, daemon=True).start()

    def closeEvent(self, event):
        if hasattr(self, 'dock_timer') and self.dock_timer:
            self.dock_timer.stop()
        if self.dashboard:
            if getattr(self.dashboard, 'active_elude_bar', None) == self:
                self.dashboard.active_elude_bar = None
            if not getattr(self.dashboard, '_is_shutting_down', False):
                try:
                    s = self.dashboard.load_gui_settings()
                    s['elude_bar_open'] = False
                    self.dashboard.save_gui_settings(s)
                except Exception as ex:
                    print(f"[M59-ELUDE] Error saving closed state: {ex}", flush=True)
        event.accept()


# ----------------------------------------------------------------------
# Floating Morph Bar Widget (Sticks & Docks to Game UI)
# ----------------------------------------------------------------------
class CompactMorphComboBox(QComboBox):
    """Compact morph selection dropdown that displays a shortened name with '...' when closed,
    expands to full width when dropped down, and displays the full creature name on hover / tooltip."""
    def __init__(self, parent=None, max_display_len=7):
        super().__init__(parent)
        self.max_display_len = max_display_len
        self.setFixedWidth(78)
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        # Ensure dropdown popup list expands to display full creature name & details
        self.view().setMinimumWidth(235)
        self.view().setTextElideMode(Qt.ElideNone)
        self.currentIndexChanged.connect(self._update_hover_tooltip)

    def _update_hover_tooltip(self, idx=None):
        if idx is None:
            idx = self.currentIndex()
        if idx < 0:
            return
        c_data = self.itemData(idx)
        if isinstance(c_data, dict):
            full_title = f"Lvl {c_data.get('level', '')} - {c_data.get('en_name', '').title()} ({c_data.get('ko_catan', '')})"
            self.setToolTip(full_title)
        else:
            self.setToolTip(self.itemText(idx))

    def paintEvent(self, event):
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)

        idx = self.currentIndex()
        c_data = self.itemData(idx)
        if isinstance(c_data, dict):
            en_name = c_data.get('en_name', '').strip().title()
        else:
            en_name = self.currentText().strip()

        # Display shortened name with ellipsis (...) to indicate length
        if len(en_name) > self.max_display_len:
            opt.currentText = en_name[:self.max_display_len - 1] + "..."
        elif en_name:
            opt.currentText = en_name

        p = QPainter(self)
        self.style().drawComplexControl(QStyle.CC_ComboBox, opt, p, self)
        self.style().drawControl(QStyle.CE_ComboBoxLabel, opt, p, self)


class QtFloatingMorphBar(QWidget):
    def __init__(self, parent=None, target_hwnd=None, dashboard=None):
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.dashboard = dashboard
        self.target_hwnd = target_hwnd
        self.setWindowTitle("M59 Morph")
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        self.offset_x = 20
        self.offset_y = 95
        if self.dashboard:
            try:
                s = self.dashboard.load_gui_settings()
                if 'morph_x_offset' in s and 'morph_y_offset' in s:
                    self.offset_x = s['morph_x_offset']
                    self.offset_y = s['morph_y_offset']
            except Exception:
                pass

        self.drag_position = QPoint()
        self.is_dragging = False

        self.setObjectName("MorphFloatContainer")

        self.setStyleSheet("""
            QWidget#MorphFloatContainer {
                background-color: #0f172a;
                color: #f8fafc;
                border: 1px solid #10b981;
                border-radius: 6px;
            }
            QLabel#Grip {
                color: #34d399;
                font-weight: bold;
                font-size: 12px;
                padding: 0 3px;
            }
            QComboBox {
                background-color: #1e293b;
                border: 1px solid #10b981;
                color: #f8fafc;
                border-radius: 4px;
                padding: 2px 4px;
                font-size: 11px;
                font-weight: 700;
                max-width: 80px;
                min-width: 74px;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 14px;
                border-left: 1px solid #10b981;
            }
            QComboBox QAbstractItemView {
                background-color: #0f172a;
                color: #f8fafc;
                selection-background-color: #059669;
                selection-color: #ffffff;
                border: 1px solid #10b981;
                padding: 4px;
                outline: none;
                min-width: 235px;
            }
            QComboBox QAbstractItemView::item {
                color: #f8fafc;
                background-color: #0f172a;
                min-height: 22px;
                padding: 4px 6px;
            }
            QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {
                background-color: #059669;
                color: #ffffff;
            }
            QPushButton#CastBtn {
                background-color: #059669;
                color: #ffffff;
                font-weight: 800;
                border: none;
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 11px;
            }
            QPushButton#CastBtn:hover {
                background-color: #10b981;
            }
            QPushButton#CloseBtn {
                background-color: #7f1d1d;
                color: #ffffff;
                font-weight: 800;
                border: none;
                border-radius: 4px;
                padding: 2px 5px;
                font-size: 10px;
            }
            QPushButton#CloseBtn:hover {
                background-color: #dc2626;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 3, 5, 3)
        layout.setSpacing(5)

        # Drag grip
        self.grip = QLabel("::")
        self.grip.setObjectName("Grip")
        self.grip.setCursor(Qt.SizeAllCursor)
        layout.addWidget(self.grip)

        # Compact creature combo box
        self.combo = CompactMorphComboBox()
        self.creatures_list = get_morph_creatures()
        self.refresh_creatures()
        layout.addWidget(self.combo)

        # Cast button
        cast_btn = QPushButton("Morph")
        cast_btn.setObjectName("CastBtn")
        cast_btn.clicked.connect(self.do_morph)
        layout.addWidget(cast_btn)

        # Close button
        close_btn = QPushButton("✕")
        close_btn.setObjectName("CloseBtn")
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

        self.adjustSize()
        self.init_docking()

        self.dock_timer = QTimer(self)
        self.dock_timer.setInterval(50)
        self.dock_timer.timeout.connect(self.check_docking)
        self.dock_timer.start()

    def refresh_creatures(self):
        self.combo.clear()
        for i, c in enumerate(self.creatures_list):
            display = f"Lvl {c['level']} - {c['en_name'].title()} ({c['ko_catan']})"
            self.combo.addItem(display, userData=c)
            self.combo.setItemData(i, display, Qt.ToolTipRole)
        self.combo._update_hover_tooltip()

    def get_target_hwnd(self):
        if self.target_hwnd and win32gui and win32gui.IsWindow(self.target_hwnd):
            return self.target_hwnd
        if self.dashboard and getattr(self.dashboard, 'main_hwnd', None):
            dh = self.dashboard.main_hwnd
            if win32gui and win32gui.IsWindow(dh):
                return dh
        return None

    def init_docking(self):
        target = self.get_target_hwnd()
        if target and win32gui and win32gui.IsWindow(target):
            try:
                rect = win32gui.GetWindowRect(target)
                target_x = rect[0] + self.offset_x
                target_y = rect[1] + self.offset_y
                hwnd = int(self.winId())
                if win32gui and win32con:
                    win32gui.SetWindowPos(hwnd, 0, target_x, target_y, 0, 0,
                                         win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER)
                    win32gui.SetWindowLong(hwnd, win32con.GWL_HWNDPARENT, target)
            except Exception as ex:
                print(f"Morph bar docking init error: {ex}")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = True
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            new_pos = event.globalPosition().toPoint() - self.drag_position
            self.move(new_pos)
            target = self.get_target_hwnd()
            if target and win32gui and win32gui.IsWindow(target):
                try:
                    my_hwnd = int(self.winId())
                    my_rect = win32gui.GetWindowRect(my_hwnd)
                    target_rect = win32gui.GetWindowRect(target)
                    self.offset_x = my_rect[0] - target_rect[0]
                    self.offset_y = my_rect[1] - target_rect[1]
                except Exception:
                    pass
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = False
            if self.dashboard:
                s = self.dashboard.load_gui_settings()
                s['morph_x_offset'] = self.offset_x
                s['morph_y_offset'] = self.offset_y
                s['morph_geometry'] = f"{self.width()}x{self.height()}+{self.offset_x}+{self.offset_y}"
                self.dashboard.save_gui_settings(s)
            event.accept()

    def check_docking(self):
        target = self.get_target_hwnd()
        if not target or not win32gui or not win32gui.IsWindow(target):
            if self.isVisible():
                self.hide()
            return

        if win32gui.IsIconic(target):
            if self.isVisible():
                self.hide()
            return

        fg = win32gui.GetForegroundWindow()
        dash_hwnd = None
        if self.dashboard and hasattr(self.dashboard, 'winId'):
            try:
                dash_hwnd = int(self.dashboard.winId())
            except Exception:
                pass
        my_hwnd = int(self.winId())

        is_game_active = False
        if self.isActiveWindow() or self.underMouse() or getattr(self, 'is_dragging', False):
            is_game_active = True
        elif self.dashboard and hasattr(self.dashboard, 'isActiveWindow') and self.dashboard.isActiveWindow():
            is_game_active = True
        elif fg in (target, dash_hwnd, my_hwnd):
            is_game_active = True
        elif fg:
            try:
                if win32process:
                    _, fg_pid = win32process.GetWindowThreadProcessId(fg)
                    if fg_pid == os.getpid():
                        is_game_active = True
                    else:
                        _, target_pid = win32process.GetWindowThreadProcessId(target)
                        if fg_pid == target_pid:
                            is_game_active = True
                if not is_game_active:
                    cur = fg
                    for _ in range(6):
                        if not cur or cur == 0:
                            break
                        if cur in (target, dash_hwnd, my_hwnd):
                            is_game_active = True
                            break
                        cur = win32gui.GetParent(cur)
            except Exception:
                pass

        if not is_game_active:
            if self.isVisible():
                self.hide()
            return

        if not self.isVisible():
            self.show()

        if not getattr(self, 'is_dragging', False):
            try:
                rect = win32gui.GetWindowRect(target)
                target_x = rect[0] + self.offset_x
                target_y = rect[1] + self.offset_y
                my_rect = win32gui.GetWindowRect(my_hwnd)
                if my_rect[0] != target_x or my_rect[1] != target_y:
                    win32gui.SetWindowPos(my_hwnd, 0, target_x, target_y, 0, 0,
                                         win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER)
            except Exception:
                pass

    def do_morph(self):
        c_data = self.combo.currentData()
        if not c_data or not isinstance(c_data, dict):
            idx = self.combo.currentIndex()
            if 0 <= idx < len(self.creatures_list):
                c_data = self.creatures_list[idx]
        if not c_data:
            return
        ko_name = c_data.get("ko_catan", "").strip()
        if not ko_name:
            return

        phrase = 'say "{name}"'
        if self.dashboard and hasattr(self.dashboard, 'morph_phrase_combo'):
            phrase = self.dashboard.morph_phrase_combo.currentText()
        formatted = phrase.replace("{name}", ko_name)

        target = self.get_target_hwnd()
        if self.dashboard and hasattr(self.dashboard, 'cast_spell_with_trance'):
            self.dashboard.cast_spell_with_trance("morph", formatted, target_hwnd=target)
        else:
            def _run():
                try:
                    if target:
                        send_chat_command(target, 'cast "morph"')
                except Exception as ex:
                    print(f"Morph macro execution failed: {ex}")
            threading.Thread(target=_run, daemon=True).start()

    def closeEvent(self, event):
        if hasattr(self, 'dock_timer') and self.dock_timer:
            self.dock_timer.stop()
        if self.dashboard:
            if getattr(self.dashboard, 'active_morph_bar', None) == self:
                self.dashboard.active_morph_bar = None
            if not getattr(self.dashboard, '_is_shutting_down', False):
                try:
                    s = self.dashboard.load_gui_settings()
                    s['morph_bar_open'] = False
                    self.dashboard.save_gui_settings(s)
                except Exception as ex:
                    print(f"[M59-MORPH] Error saving closed state: {ex}", flush=True)
        event.accept()


# ----------------------------------------------------------------------
# Floating Chat Box Widget (Anchors to Game, Overlay over in-game chat)
# ----------------------------------------------------------------------
class QtFloatingChatBox(QWidget):
    def __init__(self, parent=None, target_hwnd=None, dashboard=None):
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.dashboard = dashboard
        self.target_hwnd = target_hwnd
        self.setWindowTitle("M59 Floating Chatbox")
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        self.offset_x = 20
        self.offset_y = 350
        self.expanded_height = 240
        self.saved_width = 480
        self.is_rolled_up = False

        if self.dashboard:
            try:
                s = self.dashboard.load_gui_settings()
                if 'floating_chat_x_offset' in s and 'floating_chat_y_offset' in s:
                    self.offset_x = s['floating_chat_x_offset']
                    self.offset_y = s['floating_chat_y_offset']
                if 'floating_chat_width' in s and 'floating_chat_height' in s:
                    self.saved_width = max(280, s['floating_chat_width'])
                    self.expanded_height = max(160, s['floating_chat_height'])
                if 'floating_chat_rolled_up' in s:
                    self.is_rolled_up = s['floating_chat_rolled_up']
            except Exception:
                pass

        self.drag_position = QPoint()
        self.is_dragging = False
        self.active_channel = "all"

        self.setObjectName("FloatingChatContainer")

        self.setStyleSheet("""
            QWidget#FloatingChatContainer {
                background-color: rgba(3, 7, 18, 0.94);
                color: #f8fafc;
                border: 1px solid #0284c7;
                border-radius: 8px;
            }
            QFrame#TitleBar {
                background-color: rgba(15, 23, 42, 0.98);
                border-top-left-radius: 7px;
                border-top-right-radius: 7px;
                border-bottom: 1px solid #1e293b;
            }
            QLabel#Grip {
                color: #38bdf8;
                font-weight: 900;
                font-size: 13px;
                padding: 0 4px;
            }
            QLabel#TitleLabel {
                color: #f8fafc;
                font-weight: 800;
                font-size: 11px;
                letter-spacing: 0.5px;
            }
            QPushButton#RollBtn {
                background-color: #0369a1;
                color: #ffffff;
                font-weight: 800;
                border: none;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 10px;
            }
            QPushButton#RollBtn:hover {
                background-color: #0284c7;
            }
            QPushButton#CloseBtn {
                background-color: #7f1d1d;
                color: #ffffff;
                font-weight: 800;
                border: none;
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 10px;
            }
            QPushButton#CloseBtn:hover {
                background-color: #dc2626;
            }
            QPushButton.FloatFilterBtn {
                background-color: #1e293b;
                color: #94a3b8;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 10px;
                font-weight: 700;
            }
            QPushButton.FloatFilterBtn:hover {
                background-color: #334155;
                color: #f1f5f9;
            }
            QPushButton.FloatFilterBtn[active="true"] {
                background-color: #0284c7;
                color: #ffffff;
                border: 1px solid #38bdf8;
            }
            QTextEdit#FloatChatStream {
                background-color: rgba(3, 7, 18, 0.88);
                border: 1px solid #1e293b;
                border-radius: 6px;
                padding: 8px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11px;
                line-height: 1.4;
                color: #e2e8f0;
            }
            QLineEdit#FloatSearch {
                background-color: #0f172a;
                border: 1px solid #334155;
                border-radius: 4px;
                color: #f8fafc;
                padding: 2px 6px;
                font-size: 10px;
            }
        """)

        # Main Layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1. Custom Title Bar / Grip
        self.title_bar = QFrame()
        self.title_bar.setObjectName("TitleBar")
        tb_layout = QHBoxLayout(self.title_bar)
        tb_layout.setContentsMargins(8, 4, 8, 4)
        tb_layout.setSpacing(6)

        self.grip = QLabel("::")
        self.grip.setObjectName("Grip")
        self.grip.setCursor(Qt.SizeAllCursor)
        tb_layout.addWidget(self.grip)

        self.title_lbl = QLabel("💬 M59 FLOATING CHAT")
        self.title_lbl.setObjectName("TitleLabel")
        tb_layout.addWidget(self.title_lbl)

        self.live_indicator = QLabel("● LIVE")
        self.live_indicator.setStyleSheet("color: #10b981; font-size: 9px; font-weight: 800; padding: 1px 4px; background: rgba(16, 185, 129, 0.15); border-radius: 3px;")
        tb_layout.addWidget(self.live_indicator)

        tb_layout.addStretch()

        # Roll up / Roll down button
        self.roll_btn = QPushButton("▲ Roll Up" if not self.is_rolled_up else "▼ Roll Down")
        self.roll_btn.setObjectName("RollBtn")
        self.roll_btn.setToolTip("Roll up or expand the floating chat box")
        self.roll_btn.clicked.connect(self.toggle_roll)
        tb_layout.addWidget(self.roll_btn)

        # Close button
        close_btn = QPushButton("✕")
        close_btn.setObjectName("CloseBtn")
        close_btn.setToolTip("Close floating chatbox")
        close_btn.clicked.connect(self.close)
        tb_layout.addWidget(close_btn)

        main_layout.addWidget(self.title_bar)

        # 2. Body Widget (Collapsible with Roll Up / Down)
        self.body_widget = QWidget()
        bw_layout = QVBoxLayout(self.body_widget)
        bw_layout.setContentsMargins(8, 6, 8, 8)
        bw_layout.setSpacing(6)

        # Filter Bar: Channel Pills + Search
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(4)

        self.channel_btns = {}
        self.unread_private_count = 0
        channels = [
            ("all", "ALL"),
            ("private", "PRIVATE"),
            ("guild", "GUILD"),
            ("chat", "CHAT"),
            ("combat", "COMBAT"),
            ("improves", "GAINS"),
            ("system", "SYS")
        ]

        for cid, label in channels:
            btn = QPushButton(label)
            btn.setProperty("class", "FloatFilterBtn")
            if cid == "all":
                btn.setProperty("active", "true")
            btn.clicked.connect(lambda checked=False, c=cid: self.set_channel_filter(c))
            self.channel_btns[cid] = btn
            filter_bar.addWidget(btn)

        filter_bar.addStretch()

        self.search_input = QLineEdit()
        self.search_input.setObjectName("FloatSearch")
        self.search_input.setPlaceholderText("Filter...")
        self.search_input.setFixedWidth(85)
        self.search_input.textChanged.connect(self.filter_chat)
        filter_bar.addWidget(self.search_input)

        bw_layout.addLayout(filter_bar)

        # Stream View
        self.stream_view = QTextEdit()
        self.stream_view.setObjectName("FloatChatStream")
        self.stream_view.setReadOnly(True)
        bw_layout.addWidget(self.stream_view, 1)

        # Bottom row with size grip
        bot_row = QHBoxLayout()
        bot_row.setContentsMargins(0, 0, 0, 0)
        bot_desc = QLabel("Anchored over game client")
        bot_desc.setStyleSheet("color: #64748b; font-size: 9px; font-style: italic;")
        bot_row.addWidget(bot_desc)
        bot_row.addStretch()
        size_grip = QSizeGrip(self)
        size_grip.setFixedSize(14, 14)
        bot_row.addWidget(size_grip)
        bw_layout.addLayout(bot_row)

        main_layout.addWidget(self.body_widget, 1)

        self.resize(self.saved_width, self.expanded_height)
        if self.is_rolled_up:
            self.body_widget.hide()
            self.roll_btn.setText("▼ Roll Down")
            tb_h = max(28, self.title_bar.sizeHint().height())
            self.setFixedHeight(tb_h)
            self.resize(self.saved_width, tb_h)

        self.init_docking()

        # Load existing chat logs from dashboard
        if self.dashboard and hasattr(self.dashboard, 'chat_logs'):
            for entry in self.dashboard.chat_logs:
                self.render_entry(entry)

        # Docking timer
        self.dock_timer = QTimer(self)
        self.dock_timer.setInterval(50)
        self.dock_timer.timeout.connect(self.check_docking)
        self.dock_timer.start()

    def get_target_hwnd(self):
        if self.target_hwnd and win32gui and win32gui.IsWindow(self.target_hwnd):
            return self.target_hwnd
        if self.dashboard and getattr(self.dashboard, 'main_hwnd', None):
            dh = self.dashboard.main_hwnd
            if win32gui and win32gui.IsWindow(dh):
                return dh
        return None

    def init_docking(self):
        target = self.get_target_hwnd()
        if target and win32gui and win32gui.IsWindow(target):
            try:
                rect = win32gui.GetWindowRect(target)
                target_x = rect[0] + self.offset_x
                target_y = rect[1] + self.offset_y
                hwnd = int(self.winId())
                if win32gui and win32con:
                    win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, target_x, target_y, 0, 0,
                                         win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
            except Exception as ex:
                print(f"Floating chatbox docking init error: {ex}")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = True
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and getattr(self, 'is_dragging', False):
            new_pos = event.globalPosition().toPoint() - self.drag_position
            self.move(new_pos)
            target = self.get_target_hwnd()
            if target and win32gui and win32gui.IsWindow(target):
                try:
                    my_hwnd = int(self.winId())
                    my_rect = win32gui.GetWindowRect(my_hwnd)
                    target_rect = win32gui.GetWindowRect(target)
                    self.offset_x = my_rect[0] - target_rect[0]
                    self.offset_y = my_rect[1] - target_rect[1]
                except Exception:
                    pass
            event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.is_rolled_up:
            w = self.width()
            h = self.height()
            if w >= 280:
                self.saved_width = w
            if h >= 160:
                self.expanded_height = h

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = False
            if self.dashboard:
                s = self.dashboard.load_gui_settings()
                s['floating_chat_x_offset'] = self.offset_x
                s['floating_chat_y_offset'] = self.offset_y
                if not self.is_rolled_up:
                    self.saved_width = max(280, self.width())
                    self.expanded_height = max(160, self.height())
                s['floating_chat_width'] = self.saved_width
                s['floating_chat_height'] = self.expanded_height
                s['floating_chat_rolled_up'] = self.is_rolled_up
                self.dashboard.save_gui_settings(s)
            event.accept()

    def check_docking(self):
        target = self.get_target_hwnd()
        if not target or not win32gui or not win32gui.IsWindow(target):
            if self.isVisible():
                self.hide()
            return

        if win32gui.IsIconic(target):
            if self.isVisible():
                self.hide()
            return

        fg = win32gui.GetForegroundWindow()
        dash_hwnd = None
        if self.dashboard and hasattr(self.dashboard, 'winId'):
            try:
                dash_hwnd = int(self.dashboard.winId())
            except Exception:
                pass
        my_hwnd = int(self.winId())

        is_game_active = False
        if self.isActiveWindow() or self.underMouse() or getattr(self, 'is_dragging', False):
            is_game_active = True
        elif self.dashboard and hasattr(self.dashboard, 'isActiveWindow') and self.dashboard.isActiveWindow():
            is_game_active = True
        elif fg in (target, dash_hwnd, my_hwnd):
            is_game_active = True
        elif fg:
            try:
                if win32process:
                    _, fg_pid = win32process.GetWindowThreadProcessId(fg)
                    if fg_pid == os.getpid():
                        is_game_active = True
                    else:
                        _, target_pid = win32process.GetWindowThreadProcessId(target)
                        if fg_pid == target_pid:
                            is_game_active = True

                if not is_game_active:
                    cur = fg
                    for _ in range(6):
                        if not cur or cur == 0:
                            break
                        if cur in (target, dash_hwnd, my_hwnd):
                            is_game_active = True
                            break
                        cur = win32gui.GetParent(cur)
            except Exception:
                pass

        if not is_game_active:
            if self.isVisible():
                self.hide()
            return

        if not self.isVisible():
            self.show()

        if not getattr(self, 'is_dragging', False):
            try:
                rect = win32gui.GetWindowRect(target)
                target_x = rect[0] + self.offset_x
                target_y = rect[1] + self.offset_y
                my_rect = win32gui.GetWindowRect(my_hwnd)
                if my_rect[0] != target_x or my_rect[1] != target_y:
                    win32gui.SetWindowPos(my_hwnd, 0, target_x, target_y, 0, 0,
                                         win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER)
            except Exception:
                pass

    def toggle_roll(self):
        if not self.is_rolled_up:
            self.expanded_height = max(160, self.height())
            self.saved_width = max(280, self.width())
            self.is_rolled_up = True
            self.body_widget.hide()
            self.roll_btn.setText("▼ Roll Down")
            tb_h = max(28, self.title_bar.sizeHint().height() if hasattr(self, 'title_bar') else 28)
            self.setFixedHeight(tb_h)
            self.resize(self.saved_width, tb_h)
        else:
            self.is_rolled_up = False
            self.setMaximumHeight(16777215)
            self.setMinimumHeight(0)
            self.body_widget.show()
            self.roll_btn.setText("▲ Roll Up")
            self.resize(self.saved_width, self.expanded_height)

        if self.dashboard:
            s = self.dashboard.load_gui_settings()
            s['floating_chat_rolled_up'] = self.is_rolled_up
            s['floating_chat_width'] = self.saved_width
            s['floating_chat_height'] = self.expanded_height
            self.dashboard.save_gui_settings(s)

    def notify_private_message(self):
        if self.active_channel != "private" or self.is_rolled_up or not self.isActiveWindow():
            self.unread_private_count += 1
            self.update_unread_badge()

    def update_unread_badge(self):
        if self.unread_private_count > 0:
            self.title_lbl.setText(f"💬 M59 FLOATING CHAT  [{self.unread_private_count} Private]")
            self.title_lbl.setStyleSheet("color: #a855f7; font-weight: 800; font-size: 11px;")
            if "private" in self.channel_btns:
                self.channel_btns["private"].setText(f"PRIVATE ({self.unread_private_count})")
        else:
            self.title_lbl.setText("💬 M59 FLOATING CHAT")
            self.title_lbl.setStyleSheet("color: #94a3b8; font-weight: 800; font-size: 11px;")
            if "private" in self.channel_btns:
                self.channel_btns["private"].setText("PRIVATE")

    def reset_unread_private(self):
        self.unread_private_count = 0
        self.update_unread_badge()

    def set_channel_filter(self, channel_id):
        self.active_channel = channel_id
        if channel_id == "private":
            self.reset_unread_private()
        for cid, btn in self.channel_btns.items():
            btn.setProperty("active", "true" if cid == channel_id else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self.filter_chat()

    def filter_chat(self):
        self.stream_view.clear()
        if not self.dashboard or not hasattr(self.dashboard, 'chat_logs'):
            return
        query = self.search_input.text().lower().strip()
        for entry in self.dashboard.chat_logs:
            ch = entry.get('channel')
            is_match = False
            if self.active_channel == "all":
                is_match = True
            elif self.active_channel == "private":
                is_match = (ch in ("private", "guild", "group"))
            else:
                is_match = (self.active_channel == ch)
            if is_match:
                if not query or query in entry.get('text', '').lower():
                    self.render_entry(entry)

    def append_entry(self, entry, is_historical=False):
        ch = entry.get('channel')
        if ch in ('private', 'guild', 'group') and not is_historical:
            self.notify_private_message()
        is_match = False
        if self.active_channel == "all":
            is_match = True
        elif self.active_channel == "private":
            is_match = (ch in ("private", "guild", "group"))
        else:
            is_match = (self.active_channel == ch)
        if is_match:
            query = self.search_input.text().lower().strip()
            if not query or query in entry.get('text', '').lower():
                self.render_entry(entry)

    def render_entry(self, entry):
        color = "#e2e8f0"
        ch = entry.get('channel', 'system')
        if ch == "improves":
            color = "#34d399"
        elif ch == "combat":
            color = "#f87171"
        elif ch == "private":
            color = "#a855f7"
        elif ch == "guild":
            color = "#c084fc"
        elif ch == "group":
            color = "#38bdf8"
        elif ch == "chat":
            color = "#60a5fa"
        elif ch == "system":
            color = "#fbbf24"

        fs = 11
        if self.dashboard and hasattr(self.dashboard, 'font_settings'):
            fs = max(10, self.dashboard.font_settings.get("chat_logger", 13) - 2)
        ts_fs = max(8, fs - 2)
        raw_text = entry.get('text', '')
        text_escaped = raw_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        line_html = f"<div style='margin-bottom: 3px;'><span style='color: #64748b; font-size: {ts_fs}px;'>[{entry.get('ts', '')}]</span> <span style='color: {color}; font-weight: 600; font-size: {fs}px;'>{text_escaped}</span></div>"
        self.stream_view.append(line_html)
        self.stream_view.moveCursor(QTextCursor.End)

    def closeEvent(self, event):
        if hasattr(self, 'dock_timer') and self.dock_timer:
            self.dock_timer.stop()
        if self.dashboard:
            if getattr(self.dashboard, 'active_floating_chat', None) == self:
                self.dashboard.active_floating_chat = None
            if not getattr(self.dashboard, '_is_shutting_down', False):
                try:
                    s = self.dashboard.load_gui_settings()
                    s['floating_chat_open'] = False
                    self.dashboard.save_gui_settings(s)
                except Exception as ex:
                    print(f"[M59-CHAT] Error saving closed state: {ex}", flush=True)
        event.accept()

# ----------------------------------------------------------------------
# Non-Intrusive Floating Toast Notification (Focus-Safe)
# ----------------------------------------------------------------------
class M59ToastNotification(QWidget):
    """
    Non-disruptive, frameless floating overlay notification that does not steal
    focus from the Meridian 59 game client or active window.
    Features:
    - Custom event type / icon (🟢 Login, ⚪ Logout, ⚔️ PK, 💬 Message)
    - Action buttons: Quick Whisper (💬), Dismiss (✕)
    - Configurable auto-dismiss duration
    - Click-through safe positioning in user-selected screen corner
    """
    def __init__(self, title, message, icon_type="login", player_name=None, group_name=None, duration_ms=3000, position="bottom-right", dashboard=None):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.dashboard = dashboard
        self.player_name = player_name
        self.group_name = group_name
        self.duration_ms = max(1000, duration_ms)
        self.position = position
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setFixedWidth(310)

        # Style palette based on event type
        border_color = "#38bdf8"
        badge_bg = "#075985"
        icon_str = "🟢"
        if icon_type == "logout":
            border_color = "#64748b"
            badge_bg = "#334155"
            icon_str = "⚪"
        elif icon_type == "pk":
            border_color = "#ef4444"
            badge_bg = "#991b1b"
            icon_str = "⚔️"
        elif icon_type == "tell":
            border_color = "#c084fc"
            badge_bg = "#6b21a8"
            icon_str = "💬"

        container = QFrame(self)
        container.setObjectName("ToastContainer")
        container.setStyleSheet(f"""
            QFrame#ToastContainer {{
                background-color: #0b0f19;
                border: 1.5px solid {border_color};
                border-radius: 8px;
            }}
        """)
        c_layout = QVBoxLayout(container)
        c_layout.setContentsMargins(12, 10, 12, 10)
        c_layout.setSpacing(6)

        # Top Bar: Icon + Title + Group Badge + Close
        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        icon_lbl = QLabel(icon_str)
        icon_lbl.setStyleSheet("font-size: 14px; background: transparent;")
        top_row.addWidget(icon_lbl)

        t_lbl = QLabel(title)
        t_lbl.setStyleSheet("font-size: 12px; font-weight: 800; color: #f8fafc; background: transparent;")
        top_row.addWidget(t_lbl, 1)

        if self.group_name:
            grp_badge = QLabel(f" {self.group_name} ")
            grp_badge.setStyleSheet(f"background-color: {badge_bg}; color: #f8fafc; font-size: 10px; font-weight: 800; border-radius: 4px; padding: 1px 5px;")
            top_row.addWidget(grp_badge)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #94a3b8; font-size: 11px; font-weight: bold; border: none; border-radius: 3px;
            }
            QPushButton:hover {
                background: #1e293b; color: #f87171;
            }
        """)
        close_btn.clicked.connect(self.close)
        top_row.addWidget(close_btn)
        c_layout.addLayout(top_row)

        # Message Body
        msg_lbl = QLabel(message)
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet("font-size: 11px; color: #cbd5e1; background: transparent;")
        c_layout.addWidget(msg_lbl)

        # Bottom Actions Row (if player_name is available)
        if self.player_name:
            act_row = QHBoxLayout()
            act_row.setSpacing(6)
            act_row.addStretch()

            dm_btn = QPushButton("💬 Whisper")
            dm_btn.setCursor(Qt.PointingHandCursor)
            dm_btn.setStyleSheet("""
                QPushButton {
                    background-color: #1e293b; color: #38bdf8; font-size: 11px; font-weight: 700;
                    border: 1px solid #334155; border-radius: 4px; padding: 3px 8px;
                }
                QPushButton:hover {
                    background-color: #38bdf8; color: #0b0f19; font-weight: 800;
                }
            """)
            dm_btn.clicked.connect(self.on_whisper_clicked)
            act_row.addWidget(dm_btn)
            c_layout.addLayout(act_row)

        # Outer layout
        main_l = QVBoxLayout(self)
        main_l.setContentsMargins(0, 0, 0, 0)
        main_l.addWidget(container)

        self.adjustSize()
        self.reposition()

        # Auto-dismiss timer
        self.dismiss_timer = QTimer(self)
        self.dismiss_timer.setSingleShot(True)
        self.dismiss_timer.timeout.connect(self.close)
        self.dismiss_timer.start(self.duration_ms)

    def on_whisper_clicked(self):
        if self.dashboard and self.player_name and hasattr(self.dashboard, 'open_dm_with_player'):
            self.dashboard.open_dm_with_player(self.player_name)
        self.close()

    def reposition(self):
        """Positions the toast overlay at the user's preferred corner of the primary screen."""
        screen = None
        if self.dashboard:
            window = self.dashboard.windowHandle()
            if window:
                screen = window.screen()
        if not screen:
            screen = QGuiApplication.primaryScreen()
        if not screen:
            return

        geo = screen.availableGeometry()
        margin = 20
        self.adjustSize()
        w = max(self.width(), 310)
        h = max(self.height(), 60)

        pos = (self.position or "bottom-right").lower()
        if pos == "top-right":
            x = geo.right() - w - margin
            y = geo.top() + margin
        elif pos == "top-left":
            x = geo.left() + margin
            y = geo.top() + margin
        elif pos == "bottom-left":
            x = geo.left() + margin
            y = geo.bottom() - h - margin
        else: # bottom-right
            x = geo.right() - w - margin
            y = geo.bottom() - h - margin

        self.setGeometry(x, y, w, h)

# ----------------------------------------------------------------------
# Player Group Management / Assignment Dialog
# ----------------------------------------------------------------------
class M59PlayerGroupDialog(QDialog):
    """
    Dialog to add a player to an existing group or create a new custom group.
    """
    def __init__(self, player_name, existing_groups, current_group=None, parent=None):
        super().__init__(parent)
        self.player_name = player_name
        self.existing_groups = existing_groups or {}
        self.current_group = current_group
        self.setWindowTitle(f"Group Manager - {self.player_name}")
        self.setFixedWidth(360)
        self.setStyleSheet("""
            QDialog {
                background-color: #0b0f19;
                color: #f8fafc;
                border: 1px solid #1e293b;
            }
            QLabel {
                color: #f8fafc;
                font-size: 12px;
            }
            QLineEdit, QComboBox {
                background-color: #030712;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 6px 10px;
                color: #f8fafc;
                font-size: 12px;
            }
            QCheckBox {
                color: #cbd5e1;
                font-size: 12px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        # Title Header
        hdr = QLabel(f"Assign <b>{self.player_name}</b> to Group")
        hdr.setStyleSheet("font-size: 14px; font-weight: 800; color: #38bdf8;")
        layout.addWidget(hdr)

        # Group Selector Mode
        form = QFormLayout()
        form.setSpacing(10)

        self.group_combo = QComboBox()
        self.group_combo.addItem("➕ Create New Group...", "__NEW__")
        group_names = sorted(list(self.existing_groups.keys()))
        for gn in group_names:
            cnt = len(self.existing_groups[gn].get("members", []))
            self.group_combo.addItem(f"📁 {gn} ({cnt} members)", gn)

        if self.current_group and self.current_group in group_names:
            idx = self.group_combo.findData(self.current_group)
            if idx >= 0:
                self.group_combo.setCurrentIndex(idx)
        elif "Friends" in group_names:
            idx = self.group_combo.findData("Friends")
            if idx >= 0:
                self.group_combo.setCurrentIndex(idx)

        form.addRow("Target Group:", self.group_combo)

        self.new_group_edit = QLineEdit()
        self.new_group_edit.setPlaceholderText("Enter new group name (e.g. Friends, Guild, PK Targets)...")
        form.addRow("New Name:", self.new_group_edit)

        layout.addLayout(form)

        # Alert Options
        alert_box = QGroupBox("Group Alerts & Notifications")
        alert_box.setStyleSheet("QGroupBox { font-size: 11px; font-weight: 800; color: #94a3b8; border: 1px solid #1e293b; border-radius: 6px; margin-top: 6px; padding-top: 10px; }")
        ab_layout = QVBoxLayout(alert_box)
        ab_layout.setContentsMargins(10, 10, 10, 10)
        ab_layout.setSpacing(8)

        self.alert_login_chk = QCheckBox("Show Toast Notification on Player Login")
        self.alert_login_chk.setChecked(True)
        ab_layout.addWidget(self.alert_login_chk)

        self.alert_logout_chk = QCheckBox("Show Toast Notification on Player Logout")
        self.alert_logout_chk.setChecked(False)
        ab_layout.addWidget(self.alert_logout_chk)

        self.sound_chk = QCheckBox("Play Audio Alert Chime")
        self.sound_chk.setChecked(True)
        ab_layout.addWidget(self.sound_chk)

        layout.addWidget(alert_box)

        # Dynamic visibility for new name field
        def on_group_changed():
            is_new = (self.group_combo.currentData() == "__NEW__")
            self.new_group_edit.setEnabled(is_new)
            if is_new:
                self.new_group_edit.setFocus()
            else:
                sel_g = self.group_combo.currentData()
                g_cfg = self.existing_groups.get(sel_g, {})
                self.alert_login_chk.setChecked(g_cfg.get("alert_login", True))
                self.alert_logout_chk.setChecked(g_cfg.get("alert_logout", False))
                self.sound_chk.setChecked(g_cfg.get("sound_enabled", True))

        self.group_combo.currentIndexChanged.connect(on_group_changed)
        on_group_changed()

        # Buttons Row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setProperty("class", "WebBtnSecondary")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("✓ Save to Group")
        save_btn.setProperty("class", "WebBtnPrimary")
        save_btn.clicked.connect(self.on_save)
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)

    def on_save(self):
        sel_key = self.group_combo.currentData()
        if sel_key == "__NEW__":
            g_name = self.new_group_edit.text().strip()
            if not g_name:
                QMessageBox.warning(self, "Invalid Name", "Please enter a name for the new player group.")
                return
        else:
            g_name = sel_key

        self.result_group = g_name
        self.result_alert_login = self.alert_login_chk.isChecked()
        self.result_alert_logout = self.alert_logout_chk.isChecked()
        self.result_sound = self.sound_chk.isChecked()
        self.accept()

# ----------------------------------------------------------------------
# Player Direct Message Popup Dialog
# ----------------------------------------------------------------------
class M59DirectMessageDialog(QDialog):
    """
    Direct Message popup dialog for player-to-player communications.
    Features:
    - Frameless draggable title bar with online/offline status
    - Queued chronological message stream with timestamping
    - Direct tell input field with auto-copy to Windows clipboard (/tell <Player> <text>)
    - Sound and toast feedback notifications
    """
    def __init__(self, player_name, dashboard, parent=None):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.player_name = player_name
        self.dashboard = dashboard
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.resize(400, 460)
        self.setMinimumSize(320, 360)

        self.drag_position = QPoint()
        self.is_dragging = False

        self.setObjectName("DirectMessageDialog")
        self.setStyleSheet("""
            QDialog#DirectMessageDialog {
                background-color: #0b0f19;
                color: #f8fafc;
                border: 1px solid #d97706;
                border-radius: 8px;
            }
            QFrame#TitleBar {
                background-color: #1e293b;
                border-top-left-radius: 7px;
                border-top-right-radius: 7px;
                border-bottom: 1px solid #334155;
            }
            QLabel#TitleLabel {
                color: #fbbf24;
                font-weight: 800;
                font-size: 12px;
                letter-spacing: 0.5px;
            }
            QPushButton#CloseBtn {
                background-color: #334155;
                color: #cbd5e1;
                font-weight: 800;
                border: none;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 11px;
            }
            QPushButton#CloseBtn:hover {
                background-color: #dc2626;
                color: #ffffff;
            }
            QPushButton#ActionBtn {
                background-color: #1e293b;
                color: #cbd5e1;
                font-weight: 700;
                font-size: 10px;
                padding: 4px 8px;
                border-radius: 4px;
                border: 1px solid #334155;
            }
            QPushButton#ActionBtn:hover {
                background-color: #334155;
                color: #ffffff;
            }
            QPushButton#SendBtn {
                background-color: #059669;
                color: #ffffff;
                font-weight: 800;
                font-size: 11px;
                padding: 5px 14px;
                border-radius: 6px;
                border: 1px solid #34d399;
            }
            QPushButton#SendBtn:hover {
                background-color: #10b981;
            }
            QLineEdit {
                background-color: #030712;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 6px 10px;
                color: #f8fafc;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #fbbf24;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 1. Custom Title Bar (Draggable)
        title_bar = QFrame()
        title_bar.setObjectName("TitleBar")
        tb_layout = QHBoxLayout(title_bar)
        tb_layout.setContentsMargins(10, 7, 10, 7)
        tb_layout.setSpacing(8)

        msg_icon = QLabel("💬")
        msg_icon.setStyleSheet("font-size: 13px; background: transparent;")
        tb_layout.addWidget(msg_icon)

        self.title_lbl = QLabel(f"Direct Message — {self.player_name}")
        self.title_lbl.setObjectName("TitleLabel")
        tb_layout.addWidget(self.title_lbl)

        self.status_pill = QLabel()
        self.update_status_pill()
        tb_layout.addWidget(self.status_pill)

        tb_layout.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setObjectName("CloseBtn")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        tb_layout.addWidget(close_btn)

        layout.addWidget(title_bar)

        # 2. Content Container
        content_widget = QWidget()
        cw_layout = QVBoxLayout(content_widget)
        cw_layout.setContentsMargins(10, 10, 10, 10)
        cw_layout.setSpacing(8)

        # Sub-header actions row
        actions_row = QHBoxLayout()
        actions_row.setSpacing(6)

        clear_btn = QPushButton("🗑️ Clear History", objectName="ActionBtn")
        clear_btn.setToolTip("Clear message history for this player")
        clear_btn.clicked.connect(self.clear_history)
        actions_row.addWidget(clear_btn)

        actions_row.addStretch()

        mark_read_btn = QPushButton("✔️ Mark Read", objectName="ActionBtn")
        mark_read_btn.clicked.connect(self.mark_as_read)
        actions_row.addWidget(mark_read_btn)

        cw_layout.addLayout(actions_row)

        # 3. Queued Message Stream View
        self.msg_view = QTextEdit()
        self.msg_view.setReadOnly(True)
        self.msg_view.setStyleSheet("""
            QTextEdit {
                background-color: #030712;
                border: 1px solid #1e293b;
                border-radius: 8px;
                padding: 10px;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                font-size: 12px;
            }
        """)
        cw_layout.addWidget(self.msg_view, 1)

        # 4. Direct Tell Input Bar
        reply_box = QVBoxLayout()
        reply_box.setSpacing(4)

        input_row = QHBoxLayout()
        input_row.setSpacing(6)

        self.reply_input = QLineEdit()
        self.reply_input.setPlaceholderText(f"Message {self.player_name}... (Press Enter to Send)")
        self.reply_input.returnPressed.connect(self.send_reply)
        input_row.addWidget(self.reply_input, 1)

        send_btn = QPushButton("Send", objectName="SendBtn")
        send_btn.setCursor(Qt.PointingHandCursor)
        send_btn.clicked.connect(self.send_reply)
        input_row.addWidget(send_btn)

        reply_box.addLayout(input_row)

        self.status_toast = QLabel("")
        self.status_toast.setStyleSheet("font-size: 10px; color: #a855f7; font-weight: 700; background: transparent;")
        reply_box.addWidget(self.status_toast)

        cw_layout.addLayout(reply_box)

        layout.addWidget(content_widget, 1)

        # Prevent buttons from acting as default QDialog submit triggers
        for b in (close_btn, clear_btn, mark_read_btn, send_btn):
            b.setAutoDefault(False)
            b.setDefault(False)

        # Load queued messages
        self.refresh_messages()

    def update_status_pill(self):
        p_key = self.player_name.lower()
        is_online = hasattr(self.dashboard, 'wholist_data') and any(str(k).lower() == p_key for k in self.dashboard.wholist_data.keys())
        if is_online:
            st = self.dashboard.wholist_data.get(self.player_name, "ONLINE")
            self.status_pill.setText(f"🟢 Online ({st})")
            self.status_pill.setStyleSheet("background-color: #064e3b; color: #34d399; font-size: 10px; font-weight: 800; padding: 1px 6px; border-radius: 4px; border: 1px solid #059669;")
        else:
            self.status_pill.setText("⚪ Offline")
            self.status_pill.setStyleSheet("background-color: #1e293b; color: #94a3b8; font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 4px; border: 1px solid #334155;")

    def refresh_messages(self):
        p_key = self.player_name.lower()
        thread_data = getattr(self.dashboard, 'player_dms', {}).get(p_key, {"player_name": self.player_name, "messages": []})
        messages = thread_data.get("messages", [])

        self.update_status_pill()

        if not messages:
            html = f"<div style='color: #64748b; text-align: center; margin-top: 50px; font-size: 12px;'>No direct messages with <b>{self.player_name}</b> yet.<br><br>Type below to send a tell.</div>"
        else:
            html = "<div style='display: flex; flex-direction: column; gap: 6px; font-family: sans-serif;'>"
            for m in messages:
                direction = m.get("direction", "in")
                ts = m.get("ts", "")
                text = m.get("text", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                m_type = m.get("type", "tell").upper()

                if direction == "out":
                    # Outgoing message (You -> Player)
                    clean_target = self.player_name.strip().strip('"')
                    html += f"""
                    <div style='margin-bottom: 6px; text-align: right;'>
                        <div style='display: inline-block; max-width: 85%; background-color: #0c4a6e; border: 1px solid #0284c7; border-radius: 8px 8px 2px 8px; padding: 6px 10px; text-align: left;'>
                            <div style='font-size: 10px; color: #38bdf8; font-weight: 700; margin-bottom: 2px;'>You tell "{clean_target}" <span style='color: #94a3b8; font-weight: 400;'>[{ts}]</span></div>
                            <div style='color: #f0fdfa; font-size: 12px; line-height: 1.35;'>{text}</div>
                        </div>
                    </div>
                    """
                else:
                    # Incoming message (Player -> You)
                    clean_sender = self.player_name.strip().strip('"')
                    html += f"""
                    <div style='margin-bottom: 6px; text-align: left;'>
                        <div style='display: inline-block; max-width: 85%; background-color: #451a03; border: 1px solid #d97706; border-radius: 8px 8px 8px 2px; padding: 6px 10px;'>
                            <div style='font-size: 10px; color: #fbbf24; font-weight: 700; margin-bottom: 2px;'>"{clean_sender}" [{m_type}] <span style='color: #94a3b8; font-weight: 400;'>[{ts}]</span></div>
                            <div style='color: #fffbeb; font-size: 12px; line-height: 1.35;'>{text}</div>
                        </div>
                    </div>
                    """
            html += "</div>"

        self.msg_view.setHtml(html)
        self.msg_view.moveCursor(QTextCursor.End)

    def refocus_dialog(self):
        """Ensures DM window remains visible, on top, and ready for the next message."""
        try:
            self.show()
            self.raise_()
            self.activateWindow()
            self.reply_input.setFocus()
        except Exception:
            pass

    def clear_status_toast(self):
        try:
            if hasattr(self, 'status_toast') and self.status_toast:
                self.status_toast.setText("")
        except Exception:
            pass

    def send_reply(self):
        text = self.reply_input.text().strip()
        if not text:
            return

        clean_pname = self.player_name.strip().strip('"')
        tell_cmd = f'tell "{clean_pname}" {text}'

        # 1. Directly transmit tell command to Meridian 59 game chat
        target = getattr(self.dashboard, 'main_hwnd', None) if self.dashboard else None
        sent_to_game = False
        if target and send_chat_command:
            try:
                def _send():
                    try:
                        send_chat_command(target, tell_cmd, send_enter=True)
                    except Exception as ex:
                        print(f"[DM-SEND-ERR] {ex}", flush=True)
                    # Re-focus DM window immediately after game command dispatch
                    QTimer.singleShot(100, self.refocus_dialog)
                threading.Thread(target=_send, daemon=True).start()
                sent_to_game = True
            except Exception as e:
                print(f"[DM-SEND] Error dispatching to game: {e}", flush=True)

        # 2. Also keep clipboard ready as fallback
        try:
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(tell_cmd)
        except Exception:
            pass

        # 3. Record in local conversation thread
        ts = datetime.now().strftime("%H:%M:%S")
        if self.dashboard:
            self.dashboard.record_direct_message(ts, self.player_name, text, f"You tell {self.player_name}, '{text}'", direction="out", msg_type="tell")

        self.reply_input.clear()
        self.refresh_messages()
        
        if sent_to_game:
            self.status_toast.setText(f"✓ Sent tell to {self.player_name}")
        else:
            self.status_toast.setText(f"✓ Copied '{tell_cmd}' (Game not attached)")
        QTimer.singleShot(3000, self.clear_status_toast)

        # Refocus window and input field for continuous typing
        self.refocus_dialog()

    def clear_history(self):
        p_key = self.player_name.lower()
        if self.dashboard and hasattr(self.dashboard, 'player_dms') and p_key in self.dashboard.player_dms:
            self.dashboard.player_dms[p_key]["messages"] = []
            self.dashboard.save_dms_cache()
            self.refresh_messages()

    def mark_as_read(self):
        if self.dashboard:
            self.dashboard.mark_dm_read(self.player_name)
        self.status_toast.setText("✓ Marked as read.")
        QTimer.singleShot(2000, self.clear_status_toast)

    def mousePressEvent(self, event):
        pos_y = event.position().y() if hasattr(event, 'position') else event.pos().y()
        if event.button() == Qt.LeftButton and pos_y <= 36:
            self.is_dragging = True
            g_pos = event.globalPosition().toPoint() if hasattr(event, 'globalPosition') else event.globalPos()
            self.drag_position = g_pos - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.is_dragging and event.buttons() & Qt.LeftButton:
            g_pos = event.globalPosition().toPoint() if hasattr(event, 'globalPosition') else event.globalPos()
            self.move(g_pos - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.is_dragging = False
        event.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            # QLineEdit.returnPressed already triggers send_reply when focus is in reply_input.
            # Accept event to prevent dialog closing without invoking send_reply twice.
            event.accept()
            return
        elif event.key() == Qt.Key_Escape:
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        p_key = self.player_name.lower()
        if self.dashboard:
            if hasattr(self.dashboard, 'active_dm_dialogs'):
                self.dashboard.active_dm_dialogs.pop(p_key, None)
            if hasattr(self.dashboard, 'active_icq_dialogs'):
                self.dashboard.active_icq_dialogs.pop(p_key, None)
            self.dashboard.mark_dm_read(self.player_name)
        event.accept()

# Alias for backwards compatibility
M59ICQMessengerDialog = M59DirectMessageDialog

# ----------------------------------------------------------------------
# Alias & Macro Editor Modal Dialog
# ----------------------------------------------------------------------
class AliasEditDialog(QDialog):
    def __init__(self, alias=None, parent=None):
        super().__init__(parent)
        self.alias = alias
        self.setWindowTitle("Edit Command Alias" if alias else "Add New Command Alias")
        self.resize(480, 330)
        self.setStyleSheet("""
            QDialog {
                background-color: #0f172a;
                color: #f8fafc;
            }
            QLabel {
                font-size: 11px;
                font-weight: 700;
                color: #cbd5e1;
            }
            QLineEdit {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 6px 10px;
                color: #f8fafc;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #94a3b8;
            }
            QCheckBox {
                color: #f8fafc;
                font-size: 11px;
                font-weight: 600;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                background-color: #1e293b;
                border: 2px solid #64748b;
                border-radius: 4px;
            }
            QCheckBox::indicator:hover {
                border-color: #7dd3fc;
                background-color: #334155;
            }
            QCheckBox::indicator:checked {
                background-color: #475569;
                border-color: #94a3b8;
                image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23ffffff' stroke-width='4' stroke-linecap='round' stroke-linejoin='round'><polyline points='20 6 9 17 4 12'></polyline></svg>");
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(18, 18, 18, 18)

        form = QFormLayout()
        form.setSpacing(10)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. Barloque Escape or Broadcast Art")
        if alias and "name" in alias:
            self.name_input.setText(alias["name"])

        self.hotkey_input = QLineEdit()
        self.hotkey_input.setPlaceholderText("e.g. f1 or ctrl+h")
        if alias and "hotkey" in alias:
            self.hotkey_input.setText(alias["hotkey"])

        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("e.g. broadcast [ART] ~~ [ART]")
        if alias and "command1" in alias:
            self.command_input.setText(alias["command1"])

        cmd_box = QVBoxLayout()
        cmd_box.setSpacing(3)
        cmd_box.addWidget(self.command_input)

        hint_lbl = QLabel("💡 Tip: Use '~~' anywhere in command to place cursor without sending Enter")
        hint_lbl.setStyleSheet("color: #94a3b8; font-size: 10px; font-weight: normal;")
        cmd_box.addWidget(hint_lbl)

        self.send_enter_checkbox = QCheckBox("Send Enter automatically after placing command in chat box")
        self.send_enter_checkbox.setChecked(alias.get("send_enter", True) if alias else True)

        self.float_checkbox = QCheckBox("Show Floating Action Button on Screen")
        if alias and alias.get("show_float", False):
            self.float_checkbox.setChecked(True)

        form.addRow("Alias Name:", self.name_input)
        form.addRow("Hotkey Binding:", self.hotkey_input)
        form.addRow("Chat Command:", cmd_box)
        form.addRow("", self.send_enter_checkbox)
        form.addRow("", self.float_checkbox)

        layout.addLayout(form)

        btn_box = QHBoxLayout()
        btn_box.setSpacing(10)

        save_btn = QPushButton("Save Alias")
        save_btn.setProperty("class", "WebBtnPrimary")
        save_btn.clicked.connect(self.accept)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setProperty("class", "WebBtnSecondary")
        cancel_btn.clicked.connect(self.reject)

        btn_box.addStretch()
        btn_box.addWidget(save_btn)
        btn_box.addWidget(cancel_btn)

        layout.addLayout(btn_box)

    def get_alias_data(self):
        return {
            "name": self.name_input.text().strip() or "New Alias",
            "hotkey": self.hotkey_input.text().strip().lower(),
            "command1": self.command_input.text().strip(),
            "enabled": True,
            "send_enter": self.send_enter_checkbox.isChecked(),
            "show_float": self.float_checkbox.isChecked(),
            "x_offset": self.alias.get("x_offset", 0) if (hasattr(self, 'alias') and self.alias) else 0,
            "y_offset": self.alias.get("y_offset", 0) if (hasattr(self, 'alias') and self.alias) else 0
        }

# ----------------------------------------------------------------------
# Reorderable Container & Card System
# ----------------------------------------------------------------------
class GridReorderContainer(QWidget):
    """Fluid flow container supporting responsive horizontal tile wrapping based on application width, row height alignment, and vertical drag-reordering."""
    def __init__(self, cols=12, parent=None):
        super().__init__(parent)
        self.cols = cols
        self.cards = []
        self._is_refreshing = False
        self._last_width = None
        
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(12)
        self.setAcceptDrops(True)

        self.current_hover_target = None
        self.current_hover_zone = None
        self.current_hover_dragged = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w = event.size().width()
        if self._last_width is not None and abs(w - self._last_width) < 15:
            return
        self._last_width = w
        self.refresh_layout()

    def add_card(self, card):
        card.grid_container = self
        if card not in self.cards:
            self.cards.append(card)
        self.refresh_layout()

    def remove_card(self, card):
        if card in self.cards:
            self.cards.remove(card)
            card.grid_container = None
            self.refresh_layout()

    def refresh_layout(self):
        if getattr(self, '_is_refreshing', False):
            return
        self._is_refreshing = True
        try:
            self._do_refresh_layout()
        finally:
            self._is_refreshing = False

    def _do_refresh_layout(self):
        self.reset_hover_state()

        # Safely remove all layout items and nested layouts without deleting child widgets
        while self.main_layout.count() > 0:
            item = self.main_layout.takeAt(0)
            if item.layout():
                sub_layout = item.layout()
                while sub_layout.count() > 0:
                    sub_layout.takeAt(0)

        # Filter out any C++ deleted or invalid card objects
        valid_cards = []
        for c in self.cards:
            try:
                _ = c.title_text
                valid_cards.append(c)
            except (RuntimeError, AttributeError):
                pass
        self.cards = valid_cards

        if not self.cards:
            return

        # Handle 1-column Dock Panel layout
        if self.cols == 1:
            for card in self.cards:
                card.row_siblings = [card]
                if hasattr(card, 'update_width_controls'):
                    card.update_width_controls()
                card.custom_height = None
                if getattr(card, 'is_expanding', False):
                    card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                else:
                    card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
                    ideal_h = card.get_content_ideal_height()
                    card.setMinimumHeight(ideal_h)
                    card.setMaximumHeight(16777215)
                self.main_layout.addWidget(card)
            self.main_layout.addStretch()

            if hasattr(self, 'parent_card') and self.parent_card and hasattr(self.parent_card, 'grid_container') and self.parent_card.grid_container:
                self.parent_card.grid_container.refresh_layout()
            return

        # Handle Main Dashboard Responsive Flow Layout
        avail_w = self.width()
        if avail_w <= 100:
            p = self.parentWidget()
            if p and p.width() > 100:
                avail_w = p.width() - 28
            else:
                avail_w = 1100

        spacing = 12

        # Group cards into horizontal rows based on column spans (max 12 per row)
        rows = []
        current_row = []

        for card in self.cards:
            if hasattr(card, 'update_width_controls'):
                card.update_width_controls()

            span = getattr(card, 'column_span', 6)
            if getattr(card, 'custom_width', None):
                span = max(3, min(12, int(round((card.custom_width / float(max(1, avail_w))) * 12.0 / 3.0) * 3)))
                card.column_span = span

            row_span = sum(getattr(c, 'column_span', 6) for c in current_row)
            if current_row and (row_span + span > 12):
                rows.append(current_row)
                current_row = [card]
            else:
                current_row.append(card)

        if current_row:
            rows.append(current_row)

        # Render rows and align row heights & widths strictly with zero space
        for row_cards in rows:
            row_layout = QHBoxLayout()
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(spacing)

            # Determine unified height for all cards in this row
            row_h = 220
            has_custom_h = any(getattr(c, 'custom_height', None) for c in row_cards)
            for c in row_cards:
                ideal = c.get_content_ideal_height()
                custom = getattr(c, 'custom_height', None)
                card_req = max(ideal, custom) if custom else ideal
                row_h = max(row_h, card_req)

            # Apply identical height and row_siblings to EVERY card in row
            for c in row_cards:
                c.row_siblings = row_cards
                c.setFixedHeight(row_h)
                if has_custom_h:
                    c.custom_height = row_h

                c.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                c.setMinimumWidth(100)
                c.setMaximumWidth(16777215)

                span = getattr(c, 'column_span', 6)
                row_layout.addWidget(c, stretch=span)

            self.main_layout.addLayout(row_layout)

        self.main_layout.addStretch()

    def detect_zone(self, card, pos_in_card):
        """Determines TOP or BOTTOM drop zone for reordering."""
        h = max(1, card.height())
        y = max(0, min(h, pos_in_card.y()))
        return "TOP" if (y / h) < 0.5 else "BOTTOM"

    def apply_zone_morph(self, dragged_card, target_card, zone):
        """Applies reordering based on drag target position."""
        if not getattr(dragged_card, 'is_draggable', True):
            return

        if dragged_card not in self.cards or target_card not in self.cards or dragged_card == target_card:
            return

        self.cards.remove(dragged_card)
        target_idx = self.cards.index(target_card)

        if zone == "TOP":
            self.cards.insert(target_idx, dragged_card)
        else:
            self.cards.insert(target_idx + 1, dragged_card)

        self.refresh_layout()

        self.refresh_layout()
        if hasattr(self.window(), 'save_layout_config'):
            self.window().save_layout_config()

    def handle_drag_over_card(self, dragged_card, target_card, pos_in_card):
        zone = self.detect_zone(target_card, pos_in_card)
        if target_card != self.current_hover_target and self.current_hover_target:
            self.current_hover_target.clear_zone_style()

        self.current_hover_target = target_card
        self.current_hover_zone = zone
        self.current_hover_dragged = dragged_card
        target_card.apply_zone_style(zone)

    def reset_hover_state(self):
        if self.current_hover_target:
            try:
                if hasattr(self.current_hover_target, 'clear_zone_style'):
                    self.current_hover_target.clear_zone_style()
            except RuntimeError:
                pass
        self.current_hover_target = None
        self.current_hover_zone = None
        self.current_hover_dragged = None

    def dragEnterEvent(self, event):
        if event.mimeData().hasText() and event.mimeData().text() == "M59_REORDER_CARD":
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.reset_hover_state()

    def dropEvent(self, event):
        if self.current_hover_dragged and self.current_hover_target and self.current_hover_zone:
            self.apply_zone_morph(self.current_hover_dragged, self.current_hover_target, self.current_hover_zone)
        self.reset_hover_state()
        self.refresh_layout()
        event.acceptProposedAction()


class ReorderableCard(QFrame):
    """Interactive Card component supporting automatic zone-based drag-and-drop morphing, vertical resizing, and content scrollbars."""
    def __init__(self, title_text, grid_container=None, icon="⋮⋮", default_colspan=6, is_draggable=True, parent=None):
        border_color="#94a3b8"
        super().__init__(parent)
        self.setProperty("class", "WebCard")
        self.setAcceptDrops(True)
        self.grid_container = None
        self.column_span = default_colspan
        self.border_color = border_color
        self.is_draggable = is_draggable
        self.title_text = title_text
        self.custom_height = None
        self._card_ref = self

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(4, 4, 4, 4)
        self.main_layout.setSpacing(4)

        # Header Frame (Click & Drag Header if draggable)
        self.header_frame = QWidget()
        if self.is_draggable:
            self.header_frame.setCursor(Qt.SizeAllCursor)
        self.header_layout = QHBoxLayout(self.header_frame)
        self.header_layout.setContentsMargins(0, 0, 0, 0)
        self.header_layout.setSpacing(6)

        # Drag Handle Badge (⋮⋮ DRAG if draggable)
        if self.is_draggable:
            self.drag_handle = QLabel(icon)
            self.drag_handle.setCursor(Qt.SizeAllCursor)
            self.drag_handle.setToolTip("Click & drag header into top, bottom, left, or right zone of another tile to dock/morph")
            self.drag_handle.setStyleSheet("font-size: 11px; color: #94a3b8; font-weight: 800; padding: 2px 6px; border-radius: 4px; background: #030712; border: 1px solid #334155;")
            self.header_layout.addWidget(self.drag_handle)
        else:
            self.drag_handle = None

        # Card Title Label
        self.title_label = QLabel(title_text)
        if self.is_draggable:
            self.title_label.setCursor(Qt.SizeAllCursor)
        self.title_label.setStyleSheet(f"font-size: 11px; font-weight: 800; color: {border_color}; letter-spacing: 0.8px;")
        self.header_layout.addWidget(self.title_label)

        self.header_layout.addStretch()

        # Width controls for Main Tab tiles
        self.width_control_widget = QWidget()
        width_layout = QHBoxLayout(self.width_control_widget)
        width_layout.setContentsMargins(0, 0, 0, 0)
        width_layout.setSpacing(2)

        self.btn_shrink = QPushButton("◀")
        self.btn_shrink.setFixedSize(18, 18)
        self.btn_shrink.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_shrink.setStyleSheet("""
            QPushButton {
                background-color: #0f172a; color: #94a3b8; border: 1px solid #334155; border-radius: 3px; font-size: 9px; font-weight: 800;
            }
            QPushButton:hover { background-color: #1e293b; color: #f8fafc; }
            QPushButton:disabled { color: #334155; border-color: #1e293b; }
        """)
        self.btn_shrink.clicked.connect(self.shrink_width)

        self.span_badge = QLabel(f"{self.column_span}/12")
        self.span_badge.setStyleSheet("font-size: 9px; font-weight: 800; color: #64748b; padding: 0 4px;")

        self.btn_expand = QPushButton("▶")
        self.btn_expand.setFixedSize(18, 18)
        self.btn_expand.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_expand.setStyleSheet("""
            QPushButton {
                background-color: #0f172a; color: #94a3b8; border: 1px solid #334155; border-radius: 3px; font-size: 9px; font-weight: 800;
            }
            QPushButton:hover { background-color: #1e293b; color: #f8fafc; }
            QPushButton:disabled { color: #334155; border-color: #1e293b; }
        """)
        self.btn_expand.clicked.connect(self.expand_width)

        width_layout.addWidget(self.btn_shrink)
        width_layout.addWidget(self.span_badge)
        width_layout.addWidget(self.btn_expand)

        self.width_control_widget.hide()
        self.header_layout.addWidget(self.width_control_widget)

        self.main_layout.addWidget(self.header_frame)

        # Scroll Area for Card Content so scrollbars appear if box is small (NO horizontal scrollbar)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("background: transparent; border: none;")

        self.content_widget = QWidget()
        self.content_widget.setStyleSheet("background: transparent;")
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(8)

        self.scroll_area.setWidget(self.content_widget)
        self.main_layout.addWidget(self.scroll_area, 1)

        # Bottom bar with diagonal corner resize handle for Main Tab tiles
        self.bottom_bar = QWidget()
        bottom_layout = QHBoxLayout(self.bottom_bar)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.addStretch()

        self.corner_resize_handle = QLabel("⇲")
        self.corner_resize_handle.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.corner_resize_handle.setStyleSheet("font-size: 11px; color: #475569; font-weight: 800; padding: 0 2px;")
        self.corner_resize_handle.setToolTip("Drag to resize tile height & width (double-click to reset)")
        self.corner_resize_handle.mousePressEvent = self.handle_corner_press
        self.corner_resize_handle.mouseMoveEvent = self.handle_corner_move
        self.corner_resize_handle.mouseDoubleClickEvent = self.handle_corner_dblclick

        bottom_layout.addWidget(self.corner_resize_handle)
        self.bottom_bar.hide()
        self.main_layout.addWidget(self.bottom_bar)

        if grid_container:
            grid_container.add_card(self)

        self.update_width_controls()

        # Connect mouse events for fluid header dragging if draggable
        if self.is_draggable:
            self._drag_start_pos = None
            for w in (self.header_frame, self.drag_handle, self.title_label):
                if w:
                    w.mousePressEvent = self.handle_handle_press
                    w.mouseMoveEvent = self.handle_handle_move

    def get_content_ideal_height(self):
        if self.grid_container and self.grid_container.cols == 1:
            ch = 0
            if hasattr(self, 'content_widget') and self.content_widget:
                self.content_widget.adjustSize()
                ch = max(0, self.content_widget.sizeHint().height(), self.content_widget.minimumSizeHint().height())
            hh = self.header_frame.sizeHint().height() if hasattr(self, 'header_frame') and self.header_frame else 22
            return ch + hh + 12

        ch = 200
        if hasattr(self, 'content_widget') and self.content_widget:
            self.content_widget.adjustSize()
            ch = max(ch, self.content_widget.sizeHint().height(), self.content_widget.minimumSizeHint().height())

        hh = self.header_frame.sizeHint().height() if hasattr(self, 'header_frame') and self.header_frame else 28
        return ch + hh + 14

    def add_header_widget(self, widget):
        if hasattr(self, 'width_control_widget') and self.width_control_widget:
            idx = self.header_layout.indexOf(self.width_control_widget)
            if idx >= 0:
                self.header_layout.insertWidget(idx, widget)
                return
        self.header_layout.addWidget(widget)

    def shrink_width(self):
        if self.column_span > 3:
            self.column_span = max(3, self.column_span - 3)
            self.custom_width = None
            if self.grid_container:
                self.grid_container.refresh_layout()
            if hasattr(self.window(), 'save_layout_config'):
                self.window().save_layout_config()

    def expand_width(self):
        if self.column_span < 12:
            self.column_span = min(12, self.column_span + 3)
            self.custom_width = None
            if self.grid_container:
                self.grid_container.refresh_layout()
            if hasattr(self.window(), 'save_layout_config'):
                self.window().save_layout_config()

    def update_width_controls(self):
        # Resize options are EXCLUSIVELY for tiles in the Main Dashboard View (12-column grid container).
        # They MUST NEVER appear in the Dockable Panel (docked or undocked floating window) or in sub-cards.
        is_main_dashboard = (
            self.grid_container is not None
            and getattr(self.grid_container, 'cols', 1) == 12
            and not isinstance(self, ReorderableSubCard)
        )

        if hasattr(self, 'width_control_widget') and self.width_control_widget:
            if is_main_dashboard:
                self.width_control_widget.show()
                if hasattr(self, 'span_badge') and self.span_badge:
                    self.span_badge.setText(f"{self.column_span}/12")
                if hasattr(self, 'btn_shrink') and self.btn_shrink:
                    self.btn_shrink.setEnabled(self.column_span > 3)
                if hasattr(self, 'btn_expand') and self.btn_expand:
                    self.btn_expand.setEnabled(self.column_span < 12)
            else:
                self.width_control_widget.hide()

        if hasattr(self, 'bottom_bar') and self.bottom_bar:
            if is_main_dashboard:
                self.bottom_bar.show()
            else:
                self.bottom_bar.hide()

    def handle_corner_press(self, event):
        if self.grid_container and self.grid_container.cols == 1:
            return
        if event.button() == Qt.LeftButton:
            self._diag_resize_start_pos = event.globalPosition()
            self._diag_resize_start_w = self.width()
            self._diag_resize_start_h = self.height()

    def handle_corner_move(self, event):
        if self.grid_container and self.grid_container.cols == 1:
            return
        if (event.buttons() & Qt.LeftButton) and self._diag_resize_start_pos is not None:
            curr_pos = event.globalPosition()
            dx = curr_pos.x() - self._diag_resize_start_pos.x()
            dy = curr_pos.y() - self._diag_resize_start_pos.y()

            # 1. Synchronized Height Resizing: All cards in the row resize to exact same height
            new_h = max(160, int(self._diag_resize_start_h + dy))
            self.custom_height = new_h
            if hasattr(self, 'row_siblings') and self.row_siblings:
                for sibling in self.row_siblings:
                    sibling.custom_height = new_h

            # 2. Width Resizing: Map mouse position to column span step (3, 6, 9, 12)
            avail_w = self.grid_container.width() if self.grid_container else 1100
            new_w = int(self._diag_resize_start_w + dx)
            span_step = max(3, min(12, int(round((new_w / float(max(1, avail_w))) * 12.0 / 3.0) * 3)))
            self.column_span = span_step
            self.custom_width = None

            if self.grid_container:
                self.grid_container.refresh_layout()

            if hasattr(self.window(), 'save_layout_config'):
                self.window().save_layout_config()

    def handle_corner_dblclick(self, event):
        if self.grid_container and self.grid_container.cols == 1:
            return
        self.custom_width = None
        self.custom_height = None
        if hasattr(self, 'row_siblings') and self.row_siblings:
            for sibling in self.row_siblings:
                sibling.custom_width = None
                sibling.custom_height = None
        if self.grid_container:
            self.grid_container.refresh_layout()
        if hasattr(self.window(), 'save_layout_config'):
            self.window().save_layout_config()

    def apply_zone_style(self, zone):
        """Highlights the active drop zone border on target card."""
        if self.grid_container and self.grid_container.cols == 1:
            if zone == "LEFT":
                zone = "TOP"
            elif zone == "RIGHT":
                zone = "BOTTOM"

        base = "border-radius: 6px; background-color: #020617; "
        if zone == "LEFT":
            self.setStyleSheet(base + "border-left: 6px solid #64748b; border-top: 1px solid #1e293b; border-right: 1px solid #1e293b; border-bottom: 1px solid #1e293b;")
        elif zone == "RIGHT":
            self.setStyleSheet(base + "border-right: 6px solid #64748b; border-top: 1px solid #1e293b; border-left: 1px solid #1e293b; border-bottom: 1px solid #1e293b;")
        elif zone == "TOP":
            self.setStyleSheet(base + "border-top: 6px solid #94a3b8; border-left: 1px solid #1e293b; border-right: 1px solid #1e293b; border-bottom: 1px solid #1e293b;")
        elif zone == "BOTTOM":
            self.setStyleSheet(base + "border-bottom: 6px solid #94a3b8; border-left: 1px solid #1e293b; border-right: 1px solid #1e293b; border-top: 1px solid #1e293b;")

    def clear_zone_style(self):
        self.setStyleSheet("")
        self.setProperty("class", "WebCard")
        self.style().unpolish(self)
        self.style().polish(self)

    def handle_handle_press(self, event):
        if self.is_draggable and event.button() == Qt.LeftButton:
            self._drag_start_pos = event.globalPosition().toPoint()

    def handle_handle_move(self, event):
        if not self.is_draggable:
            return
        if not (event.buttons() & Qt.LeftButton) or not self._drag_start_pos:
            return
        if (event.globalPosition().toPoint() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return

        mime = QMimeData()
        mime.setText("M59_REORDER_CARD")
        drag = QDrag(self)
        drag.setMimeData(mime)

        pixmap = self.grab()
        if not pixmap.isNull():
            drag.setPixmap(pixmap.scaled(280, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation))

        self.setStyleSheet("border: 2px dashed #64748b; border-radius: 10px; background-color: #020617;")
        drag.exec_(Qt.MoveAction)
        self.clear_zone_style()
        self._drag_start_pos = None

    def dragEnterEvent(self, event):
        if event.mimeData().hasText() and event.mimeData().text() == "M59_REORDER_CARD":
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasText() and event.mimeData().text() == "M59_REORDER_CARD":
            src_widget = event.source()
            if src_widget and self.grid_container:
                dragged_card = getattr(src_widget, '_card_ref', None) or src_widget
                while dragged_card and not isinstance(dragged_card, ReorderableCard):
                    if hasattr(dragged_card, 'parent') and callable(dragged_card.parent):
                        dragged_card = dragged_card.parent()
                    else:
                        break
                if dragged_card and dragged_card != self and dragged_card in self.grid_container.cards:
                    pos_in_card = event.position().toPoint()
                    self.grid_container.handle_drag_over_card(dragged_card, self, pos_in_card)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.clear_zone_style()

    def dropEvent(self, event):
        if event.mimeData().hasText() and event.mimeData().text() == "M59_REORDER_CARD":
            src_widget = event.source()
            if src_widget and self.grid_container:
                dragged_card = getattr(src_widget, '_card_ref', None) or src_widget
                while dragged_card and not isinstance(dragged_card, ReorderableCard):
                    if hasattr(dragged_card, 'parent') and callable(dragged_card.parent):
                        dragged_card = dragged_card.parent()
                    else:
                        break
                if dragged_card and dragged_card in self.grid_container.cards:
                    pos_in_card = event.position().toPoint()
                    zone = self.grid_container.detect_zone(self, pos_in_card)
                    self.grid_container.apply_zone_morph(dragged_card, self, zone)
        self.clear_zone_style()
        if self.grid_container:
            self.grid_container.reset_hover_state()
            self.grid_container.refresh_layout()
        event.acceptProposedAction()


class ReorderableSubCard(ReorderableCard):
    """Compact inner sub-card for grouping multiple draggable data sections into one master parent container."""
    def __init__(self, title_text, grid_container=None, icon="⋮⋮", default_colspan=1, is_draggable=True, parent=None):
        super().__init__(title_text, grid_container=grid_container, icon=icon, default_colspan=default_colspan, is_draggable=is_draggable, parent=parent)
        self.setObjectName("WebSubCard")
        self.set_sub_style()
        self.main_layout.setContentsMargins(4, 3, 4, 3)
        self.main_layout.setSpacing(2)
        self.content_layout.setContentsMargins(2, 2, 2, 2)
        self.content_layout.setSpacing(2)

        if hasattr(self, 'width_control_widget') and self.width_control_widget:
            self.width_control_widget.hide()
        if hasattr(self, 'bottom_bar') and self.bottom_bar:
            self.bottom_bar.hide()

    def set_sub_style(self):
        self.setStyleSheet("""
            QFrame#WebSubCard {
                background-color: #0f172a;
                border: 1px solid #1e293b;
                border-radius: 6px;
            }
        """)

    def apply_zone_style(self, zone):
        base = "border-radius: 6px; background-color: #020617; "
        if zone in ("LEFT", "TOP"):
            self.setStyleSheet(base + "border-top: 3px solid #38bdf8; border-left: 1px solid #1e293b; border-right: 1px solid #1e293b; border-bottom: 1px solid #1e293b;")
        else:
            self.setStyleSheet(base + "border-bottom: 3px solid #38bdf8; border-left: 1px solid #1e293b; border-right: 1px solid #1e293b; border-top: 1px solid #1e293b;")

    def clear_zone_style(self):
        self.set_sub_style()

    def get_content_ideal_height(self):
        ch = 0
        if hasattr(self, 'content_widget') and self.content_widget:
            self.content_widget.adjustSize()
            ch = max(0, self.content_widget.sizeHint().height(), self.content_widget.minimumSizeHint().height())
        hh = self.header_frame.sizeHint().height() if hasattr(self, 'header_frame') and self.header_frame else 18
        return ch + hh + 10


# ----------------------------------------------------------------------
# Local Companion Modules
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# Reagent Trend & Herb Consumption Chart Widget
# ----------------------------------------------------------------------
class ReagentTrendChartWidget(QWidget):
    """
    Custom QPainter dark-mode chart widget for visualizing reagent/herb usage
    trends over time (Daily totals, Hourly trends, or specific herb burn rates).
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(190)
        self.daily_data = {}
        self.history_data = []
        self.timeframe = "Last 7 Days"
        self.reagent_filter = "All Reagents"
        self.hover_idx = -1
        self.setMouseTracking(True)

    def set_data(self, daily_data, history_data, timeframe="Last 7 Days", reagent_filter="All Reagents"):
        self.daily_data = daily_data or {}
        self.history_data = history_data or []
        self.timeframe = timeframe
        self.reagent_filter = reagent_filter
        self.update()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, 'globalPosition') else event.pos()
        w = self.width()
        m_left = 45
        m_right = 20
        plot_w = w - m_left - m_right
        series = self._get_data_series()
        if series and plot_w > 0:
            bar_w = plot_w / len(series)
            if pos.x() >= m_left and pos.x() <= w - m_right:
                idx = int((pos.x() - m_left) / bar_w)
                if 0 <= idx < len(series):
                    if self.hover_idx != idx:
                        self.hover_idx = idx
                        self.update()
                    return
        if self.hover_idx != -1:
            self.hover_idx = -1
            self.update()

    def leaveEvent(self, event):
        if self.hover_idx != -1:
            self.hover_idx = -1
            self.update()

    def _get_data_series(self):
        """Builds (label, value, detail_str) tuple list based on timeframe and reagent_filter."""
        from datetime import datetime, timedelta
        series = []
        today_dt = datetime.now()
        r_filter = (self.reagent_filter or "All Reagents").strip()

        if self.timeframe == "Today":
            buckets = [
                ("00:00", 0, 3), ("04:00", 4, 7), ("08:00", 8, 11),
                ("12:00", 12, 15), ("16:00", 16, 19), ("20:00", 20, 23)
            ]
            today_str = today_dt.strftime("%Y-%m-%d")
            
            for b_lbl, start_h, end_h in buckets:
                val = 0
                for h_item in self.history_data:
                    h_date = h_item.get("date", today_str)
                    if h_date == today_str:
                        ts = h_item.get("ts", "")
                        try:
                            hour = int(ts.split(":")[0])
                            if start_h <= hour <= end_h:
                                reqs = h_item.get("reagents", {})
                                if r_filter in ["All Reagents", "All", ""]:
                                    val += sum(reqs.values())
                                else:
                                    val += reqs.get(r_filter, 0)
                        except:
                            pass
                series.append((b_lbl, val, f"{b_lbl} ({start_h:02d}:00-{end_h:02d}:59)"))

        else:
            num_days = 7
            if self.timeframe == "Last 30 Days":
                num_days = 30
            elif self.timeframe == "All Time":
                num_days = max(14, len(self.daily_data))

            dates = []
            for i in range(num_days - 1, -1, -1):
                d = today_dt - timedelta(days=i)
                dates.append(d.strftime("%Y-%m-%d"))

            for d_str in dates:
                try:
                    dt_obj = datetime.strptime(d_str, "%Y-%m-%d")
                    disp_lbl = dt_obj.strftime("%m/%d")
                except:
                    disp_lbl = d_str[-5:]

                d_entry = self.daily_data.get(d_str, {})
                val = 0
                if d_entry:
                    if r_filter in ["All Reagents", "All", ""]:
                        val = d_entry.get("total_reagents", 0)
                    else:
                        val = d_entry.get("reagents", {}).get(r_filter, 0)
                series.append((disp_lbl, val, d_str))

        return series

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        w = self.width()
        h = self.height()

        # Canvas Background Card
        bg_brush = QBrush(QColor("#030712"))
        border_pen = QPen(QColor("#1e293b"), 1)
        painter.setBrush(bg_brush)
        painter.setPen(border_pen)
        painter.drawRoundedRect(0, 0, w, h, 6, 6)

        m_left = 45
        m_right = 20
        m_top = 28
        m_bottom = 32

        plot_w = w - m_left - m_right
        plot_h = h - m_top - m_bottom

        if plot_w <= 20 or plot_h <= 20:
            return

        series = self._get_data_series()
        if not series:
            painter.setPen(QPen(QColor("#64748b")))
            painter.setFont(QFont("Segoe UI", 10, QFont.Bold))
            painter.drawText(0, 0, w, h, Qt.AlignCenter, "No Reagent Usage Recorded Yet")
            return

        max_v = max([s[1] for s in series] + [10])
        
        # Gridlines (0%, 50%, 100%)
        grid_pen = QPen(QColor("#1e293b"), 1, Qt.DashLine)
        text_pen = QPen(QColor("#64748b"))
        axis_font = QFont("Segoe UI", 8, QFont.Bold)
        painter.setFont(axis_font)

        for step in [0, 0.5, 1.0]:
            y_pos = int(m_top + plot_h * (1.0 - step))
            painter.setPen(grid_pen)
            painter.drawLine(m_left, y_pos, w - m_right, y_pos)
            
            val_lbl = f"{int(max_v * step)}"
            painter.setPen(text_pen)
            painter.drawText(5, y_pos - 6, m_left - 10, 14, Qt.AlignRight | Qt.AlignVCenter, val_lbl)

        # Color Gradient Scheme based on Filter
        r_filt = self.reagent_filter.lower()
        if "herb" in r_filt:
            col_top, col_bot = QColor("#34d399"), QColor("#059669")
        elif "elderberry" in r_filt:
            col_top, col_bot = QColor("#c084fc"), QColor("#7e22ce")
        elif "mushroom" in r_filt:
            col_top, col_bot = QColor("#f472b6"), QColor("#be185d")
        elif "solstice" in r_filt or "amber" in r_filt:
            col_top, col_bot = QColor("#f59e0b"), QColor("#b45309")
        else:
            col_top, col_bot = QColor("#38bdf8"), QColor("#0284c7")

        num_bars = len(series)
        group_w = plot_w / num_bars
        bar_w = max(4, int(group_w * 0.55))

        val_font = QFont("Segoe UI", 9, QFont.Bold)
        label_font = QFont("Segoe UI", 8, QFont.Bold)

        for i, (lbl, val, detail) in enumerate(series):
            cx = int(m_left + i * group_w + group_w / 2)
            bx = cx - bar_w // 2

            bar_h = int((val / max_v) * plot_h) if max_v > 0 else 0
            by = int(m_top + plot_h - bar_h)

            if bar_h > 0:
                grad = QLinearGradient(bx, by, bx, by + bar_h)
                if i == self.hover_idx:
                    grad.setColorAt(0, col_top.lighter(130))
                    grad.setColorAt(1, col_bot.lighter(120))
                else:
                    grad.setColorAt(0, col_top)
                    grad.setColorAt(1, col_bot)

                painter.setBrush(QBrush(grad))
                painter.setPen(QPen(col_top.lighter(130) if i == self.hover_idx else col_top, 1))
                painter.drawRoundedRect(bx, by, bar_w, bar_h, 3, 3)

                # Value label above bar
                painter.setPen(QPen(QColor("#f8fafc")))
                painter.setFont(val_font)
                painter.drawText(cx - 25, max(m_top - 18, by - 16), 50, 14, Qt.AlignCenter, f"{val}")
            else:
                painter.setBrush(QBrush(QColor("#334155")))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(cx - 2, int(m_top + plot_h - 3), 4, 4)

            # X-Axis Date/Hour Label
            painter.setPen(QPen(QColor("#38bdf8") if i == self.hover_idx else QColor("#94a3b8")))
            painter.setFont(label_font)
            painter.drawText(cx - 25, int(h - m_bottom + 6), 50, 18, Qt.AlignCenter, lbl)

            # Hover vertical line indicator
            if i == self.hover_idx:
                painter.setPen(QPen(QColor("#38bdf8"), 1, Qt.DotLine))
                painter.drawLine(cx, m_top, cx, h - m_bottom)


# ----------------------------------------------------------------------
# PySide6 Splash Screen & Status Overlay
# ----------------------------------------------------------------------
class M59SplashScreen(QWidget):
    """Frameless Splash Screen overlay displaying m59comp image & startup status messages."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.SplashScreen)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setFixedSize(580, 430)
        self._drag_pos = None

        # Center on Primary Screen
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.geometry()
            x = (geo.width() - 580) // 2
            y = (geo.height() - 430) // 2
            self.move(x, y)

        self.setStyleSheet("""
            QWidget {
                background-color: #0B0F19;
                border: 2px solid #1E293B;
                border-radius: 14px;
            }
            QLabel {
                background: transparent;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 24)
        layout.setAlignment(Qt.AlignCenter)

        # Top Bar with Drag Handle / Cross-Arrow Icon
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.addStretch(1)

        self.drag_lbl = QLabel("✥ Drag to Move")
        self.drag_lbl.setCursor(Qt.SizeAllCursor)
        self.drag_lbl.setToolTip("Click and drag to move splash overlay away from game login window")
        self.drag_lbl.setStyleSheet("""
            QLabel {
                color: #94a3b8;
                background-color: #1E293B;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel:hover {
                color: #f8fafc;
                background-color: #334155;
                border-color: #3b82f6;
            }
        """)
        top_bar.addWidget(self.drag_lbl)
        layout.addLayout(top_bar)

        # Image Container Label
        self.img_lbl = QLabel()
        self.img_lbl.setAlignment(Qt.AlignCenter)

        # Search for m59comp splash image (prioritize PNG with transparent corners)
        img_path = resource_path(os.path.join("imgs", "m59comp.png"))
        if not os.path.exists(img_path):
            img_path = resource_path(os.path.join("imgs", "m59comp.jpg"))

        if os.path.exists(img_path):
            pix = QPixmap(img_path)
            if not pix.isNull():
                scaled_pix = pix.scaled(190, 190, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.img_lbl.setPixmap(scaled_pix)
        else:
            self.img_lbl.setText("🛡️")
            self.img_lbl.setStyleSheet("font-size: 64px; color: #94a3b8;")

        layout.addWidget(self.img_lbl, 0, Qt.AlignCenter)
        layout.addSpacing(10)

        title_lbl = QLabel("MERIDIAN 59 COMPANION")
        title_lbl.setStyleSheet("font-size: 20px; font-weight: 900; color: #f8fafc; letter-spacing: 1.5px;")
        title_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_lbl, 0, Qt.AlignCenter)

        layout.addSpacing(12)

        self.status_title_lbl = QLabel("↻ SCANNING FOR GAME PROCESS")
        self.status_title_lbl.setStyleSheet("font-size: 13px; font-weight: 800; color: #94a3b8; letter-spacing: 1px;")
        self.status_title_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_title_lbl, 0, Qt.AlignCenter)

        self.status_sub_lbl = QLabel("Please launch Meridian 59 (meridian.exe) to continue...")
        self.status_sub_lbl.setStyleSheet("font-size: 11px; color: #94a3b8;")
        self.status_sub_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_sub_lbl, 0, Qt.AlignCenter)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.globalPosition().toPoint() if hasattr(event, 'globalPosition') else event.globalPos()
            self._drag_pos = pos - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and hasattr(self, '_drag_pos') and self._drag_pos is not None:
            pos = event.globalPosition().toPoint() if hasattr(event, 'globalPosition') else event.globalPos()
            self.move(pos - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = None
            event.accept()

    def set_status(self, mode="searching", title=None, msg=None):
        if mode == "initializing":
            self.status_title_lbl.setText(title or "↻ INITIALIZING GAME STATE")
            self.status_title_lbl.setStyleSheet("font-size: 13px; font-weight: 800; color: #fbbf24; letter-spacing: 1px;")
            self.status_sub_lbl.setText(msg or "Synchronizing memory state and character ledgers...")
        elif mode == "login":
            self.status_title_lbl.setText(title or "↻ WAITING FOR CHARACTER LOGIN")
            self.status_title_lbl.setStyleSheet("font-size: 13px; font-weight: 800; color: #60a5fa; letter-spacing: 1px;")
            self.status_sub_lbl.setText(msg or "Please select a character and enter the world.")
        elif mode == "connected":
            self.status_title_lbl.setText(title or "🟢 CONNECTED & READY")
            self.status_title_lbl.setStyleSheet("font-size: 13px; font-weight: 800; color: #94a3b8; letter-spacing: 1px;")
            self.status_sub_lbl.setText(msg or "Meridian 59 game process attached successfully!")
        else:
            self.status_title_lbl.setText(title or "↻ SCANNING FOR GAME PROCESS")
            self.status_title_lbl.setStyleSheet("font-size: 13px; font-weight: 800; color: #94a3b8; letter-spacing: 1px;")
            self.status_sub_lbl.setText(msg or "Please launch Meridian 59 (meridian.exe) to continue.")
        QApplication.processEvents()

try:
    pass
except Exception:
    InstanceManager = None

try:
    pass
except Exception:
    capture_identity = None
    cycle_tabs_and_scrape = None
    MemoryReader = None
    get_blakgraph_stats = None
    get_text_from_hwnd = None

try:
    pass
except Exception:
    WhoListMonitor = None

try:
    pass
except Exception:
    InventoryScraper = None
    process_inventory = None

try:
    pass
except Exception:
    perform_vault_scan = None
    send_chat_command = None


# ----------------------------------------------------------------------
# Modern Web QSS Stylesheet
# ----------------------------------------------------------------------
FLUID_WEB_QSS = """
* {
    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
    outline: none;
}

QMainWindow {
    background-color: #0f172a;
}

QWidget {
    background-color: transparent;
    color: #f8fafc;
}

/* Sidebar Navigation */
#SidebarWidget {
    background-color: #0f172a;
    border-right: 1px solid #1e293b;
}

#SidebarTitle {
    font-size: 15px;
    font-weight: 800;
    color: #f8fafc;
    letter-spacing: -0.5px;
}

#SidebarSub {
    font-size: 11px;
    color: #94a3b8;
    font-weight: 500;
}

QListWidget#NavList {
    background-color: transparent;
    border: none;
    font-size: 13px;
    font-weight: 600;
}

QListWidget#NavList::item {
    padding: 6px 10px;
    border-radius: 6px;
    color: #cbd5e1;
    margin-bottom: 2px;
    border: 1px solid transparent;
}

QListWidget#NavList::item:hover {
    background-color: #1e293b;
    color: #f8fafc;
}

QListWidget#NavList::item:selected {
    background-color: #1e293b;
    color: #f8fafc;
    border: 1px solid #334155;
    font-weight: 700;
}

/* Right Side Dock Panel */
#RightPanelWidget {
    background-color: #0f172a;
    border-left: 1px solid #1e293b;
}

/* Cards & Containers */
.WebCard {
    background-color: #1e293b;
    border: 1px solid #334155;
    border-radius: 8px;
}

/* Buttons */
QPushButton.WebBtnPrimary {
    background-color: #3b82f6;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-size: 12px;
    font-weight: 600;
}

QPushButton.WebBtnPrimary:hover {
    background-color: #2563eb;
}

QPushButton.WebBtnSecondary {
    background-color: #334155;
    color: #f8fafc;
    border: 1px solid #475569;
    border-radius: 6px;
    padding: 8px 14px;
    font-size: 12px;
    font-weight: 600;
}

QPushButton.WebBtnSecondary:hover {
    background-color: #475569;
}

/* Inputs & Dropdowns */
QLineEdit {
    background-color: #0f172a;
    color: #f8fafc;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 13px;
}

QLineEdit:focus {
    border: 1px solid #3b82f6;
}

QComboBox {
    background-color: #0f172a;
    color: #f8fafc;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
    min-height: 20px;
}

QComboBox:focus, QComboBox:on {
    border: 1px solid #3b82f6;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border-left: 1px solid #334155;
}

QComboBox QAbstractItemView {
    background-color: #0f172a;
    color: #f8fafc;
    selection-background-color: #3b82f6;
    selection-color: #ffffff;
    border: 1px solid #475569;
    border-radius: 4px;
    padding: 4px;
    outline: none;
}

QComboBox QAbstractItemView::item {
    color: #f8fafc;
    background-color: #0f172a;
    min-height: 24px;
    padding: 4px 8px;
}

QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {
    background-color: #2563eb;
    color: #ffffff;
}

/* Progress Bars */
QProgressBar {
    background-color: #334155;
    border: none;
    border-radius: 4px;
    text-align: center;
    color: transparent;
}

QProgressBar::chunk {
    background-color: #3b82f6;
    border-radius: 4px;
}

/* Scrollbars */
QScrollBar:vertical {
    border: none;
    background: #0f172a;
    width: 10px;
    margin: 0px 0px 0px 0px;
}
QScrollBar::handle:vertical {
    background: #334155;
    min-height: 30px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background: #475569;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
"""


import m59_updater
from m59_updater import check_all_releases, show_qt_update_dialog, get_installed_version

# ----------------------------------------------------------------------
# Qt Signal Bridge for Game Lifecycle Events
# ----------------------------------------------------------------------
class GameBridgeSignal(QObject):
    game_connected = Signal(object, int) # pm, pid
    game_disconnected = Signal(int)       # pid
    log_line_received = Signal(str)      # line
    sync_stats_received = Signal(dict)   # stats dict
    wholist_updated = Signal(dict)       # players dict {name: status}
    scrape_finished = Signal()           # memory scrape cycle complete
    identity_found = Signal(str)         # character name found from bio window
    vault_updated = Signal(str, list, str) # vault_type, items list, last_scan timestamp string
    room_changed = Signal(str)           # current room name string
    knowledge_updated = Signal(dict)     # knowledge dict {skill/spell: percent}
    update_detected = Signal(dict)       # release data dictionary


# ----------------------------------------------------------------------
# Standalone Desktop Dock Window (AppBar)
# ----------------------------------------------------------------------
class M59StandaloneDockWindow(QWidget):
    """Standalone Desktop Dock Window for the Dock Panel (World Clock & Who List).
    Registers as a Windows AppBar so all maximized windows automatically resize around it."""
    def __init__(self, parent_app):
        super().__init__()
        self.parent_app = parent_app
        self.is_docked = False
        self.dock_content = None
        self.dock_width = getattr(parent_app, 'dock_panel_width', 340)
        self._resizing_dock = False

        self.setWindowTitle("M59 Companion Dock")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setMinimumWidth(220)
        self.setMaximumWidth(700)
        self.resize(self.dock_width, 800)
        self.setMouseTracking(True)
        self.setStyleSheet(FLUID_WEB_QSS + """
            M59StandaloneDockWindow {
                border-left: 3px solid #64748b;
                background-color: #0b0f19;
            }
        """)

        self.container_layout = QVBoxLayout(self)
        self.container_layout.setContentsMargins(0, 0, 0, 0)
        self.container_layout.setSpacing(0)

        # Standalone Header
        hdr = QHBoxLayout()
        hdr.setContentsMargins(6, 6, 6, 6)
        hdr.setSpacing(4)
        title = QLabel("🛡️ M59 DOCK PANEL")
        title.setStyleSheet("font-size: 11px; font-weight: 800; color: #94a3b8; letter-spacing: 0.8px;")
        hdr.addWidget(title)
        hdr.addStretch()

        self.undock_btn = QPushButton("↙")
        self.undock_btn.setFixedSize(22, 22)
        self.undock_btn.setToolTip("Undock panel and return to Companion application")
        self.undock_btn.setStyleSheet("""
            QPushButton {
                font-size: 11px; color: #94a3b8; background: transparent;
                border: 1px solid #334155; border-radius: 4px; padding: 0px;
            }
            QPushButton:hover {
                color: #f8fafc; background: #1e293b; border-color: #475569;
            }
        """)
        self.undock_btn.clicked.connect(self.undock_desktop)
        hdr.addWidget(self.undock_btn)

        self.container_layout.addLayout(hdr)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if event.position().x() <= 12:
                self._resizing_dock = True
                self._resize_start_pos = event.globalPosition()
                self._resize_start_w = self.width()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if getattr(self, '_resizing_dock', False):
            dx = event.globalPosition().x() - self._resize_start_pos.x()
            new_w = max(220, min(700, int(self._resize_start_w - dx)))
            self.dock_width = new_w
            self.resize(new_w, self.height())
            if self.is_docked:
                hwnd = int(self.winId())
                register_window_appbar(hwnd, width=new_w)
            if self.parent_app:
                self.parent_app.dock_panel_width = new_w
                self.parent_app.save_layout_config()
            event.accept()
            return
        else:
            if event.position().x() <= 12:
                self.setCursor(Qt.SizeHorCursor)
            else:
                self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if getattr(self, '_resizing_dock', False):
            self._resizing_dock = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def fix_workarea_alignment(self):
        """Forces an instant recalculation of Windows AppBar positioning and clears any ghost gap."""
        hwnd = int(self.winId())
        reset_desktop_workarea()
        if self.is_docked:
            register_window_appbar(hwnd, width=self.dock_width)

    def attach_dock_content(self, content_widget):
        """Attaches right_panel_content widget into this standalone window and registers Windows AppBar."""
        self.dock_content = content_widget

        # Remove from main window layout first if present
        if hasattr(self.parent_app, 'right_panel_layout') and self.parent_app.right_panel_layout:
            self.parent_app.right_panel_layout.removeWidget(self.dock_content)

        self.dock_content.setParent(self)
        self.container_layout.addWidget(self.dock_content, 1)
        self.dock_content.show()

        self.show()
        self.raise_()
        self.activateWindow()

        # Register as Windows AppBar so desktop work area resizes around it
        hwnd = int(self.winId())
        self.is_docked = register_window_appbar(hwnd, width=self.dock_width)

        # Refresh WhoList rendering for new parent
        if hasattr(self.parent_app, 'update_wholist_gui') and hasattr(self.parent_app, 'wholist_data'):
            self.parent_app.update_wholist_gui(self.parent_app.wholist_data)

    def undock_desktop(self):
        """Unregisters AppBar and notifies parent_app to return the dock content to main window."""
        try:
            reset_desktop_workarea()
        except Exception:
            pass

        if self.is_docked:
            hwnd = int(self.winId())
            unregister_window_appbar(hwnd)
            self.is_docked = False

        if self.dock_content:
            self.container_layout.removeWidget(self.dock_content)

        self.hide()
        self.parent_app.on_standalone_dock_undocked()

    def closeEvent(self, event):
        self.undock_desktop()
        event.accept()


# ----------------------------------------------------------------------
# Main Application Window
# ----------------------------------------------------------------------
class M59CompanionApp(QMainWindow):
    def __init__(self):
        super().__init__()
        # Version & Channel State
        self.version = get_installed_version()
        v_clean = str(self.version).lstrip('v')
        self.setWindowTitle(f"Meridian 59 Companion - v{v_clean}")
        self.resize(1380, 880)

        # Print Startup Debug
        print("\n========================================================", flush=True)
        print("[M59-INIT] Starting Meridian 59 Companion - Pure Real-Data Engine", flush=True)
        print("[M59-INIT] All initial fields strictly blank until game attachment.", flush=True)
        print("========================================================\n", flush=True)

        # Docking & Splash State
        self.is_desktop_docked = False
        self.saved_geometry = None
        self.standalone_dock = None
        self.splash_screen = None
        self.main_hwnd = None
        self.pm_obj = None

        # Sound & Audio Alert State
        ensure_default_sounds()
        reset_desktop_workarea()
        self.unread_private_count = 0
        s_cfg = self.load_gui_settings()
        self.pk_alert_enabled = s_cfg.get("pk_alert_enabled", True)
        self.pk_sound_enabled = s_cfg.get("pk_sound_enabled", True)
        self.pk_sound_path = s_cfg.get("pk_sound_path", "sound/alert.wav")
        self.tell_sound_enabled = s_cfg.get("tell_sound_enabled", True)
        self.tell_sound_path = s_cfg.get("tell_sound_path", "sound/dm_chime.wav")
        self.pk_frame_enabled = s_cfg.get("pk_frame_enabled", True)
        self.pk_frame = PKFrame(self.main_hwnd, dashboard=self)

        # Init BGF Manager with Steam / Non-Steam / Local installation detection
        import m59_bgf
        from m59_map import detect_installation
        try:
            rooms_dir, _, _ = detect_installation()
        except Exception:
            rooms_dir = None
        if not rooms_dir:
            rooms_dir = resource_path("graphics") if os.path.exists(resource_path("graphics")) else os.getcwd()
        self.bgf_manager = m59_bgf.BGFManager(rooms_dir)
        self.bgf_manager.load_mob_mapping(resource_path("settings/moblist.csv"))
        self.current_bgf_frames = []
        self.current_bgf_image_pixmaps = []

        # Signal Bridge
        self.signals = GameBridgeSignal()
        self.signals.game_connected.connect(self.on_game_connected)
        self.signals.game_disconnected.connect(self.on_game_disconnected)
        self.signals.log_line_received.connect(self.process_log_line)
        self.signals.sync_stats_received.connect(self.update_gui_stats)
        self.signals.wholist_updated.connect(self.update_wholist_gui)
        self.signals.scrape_finished.connect(self.on_scrape_finished)
        self.signals.identity_found.connect(self.on_identity_found)
        self.signals.vault_updated.connect(self.on_vault_updated)
        self.signals.room_changed.connect(self.update_gps_room)
        self.signals.knowledge_updated.connect(self.update_progression_ui)
        self.signals.update_detected.connect(self.on_update_detected)

        # Character State
        self.char_name = "--"
        self.target_pid = None
        self.wholist_monitor = None
        self.wholist_data = {}
        self.use_24h_clock = True
        self.dock_panel_width = 340
        self.right_panel_collapsed = False

        # Models
        self.tracker = SessionTracker()
        self.combat_monitor = CombatMonitor(self.char_name)
        self.bank_manager = BankManager()
        self.gps_manager = GPSManager()
        self.gps_room_options = self.gps_manager.get_room_options() if self.gps_manager else []
        self.current_room_name = "Unknown Location"
        self.load_pvp_icons()
        self.calculator = SchoolCalculator()
        self.knowledge_cache = {}
        self.spell_manager = SpellManager(self.char_name)

        # Vault Data State
        self.vault_data = {"barloque": [], "hungry": []}
        self.vault_last_scan = {"barloque": "No scan data", "hungry": "No scan data"}

        # Inventory & Memory Scraper State
        self.inventory_scraper = None
        self.inventory_items = []
        self.inv_weight = 0
        self.inv_bulk = 0
        self.inv_sat_perc = 0.0
        self.inv_w_perc = 0.0
        self.inv_b_perc = 0.0
        self.inv_max_cap = 1700

        # Vitals Data (Max HP 150, Mana 250, Vigor 200)
        self.hp_current, self.hp_max = 0, 150
        self.mp_current, self.mp_max = 0, 250
        self.vg_current, self.vg_max = 0, 200

        # All 7 Meridian Attributes (Blank initial values)
        self.attributes = {
            "Might": "--",
            "Intellect": "--",
            "Stamina": "--",
            "Agility": "--",
            "Mysticism": "--",
            "Aim": "--",
            "Karma": "--"
        }

        # Session Ledgers & Direct Messages
        self.session_kills = {"monsters": {}, "players": {}}
        self.improves_history = []
        self.kills_history = []
        self.chat_logs = []
        self.player_dms = {}
        self.unread_dms = {}
        self.active_icq_dialogs = {}
        self.recent_log_fingerprints = deque(maxlen=250)
        self.active_channel = "all"
        self.session_seconds = 0
        self.comms_mode = "live"
        self.active_floating_chat = None
        self.active_elude_bar = None
        self.active_morph_bar = None
        self.pending_spell_trance = None

        # Font Settings State (Grouped logically by UI domains)
        self.font_settings = {
            "player_list": 13,
            "chat_logger": 13,
            "dashboard_cards": 13,
            "clock_panel": 13,
            "sidebar_nav": 13,
        }
        loaded_gui = self.load_gui_settings()
        if "font_settings" in loaded_gui and isinstance(loaded_gui["font_settings"], dict):
            self.font_settings.update(loaded_gui["font_settings"])

        # Logging and Diagnostic Preferences State
        self.console_output_enabled = loaded_gui.get("console_output_enabled", True)
        self.console_debug_enabled = loaded_gui.get("console_debug_enabled", True)
        self.file_debug_enabled = loaded_gui.get("file_debug_enabled", True)
        self.progression_log_enabled = loaded_gui.get("progression_log_enabled", True)
        setup_logging(
            debug_enabled=self.console_debug_enabled,
            console_output=self.console_output_enabled,
            file_debug=self.file_debug_enabled,
            progression_log=self.progression_log_enabled
        )

        # Player Groups & Wholist Tracking State
        self.player_groups = self.load_player_groups()
        self.discovered_players = self.load_discovered_players()
        self.collapsed_groups = set(loaded_gui.get("collapsed_groups", []))
        self.collapsed_groups.add("__OFFLINE__")  # Always ensure offline group is collapsed by default
        self.group_toast_duration_sec = loaded_gui.get("group_toast_duration_sec", 3)
        self.group_toast_position = loaded_gui.get("group_toast_position", "bottom-right")
        self.previous_online_players = {}
        self.last_who_list_items_count = 0
        self._active_toasts = []

        # Apply Fluid QSS
        self.setStyleSheet(FLUID_WEB_QSS)

        # Build Interface
        self.setup_ui()

        # Load Attributes Cache (Character Intellect, Might, etc.)
        self.load_attributes_cache()

        # Load Knowledge Cache & Progression
        self.load_knowledge_cache()

        # Load Direct Messages Cache
        self.load_dms_cache()

        # Load Historical Logs in Settings Directory
        self.refresh_historical_logs_list()

        # Clock Timer
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.on_clock_tick)
        self.clock_timer.start(1000)

        # Initialize Game Process Attachment Engine
        self.init_lifecycle_engine()

        # Start HWND Chat Monitor (Live Stream)
        self.start_chat_monitor()

        # Initialize Floating Action Buttons & Global Hotkeys
        self.update_floating_hotkey_buttons()
        self.register_global_hotkeys()

        # Restore any open floating action bars (Elude, Morph, Floating Chat)
        QTimer.singleShot(300, self.restore_launched_floating_bars)

        # Automatic Background Check for Dual Stable & Beta Releases
        self.start_background_update_check()

    def setup_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --------------------------------------------------------------
        # 1. LEFT SIDEBAR NAVIGATION
        # --------------------------------------------------------------
        sidebar = QWidget()
        sidebar.setObjectName("SidebarWidget")
        sidebar.setFixedWidth(195)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(10, 12, 10, 12)
        sidebar_layout.setSpacing(6)

        # Brand Title Header with Logo (supports PNG with transparency / ICO / JPG)
        brand_h = QHBoxLayout()
        brand_h.setContentsMargins(0, 0, 0, 0)
        brand_h.setSpacing(8)

        logo_lbl = QLabel()
        logo_lbl.setStyleSheet("background: transparent; border: none;")
        logo_path = resource_path(os.path.join("imgs", "m59comp.png"))
        if not os.path.exists(logo_path):
            logo_path = resource_path(os.path.join("imgs", "m59comp.ico"))
        if not os.path.exists(logo_path):
            logo_path = resource_path(os.path.join("imgs", "m59comp.jpg"))

        if os.path.exists(logo_path):
            pix = QPixmap(logo_path)
            if not pix.isNull():
                scaled_pix = pix.scaled(28, 28, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                logo_lbl.setPixmap(scaled_pix)
                brand_h.addWidget(logo_lbl)

        title_v = QVBoxLayout()
        title_v.setContentsMargins(0, 0, 0, 0)
        title_v.setSpacing(1)
        t1 = QLabel("M59 Companion", objectName="SidebarTitle")
        t1.setStyleSheet("font-size: 14px; font-weight: 800; color: #f8fafc;")
        t2 = QLabel(f"v{str(self.version).lstrip('v')}", objectName="SidebarSub")
        t2.setStyleSheet("font-size: 10px; color: #64748b; font-weight: 600;")
        title_v.addWidget(t1)
        title_v.addWidget(t2)
        brand_h.addLayout(title_v, 1)

        sidebar_layout.addLayout(brand_h)

        sidebar_layout.addSpacing(4)

        # Navigation Menu List
        self.nav_list = QListWidget()
        self.nav_list.setObjectName("NavList")
        self.nav_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.nav_list.setMinimumHeight(220)
        self.nav_list.setMaximumHeight(16777215)
        self.nav_list.addItem("  Dashboard")
        self.nav_list.addItem("  Progression")
        self.nav_list.addItem("  Reagent Usage")
        self.nav_list.addItem("  Hotkeys / Buttons")
        self.nav_list.addItem("  Chat Logger")
        self.nav_list.addItem("  Vault Storage")
        self.nav_list.addItem("  Kill Book")
        self.nav_list.addItem("  UW Node Solver")
        self.nav_list.addItem("  Settings")
        self.nav_list.currentRowChanged.connect(self.switch_section)
        sidebar_layout.addWidget(self.nav_list, 1)

        # Live Game Status & Action Controls (Minimal Flat Display)
        self.status_txt = QLabel("🟡 Searching for meridian.exe...")
        self.status_txt.setStyleSheet("font-size: 10px; font-weight: 700; color: #f59e0b; padding: 2px 0px;")
        self.status_txt.setWordWrap(True)
        sidebar_layout.addWidget(self.status_txt)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(4)

        self.sync_btn = QPushButton("🔄 Sync")
        self.sync_btn.setFixedHeight(26)
        self.sync_btn.setProperty("class", "WebBtnPrimary")
        self.sync_btn.setToolTip("Triggers live memory scraping cycle.")
        self.sync_btn.clicked.connect(self.trigger_manual_sync)
        btn_row.addWidget(self.sync_btn, 1)

        self.reset_layout_btn = QPushButton("↺ Reset")
        self.reset_layout_btn.setFixedHeight(26)
        self.reset_layout_btn.setProperty("class", "WebBtnSecondary")
        self.reset_layout_btn.setToolTip("Reset dashboard layout")
        self.reset_layout_btn.clicked.connect(self.reset_layout_config)
        btn_row.addWidget(self.reset_layout_btn, 1)

        sidebar_layout.addLayout(btn_row)

        main_layout.addWidget(sidebar)

        # --------------------------------------------------------------
        # 2. MAIN WORKSPACE (STACKED WIDGET & TOAST BANNER)
        # --------------------------------------------------------------
        workspace_container = QWidget()
        workspace_layout = QVBoxLayout(workspace_container)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)

        # Non-intrusive Update Toast Notification Overlay Banner
        self.update_toast_widget = QFrame()
        self.update_toast_widget.setObjectName("UpdateToastBanner")
        self.update_toast_widget.setVisible(False)
        self.update_toast_widget.setStyleSheet("""
            QFrame#UpdateToastBanner {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1e1b4b, stop:1 #311b92);
                border: 1px solid #6366f1;
                border-radius: 6px;
                margin: 6px 10px 4px 10px;
            }
        """)
        toast_layout = QHBoxLayout(self.update_toast_widget)
        toast_layout.setContentsMargins(12, 6, 12, 6)
        toast_layout.setSpacing(12)

        self.toast_msg_lbl = QLabel("🚀 Software Update Available!")
        self.toast_msg_lbl.setStyleSheet("font-size: 11px; font-weight: 700; color: #f8fafc;")

        self.toast_action_btn = QPushButton("🚀 Update Now")
        self.toast_action_btn.setCursor(Qt.PointingHandCursor)
        self.toast_action_btn.setStyleSheet("""
            QPushButton {
                background-color: #6366f1; color: #ffffff; font-size: 10px; font-weight: 800;
                border-radius: 4px; padding: 4px 12px; border: none; min-height: 22px;
            }
            QPushButton:hover { background-color: #4f46e5; }
        """)

        self.toast_dismiss_btn = QPushButton("✖ Dismiss")
        self.toast_dismiss_btn.setCursor(Qt.PointingHandCursor)
        self.toast_dismiss_btn.setStyleSheet("""
            QPushButton {
                background-color: #334155; color: #cbd5e1; font-size: 10px; font-weight: 700;
                border-radius: 4px; padding: 4px 12px; border: 1px solid #475569; min-height: 22px;
            }
            QPushButton:hover { background-color: #475569; color: #f8fafc; }
        """)
        self.toast_dismiss_btn.clicked.connect(self.hide_update_toast)

        toast_layout.addWidget(self.toast_msg_lbl, 1)
        toast_layout.addWidget(self.toast_action_btn)
        toast_layout.addWidget(self.toast_dismiss_btn)

        workspace_layout.addWidget(self.update_toast_widget)

        self.stacked_widget = QStackedWidget()

        # Section 1: Dashboard Page
        self.page_dashboard = self.build_dashboard_page()
        self.stacked_widget.addWidget(self.page_dashboard)

        # Section 2: Progression Page
        self.page_progression = self.build_progression_page()
        self.stacked_widget.addWidget(self.page_progression)

        # Section 3: Reagents Usage Page
        self.page_reagents = self.build_reagents_page()
        self.stacked_widget.addWidget(self.page_reagents)

        # Section 4: Shortcuts Page
        self.page_shortcuts = self.build_shortcuts_page()
        self.stacked_widget.addWidget(self.page_shortcuts)

        # Section 5: Chat Logger Page
        self.page_chat = self.build_chat_logger_page()
        self.stacked_widget.addWidget(self.page_chat)

        # Section 6: Vault Storage Page
        self.page_vault = self.build_vault_page()
        self.stacked_widget.addWidget(self.page_vault)

        # Section 7: Kill Book Page
        self.page_killbook = self.build_killbook_page()
        self.stacked_widget.addWidget(self.page_killbook)

        # Section 8: UW Node Solver Page
        self.page_uwnode = UWNodeSolverWidget()
        self.stacked_widget.addWidget(self.page_uwnode)

        # Section 9: Settings Preferences Page
        self.page_settings = self.build_settings_page()
        self.stacked_widget.addWidget(self.page_settings)

        workspace_layout.addWidget(self.stacked_widget, 1)
        main_layout.addWidget(workspace_container, 1)

        # --------------------------------------------------------------
        # 3. RIGHT COLLAPSIBLE SIDE PANEL (Who List, Game Clock & Bottom Dock)
        # --------------------------------------------------------------
        self.right_panel = QWidget()
        self.right_panel.setObjectName("RightPanelWidget")
        self.right_panel.setMinimumWidth(220)
        self.right_panel.setMaximumWidth(700)

        self.right_panel_outer_layout = QHBoxLayout(self.right_panel)
        self.right_panel_outer_layout.setContentsMargins(0, 0, 0, 0)
        self.right_panel_outer_layout.setSpacing(0)

        # Super Low Profile Hide Button on Middle-Left edge of Dock Panel
        hide_strip = QVBoxLayout()
        hide_strip.setContentsMargins(0, 0, 0, 0)
        hide_strip.setSpacing(0)
        hide_strip.addStretch()

        self.collapse_btn = QPushButton("▶")
        self.collapse_btn.setFixedSize(14, 48)
        self.collapse_btn.setToolTip("Hide Dock Panel")
        self.collapse_btn.setCursor(Qt.PointingHandCursor)
        self.collapse_btn.setStyleSheet("""
            QPushButton {
                font-size: 10px; font-weight: 800; color: #94a3b8;
                background: #0f172a; border: 1px solid #334155;
                border-top-left-radius: 4px; border-bottom-left-radius: 4px;
                border-right: none; padding: 0px;
            }
            QPushButton:hover {
                color: #f8fafc; background: #1e293b; border-color: #38bdf8;
            }
        """)
        self.collapse_btn.clicked.connect(self.toggle_right_panel)
        hide_strip.addWidget(self.collapse_btn)
        hide_strip.addStretch()
        self.right_panel_outer_layout.addLayout(hide_strip)

        # Inner Panel Container for Header & Content
        right_panel_inner = QWidget()
        self.right_panel_layout = QVBoxLayout(right_panel_inner)
        self.right_panel_layout.setContentsMargins(0, 0, 0, 0)
        self.right_panel_layout.setSpacing(0)
        self.right_panel_outer_layout.addWidget(right_panel_inner, 1)

        # Minimalist Panel Header
        rp_hdr = QHBoxLayout()
        rp_hdr.setContentsMargins(8, 6, 8, 6)
        rp_hdr.setSpacing(6)

        self.rp_title = QLabel("DOCK PANEL")
        self.rp_title.setStyleSheet("font-size: 11px; font-weight: 800; color: #94a3b8; letter-spacing: 0.8px;")
        rp_hdr.addWidget(self.rp_title)
        rp_hdr.addStretch()

        self.dock_desktop_btn = QPushButton("↗")
        self.dock_desktop_btn.setFixedSize(26, 24)
        self.dock_desktop_btn.setToolTip("Dock Panel to Desktop Screen as an AppBar")
        self.dock_desktop_btn.setStyleSheet("""
            QPushButton {
                font-size: 11px; font-weight: 700; color: #94a3b8; background: transparent;
                border: 1px solid #334155; border-radius: 4px; padding: 0px;
            }
            QPushButton:hover {
                color: #f8fafc; background: #1e293b; border-color: #475569;
            }
        """)
        self.dock_desktop_btn.clicked.connect(self.toggle_desktop_dock)
        rp_hdr.addWidget(self.dock_desktop_btn)
        self.right_panel_layout.addLayout(rp_hdr)

        # Panel Content Container
        self.right_panel_content = QWidget()
        rpc_layout = QVBoxLayout(self.right_panel_content)
        rpc_layout.setContentsMargins(4, 4, 4, 4)
        rpc_layout.setSpacing(6)

        # 1. LOW-PROFILE TOP TIME CLOCK BAR (Fixed Top)
        self.time_bar = QFrame()
        self.time_bar.setObjectName("DockTimeBar")
        self.time_bar.setStyleSheet("""
            QFrame#DockTimeBar {
                background-color: #0b1120;
                border: 1px solid #1e293b;
                border-radius: 6px;
            }
        """)
        tb_layout = QHBoxLayout(self.time_bar)
        tb_layout.setContentsMargins(6, 4, 6, 4)
        tb_layout.setSpacing(6)

        clock_lbl = QLabel("🕒")
        clock_lbl.setStyleSheet("font-size: 11px; color: #94a3b8;")

        self.dock_game_time_lbl = QLabel("12:00:00 ☀️")
        self.dock_game_time_lbl.setAlignment(Qt.AlignCenter)
        self.dock_game_time_lbl.setStyleSheet("""
            font-family: 'Consolas', monospace;
            font-size: 13px;
            font-weight: 800;
            color: #f8fafc;
            background: transparent;
            border: none;
            padding: 0px;
        """)

        self.time_format_btn = QPushButton("12/24")
        self.time_format_btn.setFixedSize(40, 20)
        self.time_format_btn.setToolTip("Toggle 12-hour or 24-hour time format")
        self.time_format_btn.setStyleSheet("""
            QPushButton {
                font-size: 9px; font-weight: 700; color: #94a3b8; background: #0f172a;
                border: 1px solid #334155; border-radius: 3px; padding: 0px;
            }
            QPushButton:hover {
                color: #f8fafc; background: #334155;
            }
        """)
        self.time_format_btn.clicked.connect(self.toggle_clock_format)

        tb_layout.addWidget(clock_lbl)
        tb_layout.addWidget(self.dock_game_time_lbl, 1)
        tb_layout.addWidget(self.time_format_btn)

        rpc_layout.addWidget(self.time_bar, 0)

        # 2. ONLINE PLAYERS (WHO'S ONLINE) - Positioned under Time, Expands to fill maximum available panel space
        who_card = ReorderableCard("WHO'S ONLINE", grid_container=None, default_colspan=1, is_draggable=False)
        who_card.is_expanding = True
        who_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.who_count_badge = QLabel("0 Online")
        self.who_count_badge.setStyleSheet("""
            background-color: #064e3b;
            color: #94a3b8;
            font-size: 10px;
            font-weight: 800;
            padding: 2px 8px;
            border-radius: 6px;
        """)
        who_card.add_header_widget(self.who_count_badge)

        self.who_search_input = QLineEdit()
        self.who_search_input.setPlaceholderText("Filter online players...")
        self.who_search_input.textChanged.connect(lambda: self.update_wholist_gui(self.wholist_data))
        who_card.content_layout.addWidget(self.who_search_input)

        self.who_list_widget = QListWidget()
        self.who_list_widget.setObjectName("WhoListWidget")
        self.who_list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.who_list_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.who_list_widget.setStyleSheet("border: none; background: transparent; padding: 0px; margin: 0px;")
        self.who_list_widget.itemDoubleClicked.connect(lambda item: self.open_dm_with_player(item.data(Qt.UserRole)) if item and item.data(Qt.UserRole) else None)
        self.who_list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.who_list_widget.customContextMenuRequested.connect(lambda pos: self.on_who_list_context_menu(pos, self.who_list_widget))
        who_card.content_layout.addWidget(self.who_list_widget, 1)

        # Docked Offline Container at the foot of Player List (Always collapsed by default)
        self.who_offline_dock = QFrame()
        self.who_offline_dock.setObjectName("WhoOfflineDock")
        self.who_offline_dock.setStyleSheet("""
            QFrame#WhoOfflineDock {
                background-color: #090f1d;
                border-top: 1px solid #1e293b;
                border-radius: 4px;
                margin-top: 2px;
            }
        """)
        self.who_offline_layout = QVBoxLayout(self.who_offline_dock)
        self.who_offline_layout.setContentsMargins(0, 0, 0, 0)
        self.who_offline_layout.setSpacing(0)

        self.who_offline_hdr_widget = QWidget()
        self.who_offline_hdr_widget.setCursor(Qt.PointingHandCursor)
        self.who_offline_hdr_widget.setStyleSheet("background: transparent;")
        wo_hdr_layout = QHBoxLayout(self.who_offline_hdr_widget)
        wo_hdr_layout.setContentsMargins(6, 4, 6, 4)
        wo_hdr_layout.setSpacing(6)

        self.who_offline_arrow_lbl = QLabel("▶")
        self.who_offline_arrow_lbl.setStyleSheet("font-size: 9px; font-weight: 800; color: #64748b; background: transparent;")
        wo_hdr_layout.addWidget(self.who_offline_arrow_lbl)

        self.who_offline_title_lbl = QLabel("OFFLINE")
        self.who_offline_title_lbl.setStyleSheet("font-size: 10px; font-weight: 800; color: #64748b; letter-spacing: 0.6px; background: transparent;")
        wo_hdr_layout.addWidget(self.who_offline_title_lbl)
        wo_hdr_layout.addStretch()

        self.who_offline_cnt_badge = QLabel("0 Offline")
        self.who_offline_cnt_badge.setStyleSheet("background-color: #1e293b; color: #94a3b8; font-size: 9px; font-weight: 800; border-radius: 4px; padding: 1px 5px;")
        wo_hdr_layout.addWidget(self.who_offline_cnt_badge)

        self.who_offline_hdr_widget.mousePressEvent = lambda e: self.toggle_group_collapse("__OFFLINE__") if e.button() == Qt.LeftButton else None
        self.who_offline_layout.addWidget(self.who_offline_hdr_widget)

        self.who_offline_list_widget = QListWidget()
        self.who_offline_list_widget.setObjectName("WhoOfflineListWidget")
        self.who_offline_list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.who_offline_list_widget.setMaximumHeight(160)
        self.who_offline_list_widget.setVisible(False)
        self.who_offline_list_widget.setStyleSheet("border: none; background: transparent; padding: 0px; margin: 0px;")
        self.who_offline_list_widget.itemDoubleClicked.connect(lambda item: self.open_dm_with_player(item.data(Qt.UserRole)) if item and item.data(Qt.UserRole) else None)
        self.who_offline_list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.who_offline_list_widget.customContextMenuRequested.connect(lambda pos: self.on_who_list_context_menu(pos, self.who_offline_list_widget))
        self.who_offline_layout.addWidget(self.who_offline_list_widget)

        who_card.content_layout.addWidget(self.who_offline_dock, 0)

        rpc_layout.addWidget(who_card, 1)

        # 3. BOTTOM ANCHORED CONTAINER (Legacy Minimalistic Status Footer)
        dock_footer_container = QFrame()
        dock_footer_container.setObjectName("DockFooterContainer")
        dock_footer_container.setStyleSheet("""
            QFrame#DockFooterContainer {
                background-color: #0b1120;
                border-top: 1px solid #1e293b;
                border-left: none;
                border-right: none;
                border-bottom: none;
            }
        """)
        dock_footer_layout = QVBoxLayout(dock_footer_container)
        dock_footer_layout.setContentsMargins(10, 8, 10, 10)
        dock_footer_layout.setSpacing(4)

        # 1. GPS Navigation Header
        gps_head = QLabel("🧭 GPS NAVIGATION")
        gps_head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gps_head.setStyleSheet("font-size: 10px; font-weight: 800; color: #64748b; background: transparent; padding-top: 2px;")
        dock_footer_layout.addWidget(gps_head)

        # 2. Current Location
        self.dock_gps_loc_lbl = QLabel("Unknown Location")
        self.dock_gps_loc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.dock_gps_loc_lbl.setWordWrap(True)
        self.dock_gps_loc_lbl.setStyleSheet("font-size: 12px; font-weight: 800; color: #4ade80; background: transparent;")
        dock_footer_layout.addWidget(self.dock_gps_loc_lbl)

        # 3. PVP Status Icons Placeholder Row
        pvp_status_box = QHBoxLayout()
        pvp_status_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pvp_status_box.setContentsMargins(0, 1, 0, 1)
        pvp_status_box.setSpacing(6)

        self.dock_pvp_icon_1 = QLabel()
        self.dock_pvp_icon_2 = QLabel()
        self.dock_pvp_icon_1.setStyleSheet("background: transparent; padding: 0;")
        self.dock_pvp_icon_2.setStyleSheet("background: transparent; padding: 0;")
        pvp_status_box.addWidget(self.dock_pvp_icon_1)
        pvp_status_box.addWidget(self.dock_pvp_icon_2)

        dock_footer_layout.addLayout(pvp_status_box)

        # 4. Active Route Instruction
        self.dock_gps_dir_lbl = QLabel("No active route")
        self.dock_gps_dir_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.dock_gps_dir_lbl.setWordWrap(True)
        self.dock_gps_dir_lbl.setStyleSheet("font-size: 10px; font-weight: 700; color: #f8fafc; background: transparent;")
        dock_footer_layout.addWidget(self.dock_gps_dir_lbl)

        # Aliases & compatibility objects
        self.dock_gps_route_lbl = self.dock_gps_dir_lbl
        self.dock_gps_step_lbl = self.dock_gps_dir_lbl
        self.dock_gps_detail_lbl = QLabel()
        self.dock_gps_detail_lbl.hide()
        self.dock_gps_status_lbl = QLabel("READY")
        self.dock_gps_status_lbl.hide()
        self.dock_gps_target_lbl = QLabel("🎯 Target: None")
        self.dock_gps_target_lbl.hide()

        self.dock_gps_search = QLineEdit()
        self.dock_gps_search.hide()
        self.dock_gps_toggle_btn = QPushButton("▶")
        self.dock_gps_toggle_btn.hide()
        self.dock_gps_start_btn = self.dock_gps_toggle_btn
        self.dock_gps_stop_btn = self.dock_gps_toggle_btn

        # Subtle Divider 1
        sep1 = QFrame()
        sep1.setFixedHeight(1)
        sep1.setStyleSheet("background-color: #1e293b; border: none;")
        dock_footer_layout.addWidget(sep1)

        # 5. Status Rows (IMPROVES, BANK M, BANK I)
        def add_footer_status_row(parent_layout, label_text, icon_prefix):
            row_box = QHBoxLayout()
            row_box.setContentsMargins(0, 1, 0, 1)

            lbl_left = QLabel(f"{icon_prefix} {label_text}")
            lbl_left.setStyleSheet("font-size: 10px; font-weight: 600; color: #94a3b8; background: transparent;")

            val_right = QLabel("---")
            val_right.setAlignment(Qt.AlignmentFlag.AlignRight)
            val_right.setStyleSheet("font-size: 10px; font-weight: 800; color: #f8fafc; background: transparent;")

            row_box.addWidget(lbl_left)
            row_box.addStretch()
            row_box.addWidget(val_right)
            parent_layout.addLayout(row_box)
            return val_right

        self.dock_improves_lbl = add_footer_status_row(dock_footer_layout, "IMPROVES:", "📈")
        self.dock_improves_lbl.setText("0")

        self.dock_bank_mainland_lbl = add_footer_status_row(dock_footer_layout, "BANK (M):", "💰")
        self.dock_bank_mainland_lbl.setText("0 sh")

        self.dock_bank_island_lbl = add_footer_status_row(dock_footer_layout, "BANK (I):", "🌴")
        self.dock_bank_island_lbl.setText("0 sh")

        self.dock_bank_total_lbl = QLabel()
        self.dock_bank_total_lbl.hide()

        # Subtle Divider 2
        sep2 = QFrame()
        sep2.setFixedHeight(1)
        sep2.setStyleSheet("background-color: #1e293b; border: none;")
        dock_footer_layout.addWidget(sep2)

        # 6. Bag Space Row & Bar
        bag_row_box = QHBoxLayout()
        bag_row_box.setContentsMargins(0, 1, 0, 1)

        bag_left = QLabel("🎒 BAG SPACE:")
        bag_left.setStyleSheet("font-size: 10px; font-weight: 600; color: #94a3b8; background: transparent;")

        self.dock_inv_sat_lbl = QLabel("0.0%")
        self.dock_inv_sat_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.dock_inv_sat_lbl.setStyleSheet("font-size: 10px; font-weight: 800; color: #f8fafc; background: transparent;")

        bag_row_box.addWidget(bag_left)
        bag_row_box.addStretch()
        bag_row_box.addWidget(self.dock_inv_sat_lbl)

        dock_footer_layout.addLayout(bag_row_box)

        self.dock_inv_bar = QProgressBar()
        self.dock_inv_bar.setFixedHeight(4)
        self.dock_inv_bar.setValue(0)
        self.dock_inv_bar.setTextVisible(False)
        self.dock_inv_bar.setStyleSheet("""
            QProgressBar {
                background-color: #1e293b;
                border: none;
                border-radius: 2px;
            }
            QProgressBar::chunk {
                background-color: #22c55e;
                border-radius: 2px;
            }
        """)
        dock_footer_layout.addWidget(self.dock_inv_bar)

        self.dock_inv_weight_lbl = QLabel()
        self.dock_inv_weight_lbl.hide()
        self.dock_inv_bulk_lbl = QLabel()
        self.dock_inv_bulk_lbl.hide()
        self.dock_inv_count_lbl = QLabel()
        self.dock_inv_count_lbl.hide()

        self.dock_sub_grid = None

        rpc_layout.addWidget(dock_footer_container, 0)

        self.right_panel_layout.addWidget(self.right_panel_content)

        # Super Low Profile Reveal Button on Side of Application (when panel is hidden)
        self.reveal_container = QWidget()
        reveal_layout = QVBoxLayout(self.reveal_container)
        reveal_layout.setContentsMargins(0, 0, 0, 0)
        reveal_layout.setSpacing(0)
        reveal_layout.addStretch()

        self.reveal_btn = QPushButton("◀")
        self.reveal_btn.setFixedSize(14, 48)
        self.reveal_btn.setToolTip("Reveal Dock Panel")
        self.reveal_btn.setCursor(Qt.PointingHandCursor)
        self.reveal_btn.setStyleSheet("""
            QPushButton {
                font-size: 10px; font-weight: 800; color: #94a3b8;
                background: #0f172a; border: 1px solid #334155;
                border-top-left-radius: 4px; border-bottom-left-radius: 4px;
                border-right: none; padding: 0px;
            }
            QPushButton:hover {
                color: #f8fafc; background: #1e293b; border-color: #38bdf8;
            }
        """)
        self.reveal_btn.clicked.connect(self.toggle_right_panel)
        reveal_layout.addWidget(self.reveal_btn)
        reveal_layout.addStretch()
        self.reveal_container.hide()

        self.content_splitter = QSplitter(Qt.Horizontal)
        self.content_splitter.setObjectName("ContentSplitter")
        self.content_splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #1e293b;
                width: 5px;
            }
            QSplitter::handle:hover {
                background-color: #3b82f6;
            }
        """)
        self.content_splitter.addWidget(self.stacked_widget)
        self.content_splitter.addWidget(self.reveal_container)
        self.content_splitter.addWidget(self.right_panel)
        self.content_splitter.setCollapsible(0, False)
        self.content_splitter.setCollapsible(1, False)
        self.content_splitter.setCollapsible(2, False)
        self.content_splitter.splitterMoved.connect(self.on_dock_splitter_moved)

        main_layout.addWidget(self.content_splitter, 1)

        # Load saved view layout configuration & restore window size/position
        self.load_layout_config()
        self.restore_window_position_and_size()
        self.update_bank_ui()
        self.load_kill_book()
        self.load_vault_cache()

        # Default to Dashboard
        self.nav_list.setCurrentRow(0)

    def on_dock_splitter_moved(self, pos, index):
        if not getattr(self, 'right_panel_collapsed', False) and not getattr(self, 'is_desktop_docked', False):
            w = self.right_panel.width()
            if w >= 200:
                self.dock_panel_width = w
                if hasattr(self, 'standalone_dock') and self.standalone_dock:
                    self.standalone_dock.dock_width = w
                self.save_layout_config()

    def toggle_desktop_dock(self):
        """Toggles docking ONLY the right Dock Panel to the desktop edge as a Windows AppBar.
        When docked, Windows adjusts the desktop work area so maximized windows resize around it."""
        try:
            reset_desktop_workarea()
        except Exception:
            pass

        if not self.is_desktop_docked:
            print("[M59-DOCK] Detaching Dock Panel to standalone desktop AppBar...", flush=True)

            if not self.standalone_dock:
                self.standalone_dock = M59StandaloneDockWindow(self)

            self.standalone_dock.dock_width = getattr(self, 'dock_panel_width', 340)

            # Detach right_panel_content from main application and pass to standalone dock
            self.right_panel_layout.removeWidget(self.right_panel_content)
            self.right_panel.hide()
            if hasattr(self, 'reveal_container'):
                self.reveal_container.hide()

            self.standalone_dock.attach_dock_content(self.right_panel_content)
            self.is_desktop_docked = True

            if hasattr(self, 'hdr_dock_btn'):
                self.hdr_dock_btn.setText("↙")
                self.hdr_dock_btn.setToolTip("Undock Panel and return to Companion application")
            if hasattr(self, 'dock_desktop_btn'):
                self.dock_desktop_btn.setText("↙")
                self.dock_desktop_btn.setToolTip("Undock Panel and return to Companion application")

            self.save_layout_config()
            print("[M59-DOCK] Standalone Dock Panel successfully docked as Windows AppBar.", flush=True)
        else:
            print("[M59-DOCK] Undocking Standalone Dock Panel back to main application window...", flush=True)
            if self.standalone_dock:
                self.standalone_dock.undock_desktop()

    def on_standalone_dock_undocked(self):
        """Callback triggered when the standalone dock panel is undocked or closed."""
        try:
            reset_desktop_workarea()
        except Exception:
            pass

        if self.is_desktop_docked:
            self.is_desktop_docked = False

            if self.standalone_dock:
                self.dock_panel_width = self.standalone_dock.dock_width
                if hasattr(self, 'content_splitter'):
                    total_w = max(800, self.width())
                    self.content_splitter.setSizes([max(400, total_w - self.dock_panel_width), 14, self.dock_panel_width])
                else:
                    self.right_panel.setFixedWidth(self.dock_panel_width)

            # Return right_panel_content back to main right_panel layout
            if hasattr(self, 'right_panel_content') and self.right_panel_content:
                self.right_panel_content.setParent(self.right_panel)
                self.right_panel_layout.addWidget(self.right_panel_content)
                self.right_panel_content.show()
                self.right_panel.show()
                if hasattr(self, 'reveal_container'):
                    self.reveal_container.hide()
                self.right_panel_collapsed = False

            if hasattr(self, 'hdr_dock_btn'):
                self.hdr_dock_btn.setText("↗")
                self.hdr_dock_btn.setToolTip("Dock Panel to Desktop Screen as an AppBar")
            if hasattr(self, 'dock_desktop_btn'):
                self.dock_desktop_btn.setText("↗")
                self.dock_desktop_btn.setToolTip("Dock Panel to Desktop Screen as an AppBar")

            # Refresh WhoList rendering for restored parent
            if hasattr(self, 'update_wholist_gui') and hasattr(self, 'wholist_data'):
                self.update_wholist_gui(self.wholist_data)

            self.save_layout_config()
            print("[M59-DOCK] Dock Panel restored to main application window.", flush=True)

    def get_layout_config_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "m59_layout_config.json")

    def save_layout_config(self):
        try:
            config = {
                "dock_panel_width": getattr(self, 'dock_panel_width', 340),
                "is_desktop_docked": getattr(self, 'is_desktop_docked', False),
                "dashboard_grid": [],
                "dock_grid": []
            }
            if hasattr(self, 'dashboard_grid') and self.dashboard_grid:
                for c in self.dashboard_grid.cards:
                    config["dashboard_grid"].append({
                        "title": c.title_text,
                        "span": getattr(c, 'column_span', 6),
                        "width": getattr(c, 'custom_width', None),
                        "height": getattr(c, 'custom_height', None)
                    })
            if hasattr(self, 'dock_grid') and self.dock_grid:
                for c in self.dock_grid.cards:
                    config["dock_grid"].append({
                        "title": c.title_text,
                        "span": getattr(c, 'column_span', 1),
                        "width": getattr(c, 'custom_width', None),
                        "height": getattr(c, 'custom_height', None)
                    })
            if hasattr(self, 'dock_sub_grid') and self.dock_sub_grid:
                config["dock_sub_grid"] = [c.title_text for c in self.dock_sub_grid.cards]
            with open(self.get_layout_config_path(), "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"[M59-LAYOUT] Error saving layout config: {e}", flush=True)

    def load_layout_config(self):
        path = self.get_layout_config_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                config = json.load(f)

            if "dock_panel_width" in config and config["dock_panel_width"]:
                self.dock_panel_width = config["dock_panel_width"]
                if hasattr(self, 'content_splitter'):
                    total_w = max(800, self.width())
                    self.content_splitter.setSizes([max(400, total_w - self.dock_panel_width), self.dock_panel_width])
                elif hasattr(self, 'right_panel') and not getattr(self, 'right_panel_collapsed', False):
                    self.right_panel.setFixedWidth(self.dock_panel_width)
                if hasattr(self, 'standalone_dock') and self.standalone_dock:
                    self.standalone_dock.dock_width = self.dock_panel_width

            if hasattr(self, 'dashboard_grid') and self.dashboard_grid and "dashboard_grid" in config:
                card_map = {c.title_text: c for c in self.dashboard_grid.cards}
                new_cards = []
                for item in config["dashboard_grid"]:
                    t = item.get("title")
                    if t in card_map:
                        c = card_map.pop(t)
                        if "span" in item and item["span"]:
                            c.column_span = item["span"]
                        c.custom_width = item.get("width")
                        c.custom_height = item.get("height")
                        new_cards.append(c)
                new_cards.extend(card_map.values())
                self.dashboard_grid.cards = new_cards
                self.dashboard_grid.refresh_layout()

            if hasattr(self, 'dock_grid') and self.dock_grid and "dock_grid" in config:
                card_map = {c.title_text: c for c in self.dock_grid.cards}
                new_cards = []
                for item in config["dock_grid"]:
                    t = item.get("title")
                    if t in card_map:
                        c = card_map.pop(t)
                        if "span" in item and item["span"]:
                            c.column_span = item["span"]
                        c.custom_width = item.get("width")
                        c.custom_height = item.get("height")
                        new_cards.append(c)
                new_cards.extend(card_map.values())
                self.dock_grid.cards = new_cards
                self.dock_grid.refresh_layout()

            if hasattr(self, 'dock_sub_grid') and self.dock_sub_grid and "dock_sub_grid" in config:
                sub_map = {c.title_text: c for c in self.dock_sub_grid.cards}
                new_sub = []
                for t in config["dock_sub_grid"]:
                    if t in sub_map:
                        new_sub.append(sub_map.pop(t))
                new_sub.extend(sub_map.values())
                self.dock_sub_grid.cards = new_sub
                self.dock_sub_grid.refresh_layout()

            # Automatically restore standalone desktop dock state if it was docked on last exit
            if config.get("is_desktop_docked", False):
                QTimer.singleShot(200, lambda: self.toggle_desktop_dock() if not getattr(self, 'is_desktop_docked', False) else None)

            print("[M59-LAYOUT] Custom layout configuration successfully loaded.", flush=True)
        except Exception as e:
            print(f"[M59-LAYOUT] Error loading layout config: {e}", flush=True)

    def reset_layout_config(self):
        try:
            path = self.get_layout_config_path()
            if os.path.exists(path):
                os.remove(path)

            if hasattr(self, 'dashboard_grid') and self.dashboard_grid:
                for c in self.dashboard_grid.cards:
                    c.column_span = getattr(c, 'default_colspan', 6)
                    c.custom_width = None
                    c.custom_height = None

                title_order = [
                    "CHARACTER IDENTITY & OVERVIEW",
                    "GPS NAVIGATION",
                    "SCHOOL PROGRESSION",
                    "VAULT MANAGEMENT",
                    "SESSION KILLS (COMBAT)",
                    "SESSION IMPROVES (SKILLS & SPELLS)",
                    "CARRIED ITEMS LEDGER",
                    "UW NODE MANA SOLVER"
                ]
                card_map = {c.title_text: c for c in self.dashboard_grid.cards}
                self.dashboard_grid.cards = [card_map[t] for t in title_order if t in card_map] + [c for t, c in card_map.items() if t not in title_order]
                self.dashboard_grid.refresh_layout()

            if hasattr(self, 'dock_grid') and self.dock_grid:
                for c in self.dock_grid.cards:
                    c.custom_width = None
                    c.custom_height = None

                title_order = [
                    "WORLD CLOCK",
                    "WHO'S ONLINE",
                    "BAG SPACE & LOAD"
                ]
                card_map = {c.title_text: c for c in self.dock_grid.cards}
                self.dock_grid.cards = [card_map[t] for t in title_order if t in card_map] + [c for t, c in card_map.items() if t not in title_order]
                self.dock_grid.refresh_layout()

            if hasattr(self, 'dock_sub_grid') and self.dock_sub_grid:
                title_order = [
                    "GPS NAVIGATOR",
                    "BAG SPACE & LOAD",
                    "BANK BALANCES"
                ]
                sub_map = {c.title_text: c for c in self.dock_sub_grid.cards}
                self.dock_sub_grid.cards = [sub_map[t] for t in title_order if t in sub_map] + [c for t, c in sub_map.items() if t not in title_order]
                self.dock_sub_grid.refresh_layout()

            self.dock_panel_width = 340
            if hasattr(self, 'content_splitter'):
                total_w = max(800, self.width())
                self.content_splitter.setSizes([max(400, total_w - 340), 340])
            elif hasattr(self, 'right_panel'):
                self.right_panel.setFixedWidth(340)
            if hasattr(self, 'standalone_dock') and self.standalone_dock:
                self.standalone_dock.dock_width = 340

            print("[M59-LAYOUT] Dashboard layout reset to default.", flush=True)
        except Exception as e:
            print(f"[M59-LAYOUT] Error resetting layout: {e}", flush=True)

    def center_on_screen(self):
        """Centers main application window on primary screen."""
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            ww = min(self.width(), max(1200, geo.width() - 80))
            wh = min(self.height(), max(750, geo.height() - 80))
            self.resize(ww, wh)
            x = geo.x() + (geo.width() - ww) // 2
            y = geo.y() + (geo.height() - wh) // 2
            self.move(max(geo.x(), x), max(geo.y(), y))

    def restore_window_position_and_size(self):
        """Restores saved window position and size from GUI settings, defaulting to centered if not set or invalid."""
        cfg = self.load_gui_settings()
        pos_x = cfg.get("window_x")
        pos_y = cfg.get("window_y")
        width = cfg.get("window_width")
        height = cfg.get("window_height")
        is_max = cfg.get("window_maximized", False)

        screen = QApplication.primaryScreen()
        screen_geo = screen.availableGeometry() if screen else None

        if width and height and width >= 400 and height >= 300:
            if screen_geo:
                width = min(width, screen_geo.width())
                height = min(height, screen_geo.height())
            self.resize(width, height)

        if pos_x is not None and pos_y is not None and screen_geo:
            # Check if saved position keeps the top-left corner visible on screen
            if (screen_geo.x() - 50 <= pos_x <= screen_geo.x() + screen_geo.width() - 100 and
                screen_geo.y() - 50 <= pos_y <= screen_geo.y() + screen_geo.height() - 100):
                self.move(pos_x, pos_y)
            else:
                self.center_on_screen()
        else:
            self.center_on_screen()

        if is_max:
            self.showMaximized()

    def save_window_position_and_size(self):
        """Saves current window position and size to gui_settings.json upon exit."""
        try:
            if self.isMaximized():
                self.save_gui_settings({"window_maximized": True})
            else:
                pos = self.pos()
                sz = self.size()
                self.save_gui_settings({
                    "window_x": pos.x(),
                    "window_y": pos.y(),
                    "window_width": sz.width(),
                    "window_height": sz.height(),
                    "window_maximized": False
                })
        except Exception as ex:
            print(f"[M59-GUI] Error saving window geometry: {ex}", flush=True)

    def closeEvent(self, event):
        """Ensure window position, size, layout config, standalone AppBar, Frida monitors, background threads, and floating dialogs are cleanly saved and shutdown when exiting."""
        self._is_shutting_down = True
        try:
            self.save_window_position_and_size()
            self.save_layout_config()
        except Exception as e:
            print(f"[M59-EXIT] Error saving geometry/layout: {e}", flush=True)

        # 1. Unhook global OS keyboard hooks
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass

        # 2. Stop InstanceManager background lifecycle thread
        if hasattr(self, 'lifecycle') and self.lifecycle:
            try:
                self.lifecycle.stop()
            except Exception:
                pass
            self.lifecycle = None

        # 3. Stop WhoList Frida Monitor thread
        if hasattr(self, 'wholist_monitor') and self.wholist_monitor:
            try:
                self.wholist_monitor.stop()
            except Exception:
                pass
            self.wholist_monitor = None

        # 4. Stop Clock and UI Timers
        if hasattr(self, 'clock_timer') and self.clock_timer:
            try:
                self.clock_timer.stop()
            except Exception:
                pass

        # 5. Close PKFrame
        if hasattr(self, 'pk_frame') and self.pk_frame:
            try:
                self.pk_frame.close()
            except Exception:
                pass
            self.pk_frame = None

        # 6. Close Splash Overlay Screen
        try:
            self.hide_splash_overlay()
        except Exception:
            pass

        # 7. Close Standalone Desktop Dock Window
        if hasattr(self, 'standalone_dock') and self.standalone_dock:
            try:
                if getattr(self.standalone_dock, 'is_docked', False):
                    self.standalone_dock.undock_desktop()
                self.standalone_dock.hide()
                self.standalone_dock.close()
            except Exception:
                pass
            self.standalone_dock = None

        # 8. Close active Direct Message Dialogs
        if hasattr(self, 'active_dm_dialogs'):
            for dlg in list(self.active_dm_dialogs.values()):
                try:
                    dlg.hide()
                    dlg.close()
                except Exception:
                    pass
            self.active_dm_dialogs.clear()

        # 9. Close Floating Chat Box
        if hasattr(self, 'active_floating_chat') and self.active_floating_chat:
            try:
                self.active_floating_chat.hide()
                self.active_floating_chat.close()
            except Exception:
                pass
            self.active_floating_chat = None

        # 10. Close Floating Elude Bar
        if hasattr(self, 'active_elude_bar') and self.active_elude_bar:
            try:
                self.active_elude_bar.hide()
                self.active_elude_bar.close()
            except Exception:
                pass
            self.active_elude_bar = None

        # 11. Close Floating Morph Bar
        if hasattr(self, 'active_morph_bar') and self.active_morph_bar:
            try:
                self.active_morph_bar.hide()
                self.active_morph_bar.close()
            except Exception:
                pass
            self.active_morph_bar = None

        # 12. Close Floating Hotkey Macro Buttons
        if hasattr(self, 'floating_hotkey_buttons'):
            for btn in self.floating_hotkey_buttons:
                try:
                    btn.hide()
                    btn.close()
                except Exception:
                    pass
            self.floating_hotkey_buttons.clear()

        # Clean up any active AppBars
        try:
            cleanup_all_appbars()
        except Exception:
            pass

        event.accept()
        try:
            QApplication.closeAllWindows()
            QApplication.quit()
            import sys
            sys.exit(0)
        except Exception:
            pass

    def toggle_right_panel(self):
        if getattr(self, 'right_panel_collapsed', False):
            if hasattr(self, 'reveal_container'):
                self.reveal_container.hide()
            self.right_panel.show()
            self.right_panel.setMinimumWidth(220)
            self.right_panel.setMaximumWidth(700)
            w = getattr(self, 'dock_panel_width', 340)
            if hasattr(self, 'content_splitter'):
                total_w = max(800, self.width())
                self.content_splitter.setSizes([max(400, total_w - w), 14, w])
            else:
                self.right_panel.setFixedWidth(w)
            self.right_panel_collapsed = False
        else:
            self.right_panel.hide()
            if hasattr(self, 'reveal_container'):
                self.reveal_container.show()
            self.right_panel_collapsed = True

    def toggle_clock_format(self):
        self.use_24h_clock = not self.use_24h_clock
        self.on_clock_tick()

    def switch_section(self, index):
        self.stacked_widget.setCurrentIndex(index)
        if index == 2:
            self.update_reagents_ui()
        elif index == 6:
            self.load_kill_book()

    # ==================================================================
    # SECTION 1: DASHBOARD PAGE (Main)
    # ==================================================================
    def build_dashboard_page(self):
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background: transparent;")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        # =========================================================================
        # DASHBOARD TILES GRID (12-Column Flexible Grid System)
        # =========================================================================
        self.dashboard_grid = GridReorderContainer(cols=12)
        layout.addWidget(self.dashboard_grid, 1)

        # 1. UNIFIED TILE: CHARACTER IDENTITY & OVERVIEW (Shiftable - Minimal 6-Col)
        char_card = ReorderableCard("CHARACTER IDENTITY & OVERVIEW", self.dashboard_grid, default_colspan=6, is_draggable=True)
        char_card.content_layout.setContentsMargins(8, 6, 8, 8)
        char_card.content_layout.setSpacing(6)

        # CHARACTER NAME & ATTACHMENT OVERVIEW
        char_hdr_box = QHBoxLayout()
        char_hdr_box.setContentsMargins(0, 0, 0, 0)
        char_hdr_box.setSpacing(8)

        self.char_name_lbl = QLabel("CHARACTER: --")
        self.char_name_lbl.setStyleSheet("font-size: 13px; font-weight: 800; color: #f8fafc;")

        self.char_sub_lbl = QLabel("Waiting for process...")
        self.char_sub_lbl.setStyleSheet("font-size: 10px; color: #64748b;")

        char_hdr_box.addWidget(self.char_name_lbl)
        char_hdr_box.addStretch()
        char_hdr_box.addWidget(self.char_sub_lbl)

        char_card.content_layout.addLayout(char_hdr_box)

        # VITAL GAUGES (COMPACT 3-COLUMN SIDE-BY-SIDE LAYOUT)
        vitals_row = QHBoxLayout()
        vitals_row.setContentsMargins(0, 2, 0, 2)
        vitals_row.setSpacing(8)

        self.hp_bar_widget = self.create_vital_gauge("HEALTH", "#ef4444", 150)
        self.mp_bar_widget = self.create_vital_gauge("MANA", "#3b82f6", 250)
        self.vg_bar_widget = self.create_vital_gauge("VIGOR", "#64748b", 200)

        vitals_row.addLayout(self.hp_bar_widget['layout'], 1)
        vitals_row.addLayout(self.mp_bar_widget['layout'], 1)
        vitals_row.addLayout(self.vg_bar_widget['layout'], 1)

        char_card.content_layout.addLayout(vitals_row)

        # CHARACTER ATTRIBUTES (COMPACT CLEAN LIST FORMAT)
        attr_list_layout = QHBoxLayout()
        attr_list_layout.setContentsMargins(0, 0, 0, 0)
        attr_list_layout.setSpacing(8)

        col1_layout = QVBoxLayout()
        col1_layout.setContentsMargins(0, 0, 0, 0)
        col1_layout.setSpacing(1)
        col2_layout = QVBoxLayout()
        col2_layout.setContentsMargins(0, 0, 0, 0)
        col2_layout.setSpacing(1)

        self.attr_labels = {}
        attr_keys = ["Might", "Intellect", "Stamina", "Agility", "Mysticism", "Aim", "Karma"]

        for idx, key in enumerate(attr_keys):
            row_widget = QWidget()
            row_widget.setStyleSheet("border-bottom: 1px solid #1e293b;")
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(2, 1, 2, 1)

            lbl_title = QLabel(key.upper())
            lbl_title.setStyleSheet("font-size: 9px; font-weight: 700; color: #94a3b8; background: transparent;")

            lbl_val = QLabel("--")
            lbl_val.setStyleSheet("font-size: 10px; font-weight: 900; color: #94a3b8; background: transparent;")
            lbl_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

            self.attr_labels[key] = lbl_val

            row_layout.addWidget(lbl_title)
            row_layout.addStretch()
            row_layout.addWidget(lbl_val)

            if idx < 4:
                col1_layout.addWidget(row_widget)
            else:
                col2_layout.addWidget(row_widget)

        attr_list_layout.addLayout(col1_layout, 1)
        attr_list_layout.addLayout(col2_layout, 1)

        char_card.content_layout.addLayout(attr_list_layout)

        # BANK BALANCES
        bank_text_layout = QHBoxLayout()
        bank_text_layout.setContentsMargins(0, 2, 0, 0)
        bank_text_layout.setSpacing(8)

        self.bank_mainland_lbl = QLabel("Mainland Bank: 0 shillings")
        self.bank_mainland_lbl.setStyleSheet("font-size: 10px; font-weight: 700; color: #e2e8f0;")

        self.bank_island_lbl = QLabel("Island Bank: 0 shillings")
        self.bank_island_lbl.setStyleSheet("font-size: 10px; font-weight: 700; color: #e2e8f0;")

        bank_text_layout.addWidget(self.bank_mainland_lbl)
        bank_text_layout.addStretch()
        bank_text_layout.addWidget(self.bank_island_lbl)

        char_card.content_layout.addLayout(bank_text_layout)

        # 2. GPS NAVIGATION TILE (Placed right after CHARACTER IDENTITY & OVERVIEW)
        gps_card = ReorderableCard("GPS NAVIGATION", self.dashboard_grid, default_colspan=6, is_draggable=True)
        gps_card.content_layout.setContentsMargins(8, 8, 8, 8)
        gps_card.content_layout.setSpacing(6)

        # Top Control Cluster
        gps_top_cluster = QVBoxLayout()
        gps_top_cluster.setContentsMargins(0, 0, 0, 0)
        gps_top_cluster.setSpacing(4)

        # Line 1: Location & Destination Badges
        gps_top_box = QHBoxLayout()
        gps_top_box.setContentsMargins(0, 0, 0, 0)
        gps_top_box.setSpacing(8)

        self.gps_main_loc_lbl = QLabel("📍 Current: Unknown")
        self.gps_main_loc_lbl.setStyleSheet("font-size: 11px; font-weight: 800; color: #38bdf8; background: transparent; padding: 0;")

        # PvP Room Status Indicators (Main View)
        self.main_pvp_icon_1 = QLabel()
        self.main_pvp_icon_2 = QLabel()
        self.main_pvp_icon_1.setStyleSheet("background: transparent; padding: 0;")
        self.main_pvp_icon_2.setStyleSheet("background: transparent; padding: 0;")

        self.gps_main_target_lbl = QLabel("🎯 Target: None")
        self.gps_main_target_lbl.setStyleSheet("font-size: 11px; font-weight: 800; color: #94a3b8; background: transparent; padding: 0;")

        gps_top_box.addWidget(self.gps_main_loc_lbl)
        gps_top_box.addWidget(self.main_pvp_icon_1)
        gps_top_box.addWidget(self.main_pvp_icon_2)
        gps_top_box.addWidget(self.gps_main_target_lbl)
        gps_top_box.addStretch()

        gps_top_cluster.addLayout(gps_top_box)

        # Line 2: Search Bar + Action Button (Icon only)
        gps_search_box = QHBoxLayout()
        gps_search_box.setContentsMargins(0, 0, 0, 0)
        gps_search_box.setSpacing(6)

        lbl_search = QLabel("Destination:")
        lbl_search.setStyleSheet("font-size: 11px; font-weight: 700; color: #94a3b8;")

        self.gps_main_search = QLineEdit()
        self.gps_main_search.setPlaceholderText("Search destination room (e.g. Marion, Jas Inn)...")
        self.gps_main_search.setFixedHeight(28)
        self.gps_main_search.setStyleSheet("""
            QLineEdit {
                background-color: #0b1120;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 0 8px;
                font-size: 11px;
            }
            QLineEdit:focus {
                border: 1px solid #3b82f6;
            }
        """)

        main_completer = self.create_room_completer(self.gps_main_search)
        if main_completer:
            self.gps_main_search.setCompleter(main_completer)
        self.gps_main_search.returnPressed.connect(lambda: self.toggle_navigation(source_text=self.gps_main_search.text()))

        self.gps_main_btn = QPushButton("▶")
        self.gps_main_btn.setFixedSize(32, 28)
        self.gps_main_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.gps_main_btn.setStyleSheet("""
            QPushButton {
                background-color: #16a34a;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                font-size: 12px;
                font-weight: 800;
            }
            QPushButton:hover {
                background-color: #22c55e;
            }
        """)
        self.gps_main_btn.clicked.connect(lambda: self.toggle_navigation(source_text=self.gps_main_search.text()))

        # Backward compatibility references
        self.gps_main_start_btn = self.gps_main_btn
        self.gps_main_stop_btn = self.gps_main_btn

        gps_search_box.addWidget(lbl_search)
        gps_search_box.addWidget(self.gps_main_search, 1)
        gps_search_box.addWidget(self.gps_main_btn)

        gps_top_cluster.addLayout(gps_search_box)

        # Line 3: Navigation Instruction Row
        step_layout = QHBoxLayout()
        step_layout.setContentsMargins(0, 0, 0, 0)
        step_layout.setSpacing(6)

        self.gps_instruction_lbl = QLabel("Select a destination to begin navigation...")
        self.gps_instruction_lbl.setStyleSheet("font-size: 12px; font-weight: 700; color: #f1f5f9; background: transparent;")
        self.gps_instruction_lbl.setWordWrap(True)

        step_layout.addWidget(self.gps_instruction_lbl, 1)

        gps_top_cluster.addLayout(step_layout)

        gps_card.content_layout.addLayout(gps_top_cluster)

        # Trip Preview Section (Header + List that expands to fill remaining tile height)
        self.gps_route_title_lbl = QLabel("TRIP PREVIEW")
        self.gps_route_title_lbl.setStyleSheet("font-size: 10px; font-weight: 800; color: #94a3b8; letter-spacing: 0.8px; margin-top: 2px;")
        gps_card.content_layout.addWidget(self.gps_route_title_lbl)

        self.gps_route_list = QListWidget()
        self.gps_route_list.setMinimumHeight(60)
        self.gps_route_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.gps_route_list.setStyleSheet("""
            QListWidget {
                background-color: #090d16;
                color: #e2e8f0;
                border: 1px solid #1e293b;
                border-radius: 4px;
                font-family: monospace;
                font-size: 10px;
                padding: 2px;
            }
            QListWidget::item {
                padding: 3px 6px;
                border-bottom: 1px solid #0f172a;
            }
            QListWidget::item:selected {
                background-color: #1e3a8a;
                color: #ffffff;
            }
        """)

        # Add initial idle placeholder item
        item = QListWidgetItem(" No active route. Enter a destination above to preview trip steps.")
        item.setForeground(QColor("#64748b"))
        self.gps_route_list.addItem(item)

        gps_card.content_layout.addWidget(self.gps_route_list, 1)

        # 3. VAULT MANAGEMENT TILE WITH TABS (Shiftable)
        vault_card = ReorderableCard("VAULT MANAGEMENT", self.dashboard_grid, default_colspan=6, is_draggable=True)
        vault_card.content_layout.setContentsMargins(8, 8, 8, 8)
        vault_card.content_layout.setSpacing(8)

        # BANK BALANCES IN VAULT TILE
        v_bank_title = QLabel("BANK BALANCES")
        v_bank_title.setStyleSheet("font-size: 10px; font-weight: 800; color: #94a3b8; letter-spacing: 0.8px;")
        vault_card.content_layout.addWidget(v_bank_title)

        v_bank_text_layout = QHBoxLayout()
        v_bank_text_layout.setContentsMargins(0, 0, 0, 0)
        v_bank_text_layout.setSpacing(12)

        self.vault_bank_mainland_lbl = QLabel("Mainland Bank: 0 shillings")
        self.vault_bank_mainland_lbl.setStyleSheet("font-size: 11px; font-weight: 700; color: #e2e8f0;")

        self.vault_bank_island_lbl = QLabel("Island Bank: 0 shillings")
        self.vault_bank_island_lbl.setStyleSheet("font-size: 11px; font-weight: 700; color: #e2e8f0;")

        v_bank_text_layout.addWidget(self.vault_bank_mainland_lbl)
        v_bank_text_layout.addStretch()
        v_bank_text_layout.addWidget(self.vault_bank_island_lbl)

        vault_card.content_layout.addLayout(v_bank_text_layout)

        self.vault_tab_widget = QTabWidget()
        self.vault_tab_widget.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #1e293b;
                background-color: #0b1120;
                border-radius: 6px;
            }
            QTabBar::tab {
                background-color: #0f172a;
                color: #94a3b8;
                padding: 6px 14px;
                font-size: 11px;
                font-weight: 700;
                border: 1px solid #1e293b;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: #1e293b;
                color: #94a3b8;
                border-color: #94a3b8;
            }
            QTabBar::tab:hover {
                color: #f8fafc;
            }
        """)

        self.vault_widgets = {}

        # --- Tab 1: Barloque Vault ---
        barloque_tab = QWidget()
        bt_layout = QVBoxLayout(barloque_tab)
        bt_layout.setContentsMargins(8, 8, 8, 8)
        bt_layout.setSpacing(6)

        b_ctrl_layout = QHBoxLayout()
        b_search = QLineEdit()
        b_search.setPlaceholderText("Filter Barloque items...")
        b_search.textChanged.connect(lambda: self.update_vault_table("barloque"))
        b_ctrl_layout.addWidget(b_search, 1)

        b_scan_btn = QPushButton("🔄 Scan Barloque")
        b_scan_btn.setProperty("class", "WebBtnPrimary")
        b_scan_btn.setToolTip("Triggers automated in-game Barloque Vault scanning.")
        b_scan_btn.clicked.connect(lambda: self.trigger_vault_scan("barloque"))
        b_ctrl_layout.addWidget(b_scan_btn)

        bt_layout.addLayout(b_ctrl_layout)

        b_table = QTableWidget(0, 2)
        b_table.setMinimumHeight(180)
        b_table.verticalHeader().setVisible(False)
        b_table.setHorizontalHeaderLabels(["ITEM NAME", "QTY"])
        b_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        b_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        bt_layout.addWidget(b_table)

        b_status = QLabel("No scan data")
        b_status.setStyleSheet("font-size: 10px; color: #64748b; font-style: italic;")
        bt_layout.addWidget(b_status)

        self.vault_widgets["barloque"] = {
            "table": b_table,
            "search": b_search,
            "status": b_status,
            "btn": b_scan_btn
        }

        # --- Tab 2: Hungry Vault ---
        hungry_tab = QWidget()
        ht_layout = QVBoxLayout(hungry_tab)
        ht_layout.setContentsMargins(8, 8, 8, 8)
        ht_layout.setSpacing(6)

        h_ctrl_layout = QHBoxLayout()
        h_search = QLineEdit()
        h_search.setPlaceholderText("Filter Hungry items...")
        h_search.textChanged.connect(lambda: self.update_vault_table("hungry"))
        h_ctrl_layout.addWidget(h_search, 1)

        h_scan_btn = QPushButton("🔄 Scan Hungry")
        h_scan_btn.setProperty("class", "WebBtnSecondary")
        h_scan_btn.setToolTip("Triggers automated in-game Hungry Vault scanning.")
        h_scan_btn.clicked.connect(lambda: self.trigger_vault_scan("hungry"))
        h_ctrl_layout.addWidget(h_scan_btn)

        ht_layout.addLayout(h_ctrl_layout)

        h_table = QTableWidget(0, 2)
        h_table.setMinimumHeight(180)
        h_table.verticalHeader().setVisible(False)
        h_table.setHorizontalHeaderLabels(["ITEM NAME", "QTY"])
        h_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        h_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        ht_layout.addWidget(h_table)

        h_status = QLabel("No scan data")
        h_status.setStyleSheet("font-size: 10px; color: #64748b; font-style: italic;")
        ht_layout.addWidget(h_status)

        self.vault_widgets["hungry"] = {
            "table": h_table,
            "search": h_search,
            "status": h_status,
            "btn": h_scan_btn
        }

        self.vault_tab_widget.addTab(barloque_tab, "🏰 Barloque Vault")
        self.vault_tab_widget.addTab(hungry_tab, "🏝️ Hungry Vault")

        vault_card.content_layout.addWidget(self.vault_tab_widget)

        # 4. School Progression Card (Shiftable Tile)
        prog_card = ReorderableCard("SCHOOL PROGRESSION", self.dashboard_grid, default_colspan=6, is_draggable=True)

        self.dash_prog_summary_badge = QLabel("0 Active Schools")
        self.dash_prog_summary_badge.setStyleSheet("background-color: #0c4a6e; color: #38bdf8; font-size: 10px; font-weight: 800; padding: 2px 8px; border-radius: 6px;")
        prog_card.add_header_widget(self.dash_prog_summary_badge)

        self.dash_prog_rescan_btn = QPushButton("🔄 Refresh")
        self.dash_prog_rescan_btn.setProperty("class", "WebBtnSecondary")
        self.dash_prog_rescan_btn.setToolTip("Refresh progression metrics from memory cache or trigger stats sync")
        self.dash_prog_rescan_btn.clicked.connect(lambda: self.update_progression_ui())
        prog_card.add_header_widget(self.dash_prog_rescan_btn)

        self.dash_prog_tree = QTreeWidget()
        self.dash_prog_tree.setMinimumHeight(180)
        self.dash_prog_tree.setHeaderLabels(["SCHOOL / ABILITY", "LEVEL", "SUM", "GOAL", "NEEDED"])
        self.dash_prog_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.dash_prog_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.dash_prog_tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.dash_prog_tree.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.dash_prog_tree.header().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.dash_prog_tree.setStyleSheet("""
            QTreeWidget {
                background-color: #030712;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 6px;
            }
            QHeaderView::section {
                background-color: #0f172a;
                color: #94a3b8;
                font-size: 10px;
                font-weight: 800;
                padding: 4px;
                border: 1px solid #1e293b;
            }
            QTreeWidget::item {
                padding: 3px 0px;
            }
            QTreeWidget::item:selected {
                background-color: #1e293b;
                color: #38bdf8;
            }
        """)
        prog_card.content_layout.addWidget(self.dash_prog_tree)

        # 5. Session Kills Ledger Section (Shiftable)
        kill_card = ReorderableCard("SESSION KILLS (COMBAT)", self.dashboard_grid, default_colspan=6)

        self.kill_count_badge = QLabel("0 Kills")
        self.kill_count_badge.setStyleSheet("background-color: #881337; color: #fda4af; font-size: 10px; font-weight: 800; padding: 2px 8px; border-radius: 6px;")
        kill_card.add_header_widget(self.kill_count_badge)

        self.kill_table = QTableWidget(0, 4)
        self.kill_table.setMinimumHeight(180)
        self.kill_table.verticalHeader().setVisible(False)
        self.kill_table.setHorizontalHeaderLabels(["TARGET", "CATEGORY", "SESSION KILLS", "TIME"])
        self.kill_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.kill_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.kill_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.kill_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        kill_card.content_layout.addWidget(self.kill_table)

        # 5. Session Improves Ledger Section (Shiftable)
        imp_card = ReorderableCard("SESSION IMPROVES (SKILLS & SPELLS)", self.dashboard_grid, default_colspan=6)

        self.imp_count_badge = QLabel("0 Gains")
        self.imp_count_badge.setStyleSheet("background-color: #064e3b; color: #94a3b8; font-size: 10px; font-weight: 800; padding: 2px 8px; border-radius: 6px;")
        imp_card.add_header_widget(self.imp_count_badge)

        self.imp_table = QTableWidget(0, 4)
        self.imp_table.setMinimumHeight(180)
        self.imp_table.verticalHeader().setVisible(False)
        self.imp_table.setHorizontalHeaderLabels(["SKILL / SPELL", "TOTAL GAINS", "DELTA", "LAST GAIN"])
        self.imp_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.imp_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.imp_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.imp_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        imp_card.content_layout.addWidget(self.imp_table)

        # 6. Carried Items Ledger Table Section (Shiftable - Low Profile Gauge Bars Above List)
        items_card = ReorderableCard("CARRIED ITEMS LEDGER", self.dashboard_grid, default_colspan=6)

        self.inv_search_input = QLineEdit()
        self.inv_search_input.setPlaceholderText("Filter carried items...")
        self.inv_search_input.setFixedWidth(150)
        self.inv_search_input.textChanged.connect(self.filter_inventory_table)
        items_card.add_header_widget(self.inv_search_input)

        self.inv_rescan_btn = QPushButton("↻ Rescan")
        self.inv_rescan_btn.setProperty("class", "WebBtnSecondary")
        self.inv_rescan_btn.clicked.connect(self.poll_inventory)
        items_card.add_header_widget(self.inv_rescan_btn)

        # Low-profile Gauge Bars section above carried items table
        gauge_frame = QFrame()
        gauge_frame.setStyleSheet("background-color: #030712; border: 1px solid #334155; border-radius: 6px;")
        gauge_layout = QHBoxLayout(gauge_frame)
        gauge_layout.setContentsMargins(8, 6, 8, 6)
        gauge_layout.setSpacing(10)

        # Meter 1: Saturation
        m1_layout = QVBoxLayout()
        m1_layout.setSpacing(2)
        m1_hdr = QHBoxLayout()
        m1_title = QLabel("SATURATION")
        m1_title.setStyleSheet("font-size: 9px; font-weight: 800; color: #94a3b8;")
        self.sat_val_lbl = QLabel("0.0%")
        self.sat_val_lbl.setStyleSheet("font-size: 11px; font-weight: 900; color: #94a3b8;")
        m1_hdr.addWidget(m1_title)
        m1_hdr.addStretch()
        m1_hdr.addWidget(self.sat_val_lbl)
        m1_layout.addLayout(m1_hdr)

        self.sat_bar = QProgressBar()
        self.sat_bar.setFixedHeight(6)
        self.sat_bar.setTextVisible(False)
        self.set_progress_bar_color(self.sat_bar, 0)
        m1_layout.addWidget(self.sat_bar)

        self.sat_sub_lbl = QLabel("Max Cap: 1,700")
        self.sat_sub_lbl.setStyleSheet("font-size: 9px; color: #64748b;")
        m1_layout.addWidget(self.sat_sub_lbl)
        gauge_layout.addLayout(m1_layout, 1)

        # Meter 2: Weight Load
        m2_layout = QVBoxLayout()
        m2_layout.setSpacing(2)
        m2_hdr = QHBoxLayout()
        m2_title = QLabel("WEIGHT LOAD")
        m2_title.setStyleSheet("font-size: 9px; font-weight: 800; color: #60a5fa;")
        self.weight_val_lbl = QLabel("0 / 1,700 W")
        self.weight_val_lbl.setStyleSheet("font-size: 11px; font-weight: 900; color: #60a5fa;")
        m2_hdr.addWidget(m2_title)
        m2_hdr.addStretch()
        m2_hdr.addWidget(self.weight_val_lbl)
        m2_layout.addLayout(m2_hdr)

        self.weight_bar = QProgressBar()
        self.weight_bar.setFixedHeight(6)
        self.weight_bar.setTextVisible(False)
        self.set_progress_bar_color(self.weight_bar, 0)
        m2_layout.addWidget(self.weight_bar)

        self.weight_sub_lbl = QLabel("Cap: 1,700 Stone")
        self.weight_sub_lbl.setStyleSheet("font-size: 9px; color: #64748b;")
        m2_layout.addWidget(self.weight_sub_lbl)
        gauge_layout.addLayout(m2_layout, 1)

        # Meter 3: Bulk Load
        m3_layout = QVBoxLayout()
        m3_layout.setSpacing(2)
        m3_hdr = QHBoxLayout()
        m3_title = QLabel("BULK LOAD")
        m3_title.setStyleSheet("font-size: 9px; font-weight: 800; color: #c084fc;")
        self.bulk_val_lbl = QLabel("0 / 1,700 B")
        self.bulk_val_lbl.setStyleSheet("font-size: 11px; font-weight: 900; color: #c084fc;")
        m3_hdr.addWidget(m3_title)
        m3_hdr.addStretch()
        m3_hdr.addWidget(self.bulk_val_lbl)
        m3_layout.addLayout(m3_hdr)

        self.bulk_bar = QProgressBar()
        self.bulk_bar.setFixedHeight(6)
        self.bulk_bar.setTextVisible(False)
        self.set_progress_bar_color(self.bulk_bar, 0)
        m3_layout.addWidget(self.bulk_bar)

        self.bulk_sub_lbl = QLabel("Cap: 1,700 Vol")
        self.bulk_sub_lbl.setStyleSheet("font-size: 9px; color: #64748b;")
        m3_layout.addWidget(self.bulk_sub_lbl)
        gauge_layout.addLayout(m3_layout, 1)

        items_card.content_layout.addWidget(gauge_frame)

        self.inv_table = QTableWidget(0, 5)
        self.inv_table.setMinimumHeight(200)
        self.inv_table.verticalHeader().setVisible(False)
        self.inv_table.setHorizontalHeaderLabels(["ITEM NAME", "QTY", "WEIGHT (W)", "BULK (B)", "TOTAL W/B"])
        self.inv_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.inv_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.inv_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.inv_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.inv_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        items_card.content_layout.addWidget(self.inv_table)

        # 7. UW Node Mana Solver Section (Shiftable)
        uwnode_card = ReorderableCard("UW NODE MANA SOLVER", self.dashboard_grid, default_colspan=6)

        open_uwnode_btn = QPushButton("🌀 Launch Full Interactive Pentagram Solver Page")
        open_uwnode_btn.setProperty("class", "WebBtnPrimary")
        open_uwnode_btn.clicked.connect(lambda: self.nav_list.setCurrentRow(7))
        uwnode_card.add_header_widget(open_uwnode_btn)

        dash_uwnode_widget = UWNodeSolverWidget()
        dash_uwnode_widget.setMinimumHeight(480)
        uwnode_card.content_layout.addWidget(dash_uwnode_widget)

        scroll.setWidget(container)
        page_layout.addWidget(scroll)

        return page

    def create_vital_gauge(self, title, color, initial_max=None):
        layout = QVBoxLayout()
        layout.setSpacing(4)

        hdr = QHBoxLayout()
        lbl = QLabel(title)
        lbl.setStyleSheet(f"font-size: 10px; font-weight: 800; color: {color}; letter-spacing: 0.8px;")

        init_str = f"-- / {initial_max}" if initial_max is not None else "-- / --"
        v_lbl = QLabel(init_str)
        v_lbl.setStyleSheet("font-size: 12px; font-weight: 800; color: #f8fafc;")

        hdr.addWidget(lbl)
        hdr.addStretch()
        hdr.addWidget(v_lbl)
        layout.addLayout(hdr)

        pbar = QProgressBar()
        pbar.setRange(0, 100)
        pbar.setValue(0)
        pbar.setTextVisible(False)
        pbar.setFixedHeight(8)
        pbar.setStyleSheet(f"""
            QProgressBar {{
                background-color: #030712;
                border: 1px solid #334155;
                border-radius: 4px;
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 3px;
            }}
        """)
        layout.addWidget(pbar)

        return {"layout": layout, "v_lbl": v_lbl, "pbar": pbar}

    # ==================================================================
    # SECTION 2: HOTKEYS, SPELLS & BUTTONS MACROS PAGE
    # ==================================================================
    def build_shortcuts_page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(18)

        # Header Title Banner
        hdr_card = QFrame()
        hdr_card.setProperty("class", "WebCard")
        hc_layout = QHBoxLayout(hdr_card)
        hc_layout.setContentsMargins(16, 14, 16, 14)

        title_box = QVBoxLayout()
        t_lbl = QLabel("⚡ Hotkeys, Spells & Command Buttons")
        t_lbl.setStyleSheet("font-size: 16px; font-weight: 800; color: #f8fafc;")
        s_lbl = QLabel("Configure trance-steered teleport eludes, creature morphs, floating action bars, and macro hotkeys.")
        s_lbl.setStyleSheet("font-size: 11px; color: #94a3b8;")
        title_box.addWidget(t_lbl)
        title_box.addWidget(s_lbl)
        hc_layout.addLayout(title_box)
        hc_layout.addStretch()

        layout.addWidget(hdr_card)

        combo_box_qss = """
            QComboBox {
                background-color: #0f172a;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
                min-height: 22px;
            }
            QComboBox:focus, QComboBox:on {
                border: 1px solid #38bdf8;
            }
            QComboBox QAbstractItemView {
                background-color: #0f172a;
                color: #f8fafc;
                selection-background-color: #0284c7;
                selection-color: #ffffff;
                border: 1px solid #38bdf8;
                padding: 4px;
                outline: none;
            }
            QComboBox QAbstractItemView::item {
                color: #f8fafc;
                background-color: #0f172a;
                min-height: 24px;
                padding: 4px 8px;
            }
            QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {
                background-color: #0284c7;
                color: #ffffff;
            }
        """

        # --------------------------------------------------------------
        # CARD 1: ELUSION & TELEPORT SHORTCUTS
        # --------------------------------------------------------------
        elude_card = QFrame()
        elude_card.setProperty("class", "WebCard")
        ec_layout = QVBoxLayout(elude_card)
        ec_layout.setContentsMargins(18, 16, 18, 16)
        ec_layout.setSpacing(14)

        eh_box = QHBoxLayout()
        eh_lbl = QLabel("🔮 ELUSION & TELEPORT SPELL")
        eh_lbl.setStyleSheet("font-size: 13px; font-weight: 800; color: #cbd5e1; letter-spacing: 0.6px;")
        eh_badge = QLabel("Trance Steered")
        eh_badge.setStyleSheet("background-color: rgba(168, 85, 247, 0.15); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.4); padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: 700;")
        eh_box.addWidget(eh_lbl)
        eh_box.addSpacing(8)
        eh_box.addWidget(eh_badge)
        eh_box.addStretch()
        ec_layout.addLayout(eh_box)

        form_grid = QGridLayout()
        form_grid.setSpacing(12)

        # Guildhall Name Input
        gh_lbl = QLabel("Guildhall Name:")
        gh_lbl.setStyleSheet("font-size: 11px; font-weight: 700; color: #cbd5e1;")
        self.shortcut_guildhall_input = QLineEdit()
        self.shortcut_guildhall_input.setPlaceholderText("e.g. Order of the Black Rose")
        self.shortcut_guildhall_input.setText(getattr(self, 'guildhall_name_val', ''))
        self.shortcut_guildhall_input.textChanged.connect(self.on_elude_settings_changed)

        form_grid.addWidget(gh_lbl, 0, 0)
        form_grid.addWidget(self.shortcut_guildhall_input, 0, 1)

        # Elusion Phrase Selector
        phrase_lbl = QLabel("Elusion Phrase ({loc}):")
        phrase_lbl.setStyleSheet("font-size: 11px; font-weight: 700; color: #cbd5e1;")
        self.shortcut_phrase_combo = QComboBox()
        self.shortcut_phrase_combo.setStyleSheet(combo_box_qss)
        self.shortcut_phrase_combo.setEditable(True)
        base_phrases = [
            'say I wish to travel to {loc}.',
            'say By the grace of the High Council, I demand passage to {loc}!',
            'emote separates the earths and forms a path to {loc}',
            'emote traces a rune in the air, opening a rift to {loc}',
            'emote bends the fabric of space with Riija\'s chaotic magic, stepping towards {loc}'
        ]
        self.shortcut_phrase_combo.addItems(base_phrases)

        form_grid.addWidget(phrase_lbl, 1, 0)
        form_grid.addWidget(self.shortcut_phrase_combo, 1, 1)

        # Destination Location Picker
        loc_lbl = QLabel("Target Destination:")
        loc_lbl.setStyleSheet("font-size: 11px; font-weight: 700; color: #cbd5e1;")
        self.shortcut_loc_combo = QComboBox()
        self.shortcut_loc_combo.setStyleSheet(combo_box_qss)
        self.update_elude_locations_list()

        form_grid.addWidget(loc_lbl, 2, 0)
        form_grid.addWidget(self.shortcut_loc_combo, 2, 1)

        ec_layout.addLayout(form_grid)

        # Action buttons for Elude
        elude_btn_box = QHBoxLayout()
        elude_btn_box.setSpacing(10)

        self.cast_elude_btn = QPushButton("⚡ Cast Elude Spell")
        self.cast_elude_btn.setProperty("class", "WebBtnPrimary")
        self.cast_elude_btn.clicked.connect(self.trigger_cast_elude)

        self.float_elude_btn = QPushButton("🚀 Launch Floating Elude Bar")
        self.float_elude_btn.setProperty("class", "WebBtnSecondary")
        self.float_elude_btn.clicked.connect(self.trigger_launch_elude_bar)

        elude_btn_box.addWidget(self.cast_elude_btn)
        elude_btn_box.addWidget(self.float_elude_btn)
        elude_btn_box.addStretch()

        ec_layout.addLayout(elude_btn_box)
        layout.addWidget(elude_card)

        # --------------------------------------------------------------
        # CARD 2: MORPH & CREATURE TRANSFORMATION SHORTCUTS
        # --------------------------------------------------------------
        morph_card = QFrame()
        morph_card.setProperty("class", "WebCard")
        mc_layout = QVBoxLayout(morph_card)
        mc_layout.setContentsMargins(18, 16, 18, 16)
        mc_layout.setSpacing(14)

        mh_box = QHBoxLayout()
        mh_lbl = QLabel("🦎 MORPH SPELL & CREATURE SELECTOR")
        mh_lbl.setStyleSheet("font-size: 13px; font-weight: 800; color: #cbd5e1; letter-spacing: 0.6px;")
        mh_badge = QLabel("Trance Steered")
        mh_badge.setStyleSheet("background-color: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: 700;")
        mh_box.addWidget(mh_lbl)
        mh_box.addSpacing(8)
        mh_box.addWidget(mh_badge)
        mh_box.addStretch()
        mc_layout.addLayout(mh_box)

        morph_grid = QGridLayout()
        morph_grid.setSpacing(12)

        # Morph Creature Picker
        mc_lbl = QLabel("Target Creature Form:")
        mc_lbl.setStyleSheet("font-size: 11px; font-weight: 700; color: #cbd5e1;")
        self.morph_creature_combo = QComboBox()
        self.morph_creature_combo.setStyleSheet(combo_box_qss)
        self.morph_creature_combo.currentIndexChanged.connect(self.on_morph_creature_selected)

        morph_grid.addWidget(mc_lbl, 0, 0)
        morph_grid.addWidget(self.morph_creature_combo, 0, 1)

        # Creature Details / Ko'catan Name Info
        mi_lbl = QLabel("Ko'catan Spoken Name:")
        mi_lbl.setStyleSheet("font-size: 11px; font-weight: 700; color: #cbd5e1;")
        self.morph_detail_lbl = QLabel("Loading creatures...")
        self.morph_detail_lbl.setStyleSheet("font-size: 12px; font-weight: 800; color: #34d399; padding: 4px 0px;")

        morph_grid.addWidget(mi_lbl, 1, 0)
        morph_grid.addWidget(self.morph_detail_lbl, 1, 1)

        # Morph Phrase Selector
        mph_lbl = QLabel("Morph Phrase ({name}):")
        mph_lbl.setStyleSheet("font-size: 11px; font-weight: 700; color: #cbd5e1;")
        self.morph_phrase_combo = QComboBox()
        self.morph_phrase_combo.setStyleSheet(combo_box_qss)
        self.morph_phrase_combo.setEditable(True)
        self.morph_phrase_combo.addItems([
            'say "{name}"',
            'say {name}',
            'emote shifts form into a {name}',
            'say "By the power of Kraanan, become {name}!"'
        ])

        morph_grid.addWidget(mph_lbl, 2, 0)
        morph_grid.addWidget(self.morph_phrase_combo, 2, 1)

        mc_layout.addLayout(morph_grid)

        # Action buttons for Morph
        morph_btn_box = QHBoxLayout()
        morph_btn_box.setSpacing(10)

        self.cast_morph_btn = QPushButton("⚡ Cast Morph Spell")
        self.cast_morph_btn.setStyleSheet("""
            QPushButton {
                background-color: #059669;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 12px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #10b981;
            }
        """)
        self.cast_morph_btn.clicked.connect(self.trigger_cast_morph)

        self.float_morph_btn = QPushButton("🚀 Launch Floating Morph Bar")
        self.float_morph_btn.setProperty("class", "WebBtnSecondary")
        self.float_morph_btn.clicked.connect(self.trigger_launch_morph_bar)

        morph_btn_box.addWidget(self.cast_morph_btn)
        morph_btn_box.addWidget(self.float_morph_btn)
        morph_btn_box.addStretch()

        mc_layout.addLayout(morph_btn_box)
        layout.addWidget(morph_card)

        # Populate creature choices
        self.update_morph_creatures_list()

        # --------------------------------------------------------------
        # CARD 3: COMMAND ALIASES & HOTKEYS TABLE
        # --------------------------------------------------------------
        alias_card = QFrame()
        alias_card.setProperty("class", "WebCard")
        ac_layout = QVBoxLayout(alias_card)
        ac_layout.setContentsMargins(18, 16, 18, 16)
        ac_layout.setSpacing(12)

        ah_box = QHBoxLayout()
        ah_lbl = QLabel("⚡ COMMAND ALIASES & MACRO HOTKEYS")
        ah_lbl.setStyleSheet("font-size: 13px; font-weight: 800; color: #94a3b8; letter-spacing: 0.6px;")
        ah_badge = QLabel("Key Binds")
        ah_badge.setStyleSheet("background-color: rgba(56, 189, 248, 0.15); color: #94a3b8; border: 1px solid rgba(56, 189, 248, 0.4); padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: 700;")
        ah_box.addWidget(ah_lbl)
        ah_box.addSpacing(8)
        ah_box.addWidget(ah_badge)
        ah_box.addStretch()

        ac_layout.addLayout(ah_box)

        # Toolbar
        tb_box = QHBoxLayout()
        tb_box.setSpacing(8)

        add_alias_btn = QPushButton("✚ Add New Alias")
        add_alias_btn.setProperty("class", "WebBtnPrimary")
        add_alias_btn.clicked.connect(self.open_add_alias_dialog)

        edit_alias_btn = QPushButton("✎ Edit Selected")
        edit_alias_btn.setProperty("class", "WebBtnSecondary")
        edit_alias_btn.clicked.connect(self.edit_selected_alias)

        del_alias_btn = QPushButton("🗑 Delete Selected")
        del_alias_btn.setProperty("class", "WebBtnSecondary")
        del_alias_btn.clicked.connect(self.delete_selected_alias)

        refresh_cfg_btn = QPushButton("🔄 Refresh Config Keys")
        refresh_cfg_btn.setProperty("class", "WebBtnSecondary")
        refresh_cfg_btn.clicked.connect(self.refresh_m59_config_keys)

        tb_box.addWidget(add_alias_btn)
        tb_box.addWidget(edit_alias_btn)
        tb_box.addWidget(del_alias_btn)
        tb_box.addWidget(refresh_cfg_btn)
        tb_box.addStretch()

        ac_layout.addLayout(tb_box)

        # Table
        self.alias_table = QTableWidget()
        self.alias_table.setColumnCount(5)
        self.alias_table.setHorizontalHeaderLabels(["Alias Name", "Hotkey", "Command Phrase", "Send Enter", "Floating Button"])
        self.alias_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.alias_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self.alias_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.alias_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Interactive)
        self.alias_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Interactive)
        self.alias_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.alias_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.alias_table.setAlternatingRowColors(True)
        self.alias_table.setStyleSheet("""
            QTableWidget {
                background-color: #090d16;
                gridline-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 6px;
                color: #e2e8f0;
            }
            QHeaderView::section {
                background-color: #111827;
                color: #94a3b8;
                font-weight: 800;
                font-size: 11px;
                padding: 6px;
                border: none;
                border-bottom: 1px solid #1e293b;
            }
            QTableWidget::item:selected {
                background-color: #1e293b;
                color: #94a3b8;
            }
        """)

        ac_layout.addWidget(self.alias_table)
        layout.addWidget(alias_card)

        scroll.setWidget(page)

        # Populate initial alias list
        self.populate_alias_table()

        return scroll

    def update_elude_locations_list(self):
        if not hasattr(self, 'shortcut_loc_combo'):
            return
        cur = self.shortcut_loc_combo.currentText()
        self.shortcut_loc_combo.clear()
        locs = [
            "The Streets of Tos",
            "Marion",
            "South Barloque",
            "Cor Noth",
            "East Jasper",
            "The Aerie Guest House",
            "Guild Hall"
        ]
        gh = self.shortcut_guildhall_input.text().strip() if hasattr(self, 'shortcut_guildhall_input') else ""
        if gh and gh not in locs:
            locs.append(gh)
        self.shortcut_loc_combo.addItems(locs)
        if cur and cur in locs:
            self.shortcut_loc_combo.setCurrentText(cur)

    def on_elude_settings_changed(self):
        if hasattr(self, 'shortcut_guildhall_input'):
            self.guildhall_name_val = self.shortcut_guildhall_input.text().strip()
            self.update_elude_locations_list()

    def update_morph_creatures_list(self):
        if not hasattr(self, 'morph_creature_combo'):
            return
        creatures = get_morph_creatures()
        self.morph_creature_combo.clear()
        for c in creatures:
            display = f"Lvl {c['level']} - {c['en_name'].title()} ({c['ko_catan']})"
            self.morph_creature_combo.addItem(display, userData=c)
        self.on_morph_creature_selected()

    def on_morph_creature_selected(self):
        if not hasattr(self, 'morph_creature_combo') or not hasattr(self, 'morph_detail_lbl'):
            return
        c_data = self.morph_creature_combo.currentData()
        if not c_data or not isinstance(c_data, dict):
            idx = self.morph_creature_combo.currentIndex()
            creatures = get_morph_creatures()
            if 0 <= idx < len(creatures):
                c_data = creatures[idx]
        if c_data:
            self.morph_detail_lbl.setText(f'🗣️ Say "{c_data.get("ko_catan", "")}"  •  Level {c_data.get("level", "1")} ({c_data.get("en_name", "").title()})')
        else:
            self.morph_detail_lbl.setText("No creature selected")

    def cast_spell_with_trance(self, spell_name, steer_command, target_hwnd=None):
        """Initiates casting a trance-steered spell (e.g. elusion, morph).
        Passes the steer_command to the target game window ONLY when trance is confirmed
        by 'You focus your whole will on casting [spellname].' in chat log."""
        target = target_hwnd or getattr(self, 'main_hwnd', None)
        if not target:
            print(f"[M59-SPELL] Cannot cast {spell_name}: game window not attached.", flush=True)
            return

        clean_spell = spell_name.strip().lower()
        t_cast = time.time()

        # Sanitize steer command if it has redundant quotes around say phrase
        cmd = steer_command.strip()
        m_say_quotes = re.match(r'^say\s+"(.*)"$', cmd, re.IGNORECASE)
        if m_say_quotes:
            cmd = f'say {m_say_quotes.group(1)}'

        self.pending_spell_trance = {
            "spell_name": clean_spell,
            "steer_command": cmd,
            "target_hwnd": target,
            "cast_time": t_cast,
            "trance_entered": False,
            "fizzled": False,
            "completed": False
        }
        print(f"[M59-SPELL] Initiating trance-steered spell '{clean_spell}' with target command: {cmd}", flush=True)
        send_chat_command(target, f'cast "{clean_spell}"')

        # Safety cleanup thread: after 10s, clear pending state if spell was not completed or failed
        def _expire_cleanup():
            time.sleep(10.0)
            if hasattr(self, 'pending_spell_trance') and self.pending_spell_trance:
                cur = self.pending_spell_trance
                if cur.get('cast_time') == t_cast and not cur.get('completed'):
                    print(f"[M59-SPELL] Trance timeout reached for '{clean_spell}' without focus confirmation. Steering aborted.", flush=True)
                    self.pending_spell_trance = None

        threading.Thread(target=_expire_cleanup, daemon=True).start()

    def trigger_cast_elude(self):
        loc = self.shortcut_loc_combo.currentText() if hasattr(self, 'shortcut_loc_combo') else "Marion"
        phrase = self.shortcut_phrase_combo.currentText() if hasattr(self, 'shortcut_phrase_combo') else 'say I wish to travel to {loc}.'
        formatted = phrase.replace("{loc}", loc)
        hwnd = getattr(self, 'main_hwnd', None)
        self.cast_spell_with_trance("elusion", formatted, target_hwnd=hwnd)

    def trigger_launch_elude_bar(self):
        try:
            hwnd = getattr(self, 'main_hwnd', None)
            s = self.load_gui_settings()
            s['elude_bar_open'] = True
            self.save_gui_settings(s)

            if hasattr(self, 'active_elude_bar') and self.active_elude_bar:
                try:
                    if self.active_elude_bar.isVisible():
                        self.active_elude_bar.raise_()
                        self.active_elude_bar.activateWindow()
                        return
                    else:
                        self.active_elude_bar.show()
                        return
                except Exception:
                    pass
            self.active_elude_bar = QtFloatingEludeBar(dashboard=self, target_hwnd=hwnd)
            self.active_elude_bar.show()
        except Exception as ex:
            print(f"[M59-ELUDE] Error launching floating elude bar: {ex}", flush=True)

    def trigger_cast_morph(self):
        c_data = self.morph_creature_combo.currentData() if hasattr(self, 'morph_creature_combo') else None
        if not c_data or not isinstance(c_data, dict):
            creatures = get_morph_creatures()
            idx = self.morph_creature_combo.currentIndex() if hasattr(self, 'morph_creature_combo') else 0
            if 0 <= idx < len(creatures):
                c_data = creatures[idx]
        if not c_data:
            return
        ko_name = c_data.get("ko_catan", "").strip()
        if not ko_name:
            return

        phrase = self.morph_phrase_combo.currentText() if hasattr(self, 'morph_phrase_combo') else 'say "{name}"'
        formatted = phrase.replace("{name}", ko_name)
        hwnd = getattr(self, 'main_hwnd', None)
        self.cast_spell_with_trance("morph", formatted, target_hwnd=hwnd)

    def trigger_launch_morph_bar(self):
        try:
            hwnd = getattr(self, 'main_hwnd', None)
            s = self.load_gui_settings()
            s['morph_bar_open'] = True
            self.save_gui_settings(s)

            if hasattr(self, 'active_morph_bar') and self.active_morph_bar:
                try:
                    if self.active_morph_bar.isVisible():
                        self.active_morph_bar.raise_()
                        self.active_morph_bar.activateWindow()
                        return
                    else:
                        self.active_morph_bar.show()
                        return
                except Exception:
                    pass
            self.active_morph_bar = QtFloatingMorphBar(dashboard=self, target_hwnd=hwnd)
            self.active_morph_bar.show()
        except Exception as ex:
            print(f"[M59-MORPH] Error launching floating morph bar: {ex}", flush=True)

    def trigger_launch_floating_chat(self):
        try:
            hwnd = getattr(self, 'main_hwnd', None)
            s = self.load_gui_settings()
            s['floating_chat_open'] = True
            self.save_gui_settings(s)

            if hasattr(self, 'active_floating_chat') and self.active_floating_chat:
                try:
                    if self.active_floating_chat.isVisible():
                        self.active_floating_chat.raise_()
                        self.active_floating_chat.activateWindow()
                        return
                    else:
                        self.active_floating_chat.show()
                        return
                except Exception:
                    pass
            self.active_floating_chat = QtFloatingChatBox(dashboard=self, target_hwnd=hwnd)
            self.active_floating_chat.show()
        except Exception as ex:
            print(f"[M59-CHAT] Error launching floating chatbox: {ex}", flush=True)

    def restore_launched_floating_bars(self):
        """Restores any floating action bars (Elude, Morph, Floating Chat) that were open when app was last closed."""
        try:
            s = self.load_gui_settings()
            if s.get("elude_bar_open", False):
                self.trigger_launch_elude_bar()
            if s.get("morph_bar_open", False):
                self.trigger_launch_morph_bar()
            if s.get("floating_chat_open", False):
                self.trigger_launch_floating_chat()
        except Exception as ex:
            print(f"[M59-RESTORE] Error restoring floating bars: {ex}", flush=True)

    def update_floating_hotkey_buttons(self):
        if hasattr(self, 'floating_hotkey_buttons'):
            for btn in self.floating_hotkey_buttons:
                try:
                    btn.close()
                except Exception:
                    pass
        self.floating_hotkey_buttons = []

        target = getattr(self, 'main_hwnd', None)
        aliases = self.load_commaliases()
        x_off = 30
        for alias in aliases:
            if alias.get('show_float', False) and alias.get('enabled', True):
                saved_x = alias.get('x_offset')
                if saved_x is None:
                    saved_x = x_off
                saved_y = alias.get('y_offset')
                if saved_y is None:
                    saved_y = 60
                btn = QtFloatingHotkeyButton(
                    alias_name=alias.get('name', 'Alias'),
                    command1=alias.get('command1', ''),
                    send_enter=alias.get('send_enter', True),
                    alias_dict=alias,
                    dashboard=self,
                    target_hwnd=target,
                    x_offset=saved_x,
                    y_offset=saved_y
                )
                btn.show()
                self.floating_hotkey_buttons.append(btn)
                x_off += 130

    def register_global_hotkeys(self):
        try:
            try:
                keyboard.unhook_all()
            except Exception:
                pass

            aliases = self.load_commaliases()
            for alias in aliases:
                if not alias.get('enabled', True):
                    continue
                hotkey = alias.get('hotkey', '').strip()
                if hotkey:
                    k_hotkey = self._translate_hotkey_for_keyboard(hotkey)
                    if k_hotkey:
                        try:
                            keyboard.add_hotkey(k_hotkey, self._on_global_hotkey_triggered, args=(alias,))
                        except Exception as ex:
                            print(f"Failed to bind hotkey {k_hotkey}: {ex}")
        except Exception as ex:
            print(f"Global hotkey hook warning: {ex}")

    def _translate_hotkey_for_keyboard(self, key_str):
        key_str = key_str.lower().strip()
        parts = key_str.split('+')
        modifiers = []
        main_key = None
        for p in parts:
            p = p.strip()
            if p in ('ctrl', 'alt', 'shift'):
                modifiers.append(p)
            else:
                main_key = p
        if not main_key:
            return None
        if modifiers:
            return "+".join(modifiers) + "+" + main_key
        return main_key

    def _on_global_hotkey_triggered(self, alias):
        target = getattr(self, 'main_hwnd', None)
        if not target:
            return
        if win32gui:
            try:
                active_hwnd = win32gui.GetForegroundWindow()
                if active_hwnd != target:
                    return
            except Exception:
                pass
        cmd1 = alias.get('command1', '').strip()
        send_enter = alias.get('send_enter', True)
        if cmd1:
            def _run():
                try:
                    send_chat_command(target, cmd1, send_enter=send_enter)
                except Exception as ex:
                    print(f"Hotkey command execution failed: {ex}")
            threading.Thread(target=_run, daemon=True).start()

    def load_commaliases(self):
        candidate_paths = [
            os.path.join("settings", "commalias.json"),
            os.path.join("settings", "commaliases.json"),
            os.path.join("settings", "aliases.json"),
            "commalias.json",
            "aliases.json",
        ]
        for p in candidate_paths:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            return data
                except Exception as e:
                    print(f"Error loading commaliases from {p}: {e}")
        return []

    def save_commaliases(self, aliases, rebuild_buttons=True):
        os.makedirs("settings", exist_ok=True)
        p = os.path.join("settings", "commalias.json")
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(aliases, f, indent=2)
        except Exception as ex:
            print(f"Error saving commaliases: {ex}")
        if rebuild_buttons:
            self.update_floating_hotkey_buttons()
            self.register_global_hotkeys()

    def load_discovered_players(self):
        """Loads persistently discovered players cache from settings/discovered_players.json."""
        candidate_paths = [
            os.path.join("settings", "discovered_players.json"),
            "discovered_players.json"
        ]
        for p in candidate_paths:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            return data
                except Exception as e:
                    print(f"Error loading discovered players from {p}: {e}")
        return {}

    def save_discovered_players(self):
        """Saves discovered players cache to settings/discovered_players.json."""
        os.makedirs("settings", exist_ok=True)
        p = os.path.join("settings", "discovered_players.json")
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(self.discovered_players, f, indent=2)
        except Exception as ex:
            print(f"Error saving discovered_players.json: {ex}")

    def load_player_groups(self):
        """Loads custom player group definitions and memberships from settings/player_groups.json."""
        candidate_paths = [
            os.path.join("settings", "player_groups.json"),
            "player_groups.json"
        ]
        for p in candidate_paths:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            return data
                except Exception as e:
                    print(f"Error loading player groups from {p}: {e}")
        # Default starting template
        return {
            "Friends": {
                "members": [],
                "alert_login": True,
                "alert_logout": False,
                "sound_enabled": True
            }
        }

    def save_player_groups(self, groups_dict=None):
        """Saves current player groups to settings/player_groups.json."""
        if groups_dict is not None:
            self.player_groups = groups_dict
        os.makedirs("settings", exist_ok=True)
        p = os.path.join("settings", "player_groups.json")
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(self.player_groups, f, indent=2)
        except Exception as ex:
            print(f"Error saving player_groups.json: {ex}")

    def add_player_to_group(self, player_name, group_name, alert_login=True, alert_logout=False, sound_enabled=True):
        """Adds or moves a player into a group, creating the group if it doesn't exist."""
        clean_name = player_name.strip().strip('"')
        # Remove from any existing groups first (each player belongs to at most one custom group)
        for g_name, g_data in list(self.player_groups.items()):
            members = [m for m in g_data.get("members", []) if m.lower() != clean_name.lower()]
            g_data["members"] = members

        if group_name not in self.player_groups:
            self.player_groups[group_name] = {
                "members": [],
                "alert_login": alert_login,
                "alert_logout": alert_logout,
                "sound_enabled": sound_enabled
            }
        else:
            self.player_groups[group_name]["alert_login"] = alert_login
            self.player_groups[group_name]["alert_logout"] = alert_logout
            self.player_groups[group_name]["sound_enabled"] = sound_enabled

        if clean_name not in self.player_groups[group_name]["members"]:
            self.player_groups[group_name]["members"].append(clean_name)

        self.save_player_groups()
        self.update_wholist_gui(self.wholist_data)

    def remove_player_from_group(self, player_name):
        """Removes a player from all custom groups."""
        clean_name = player_name.strip().strip('"')
        changed = False
        for g_name, g_data in list(self.player_groups.items()):
            prev_len = len(g_data.get("members", []))
            g_data["members"] = [m for m in g_data.get("members", []) if m.lower() != clean_name.lower()]
            if len(g_data["members"]) != prev_len:
                changed = True

        if changed:
            self.save_player_groups()
            self.update_wholist_gui(self.wholist_data)

    def get_player_group(self, player_name):
        """Returns the group name the player belongs to, or None."""
        clean_name = player_name.strip().strip('"').lower()
        for g_name, g_data in self.player_groups.items():
            for m in g_data.get("members", []):
                if m.lower() == clean_name:
                    return g_name
        return None

    def show_group_toast_notification(self, title, message, icon_type="login", player_name=None, group_name=None):
        """Displays a non-disruptive floating overlay notification."""
        try:
            dur_ms = int(getattr(self, 'group_toast_duration_sec', 3) * 1000)
            pos = getattr(self, 'group_toast_position', 'bottom-right')
            toast = M59ToastNotification(
                title=title,
                message=message,
                icon_type=icon_type,
                player_name=player_name,
                group_name=group_name,
                duration_ms=dur_ms,
                position=pos,
                dashboard=self
            )
            if not hasattr(self, '_active_toasts'):
                self._active_toasts = []
            self._active_toasts.append(toast)
            toast.destroyed.connect(lambda: self._active_toasts.remove(toast) if toast in self._active_toasts else None)
            toast.show()
            toast.raise_()
        except Exception as ex:
            print(f"[M59-TOAST] Error showing toast notification: {ex}")

    def on_who_list_context_menu(self, pos, target_widget=None):
        """Right-click context menu handler for Who's Online and Offline list items."""
        src_widget = target_widget if target_widget is not None else self.who_list_widget
        item = src_widget.itemAt(pos)
        if not item:
            return
        
        # Check if right-clicked item is a section header (toggle collapse or group settings)
        hdr_group = item.data(Qt.UserRole + 1)
        if hdr_group:
            menu = QMenu(self)
            menu.setStyleSheet("""
                QMenu {
                    background-color: #0f172a;
                    color: #f8fafc;
                    border: 1px solid #334155;
                    border-radius: 6px;
                    padding: 4px;
                }
                QMenu::item {
                    padding: 6px 16px;
                    border-radius: 4px;
                    font-size: 12px;
                }
                QMenu::item:selected {
                    background-color: #0284c7;
                    color: #ffffff;
                }
            """)
            is_collapsed = (hdr_group in getattr(self, 'collapsed_groups', set()))
            toggle_txt = "▶ Expand Group" if is_collapsed else "▼ Collapse Group"
            act_toggle = menu.addAction(toggle_txt)
            act_toggle.triggered.connect(lambda: self.toggle_group_collapse(hdr_group))

            if hdr_group not in ("__OFFLINE__", "Other Players"):
                menu.addSeparator()
                act_del = menu.addAction(f"🗑 Delete Group '{hdr_group}'")
                def delete_grp(g=hdr_group):
                    reply = QMessageBox.question(
                        self, "Delete Group",
                        f"Are you sure you want to delete the group '{g}'? Players will remain in the general list.",
                        QMessageBox.Yes | QMessageBox.No
                    )
                    if reply == QMessageBox.Yes:
                        if g in self.player_groups:
                            del self.player_groups[g]
                            self.save_player_groups()
                            self.update_wholist_gui(self.wholist_data)
                act_del.triggered.connect(delete_grp)

            menu.exec(src_widget.mapToGlobal(pos))
            return

        # Player item right-clicked
        player_name = item.data(Qt.UserRole)
        if not player_name:
            return

        current_grp = self.get_player_group(player_name)

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #0f172a;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 16px;
                border-radius: 4px;
                font-size: 12px;
            }
            QMenu::item:selected {
                background-color: #0284c7;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background: #334155;
                margin: 4px 6px;
            }
        """)

        # 1. Direct Message action
        act_dm = menu.addAction(f"💬 Send Direct Message to {player_name}")
        act_dm.triggered.connect(lambda: self.open_dm_with_player(player_name))

        menu.addSeparator()

        # 2. Add / Edit Group assignment
        if current_grp:
            act_grp = menu.addAction(f"📁 Change Group (Current: {current_grp})...")
        else:
            act_grp = menu.addAction("📁 Add to Group...")

        def open_group_dialog():
            dlg = M59PlayerGroupDialog(player_name, self.player_groups, current_group=current_grp, parent=self)
            if dlg.exec():
                self.add_player_to_group(
                    player_name,
                    dlg.result_group,
                    alert_login=dlg.result_alert_login,
                    alert_logout=dlg.result_alert_logout,
                    sound_enabled=dlg.result_sound
                )

        act_grp.triggered.connect(open_group_dialog)

        # Quick Add submenu if existing groups exist
        if self.player_groups:
            quick_menu = menu.addMenu("⚡ Quick Assign to...")
            quick_menu.setStyleSheet(menu.styleSheet())
            for gn, g_cfg in sorted(self.player_groups.items()):
                if gn == current_grp:
                    continue
                q_act = quick_menu.addAction(f"📁 {gn}")
                q_act.triggered.connect(lambda checked=False, g=gn, c=g_cfg: self.add_player_to_group(
                    player_name, g,
                    alert_login=c.get("alert_login", True),
                    alert_logout=c.get("alert_logout", False),
                    sound_enabled=c.get("sound_enabled", True)
                ))

        if current_grp:
            act_rem = menu.addAction(f"✕ Remove from '{current_grp}'")
            act_rem.triggered.connect(lambda: self.remove_player_from_group(player_name))

        menu.exec(src_widget.mapToGlobal(pos))

    def toggle_group_collapse(self, group_name):
        """Toggles collapsed/expanded state of a player group in the who list."""
        if not hasattr(self, 'collapsed_groups'):
            self.collapsed_groups = set()
        if group_name in self.collapsed_groups:
            self.collapsed_groups.remove(group_name)
        else:
            self.collapsed_groups.add(group_name)
        
        # Save collapse preference
        self.save_gui_settings({"collapsed_groups": list(self.collapsed_groups)})
        self.update_wholist_gui(self.wholist_data)

    def load_gui_settings(self):
        candidate_paths = [
            os.path.join("settings", "gui_settings.json"),
            "gui_settings.json",
            os.path.join("settings", "settings.json"),
            "settings.json"
        ]
        for p in candidate_paths:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            return data
                except Exception as e:
                    print(f"Error loading gui settings from {p}: {e}")
        return {}

    def save_gui_settings(self, settings_dict):
        os.makedirs("settings", exist_ok=True)
        p = os.path.join("settings", "gui_settings.json")
        try:
            current = self.load_gui_settings()
            current.update(settings_dict)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(current, f, indent=2)
        except Exception as ex:
            print(f"Error saving gui_settings.json: {ex}")

    def trigger_pk_alert(self):
        """Triggers PK/PvP alert sound and visual red box overlay if enabled."""
        if getattr(self, 'pk_alert_enabled', True):
            if getattr(self, 'pk_sound_enabled', True):
                snd = getattr(self, 'pk_sound_path', "sound/alert.wav")
                print(f"[M59-ALERT] Triggering PK alert audio: {snd}", flush=True)
                play_audio_file(snd)
            if getattr(self, 'pk_frame_enabled', True) and getattr(self, 'pk_frame', None):
                print("[M59-ALERT] Flashing red box overlay around game window!", flush=True)
                self.pk_frame.flash(5)

    def play_tell_alert(self):
        """Triggers tell / private message audio chime if enabled."""
        if getattr(self, 'tell_sound_enabled', True):
            snd = getattr(self, 'tell_sound_path', "sound/dm_chime.wav")
            play_audio_file(snd)

    def save_sound_settings(self):
        """Saves current sound and alert configuration to gui_settings.json."""
        self.pk_alert_enabled = self.pk_chk.isChecked() if hasattr(self, 'pk_chk') else getattr(self, 'pk_alert_enabled', True)
        self.pk_sound_enabled = self.pk_alert_enabled
        self.pk_sound_path = self.pk_sound_combo.currentText() if hasattr(self, 'pk_sound_combo') else getattr(self, 'pk_sound_path', "sound/alert.wav")
        self.tell_sound_enabled = self.tell_chk.isChecked() if hasattr(self, 'tell_chk') else getattr(self, 'tell_sound_enabled', True)
        self.tell_sound_path = self.tell_sound_combo.currentText() if hasattr(self, 'tell_sound_combo') else getattr(self, 'tell_sound_path', "sound/dm_chime.wav")
        self.pk_frame_enabled = self.pk_redbox_chk.isChecked() if hasattr(self, 'pk_redbox_chk') else getattr(self, 'pk_frame_enabled', True)
        self.group_toast_duration_sec = self.toast_dur_spin.value() if hasattr(self, 'toast_dur_spin') else getattr(self, 'group_toast_duration_sec', 3)
        self.group_toast_position = self.toast_pos_combo.currentData() if hasattr(self, 'toast_pos_combo') else getattr(self, 'group_toast_position', "bottom-right")

        s = {
            "pk_alert_enabled": self.pk_alert_enabled,
            "pk_sound_enabled": self.pk_sound_enabled,
            "pk_sound_path": self.pk_sound_path,
            "tell_sound_enabled": self.tell_sound_enabled,
            "tell_sound_path": self.tell_sound_path,
            "pk_frame_enabled": self.pk_frame_enabled,
            "group_toast_duration_sec": self.group_toast_duration_sec,
            "group_toast_position": self.group_toast_position,
        }
        self.save_gui_settings(s)

    def save_debug_settings(self):
        """Saves logging and diagnostic preferences to gui_settings.json and updates runtime handlers."""
        self.console_output_enabled = self.dbg_console_chk.isChecked() if hasattr(self, 'dbg_console_chk') else getattr(self, 'console_output_enabled', True)
        self.console_debug_enabled = self.dbg_debug_level_chk.isChecked() if hasattr(self, 'dbg_debug_level_chk') else getattr(self, 'console_debug_enabled', True)
        self.file_debug_enabled = self.dbg_file_chk.isChecked() if hasattr(self, 'dbg_file_chk') else getattr(self, 'file_debug_enabled', True)
        self.progression_log_enabled = self.dbg_prog_chk.isChecked() if hasattr(self, 'dbg_prog_chk') else getattr(self, 'progression_log_enabled', True)

        s = {
            "console_output_enabled": self.console_output_enabled,
            "console_debug_enabled": self.console_debug_enabled,
            "file_debug_enabled": self.file_debug_enabled,
            "progression_log_enabled": self.progression_log_enabled,
        }
        self.save_gui_settings(s)
        setup_logging(
            debug_enabled=self.console_debug_enabled,
            console_output=self.console_output_enabled,
            file_debug=self.file_debug_enabled,
            progression_log=self.progression_log_enabled
        )

    def open_logs_folder(self):
        """Opens the logs directory in the operating system's native file explorer."""
        os.makedirs("logs", exist_ok=True)
        logs_path = os.path.abspath("logs")
        try:
            if sys.platform == 'win32':
                os.startfile(logs_path)
            elif sys.platform == 'darwin':
                import subprocess
                subprocess.Popen(['open', logs_path])
            else:
                import subprocess
                subprocess.Popen(['xdg-open', logs_path])
        except Exception as ex:
            print(f"[M59-LOG] Error opening logs folder: {ex}")

    def clear_app_logs(self):
        """Truncates and flushes active log files."""
        cleared = clear_log_files()
        if hasattr(self, 'debug_log_preview') and self.debug_log_preview:
            self.debug_log_preview.setPlainText("Log files cleared.")
        QMessageBox.information(self, "Logs Cleared", f"Cleared log file(s):\n" + "\n".join(cleared) if cleared else "No log files to clear.")

    def refresh_debug_log_preview(self):
        """Reloads recent log entries into the debug tab's log viewer."""
        if not hasattr(self, 'debug_log_preview') or not self.debug_log_preview:
            return
        
        target_file = "logs/progression_debug.log"
        if not os.path.exists(target_file) or os.path.getsize(target_file) == 0:
            target_file = "logs/companion_debug.log"

        if os.path.exists(target_file):
            try:
                with open(target_file, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                    tail_lines = lines[-60:] if len(lines) > 60 else lines
                    content = "".join(tail_lines)
                    self.debug_log_preview.setPlainText(f"--- Showing last {len(tail_lines)} lines of {target_file} ---\n\n" + content)
                    self.debug_log_preview.moveCursor(QTextCursor.End)
            except Exception as ex:
                self.debug_log_preview.setPlainText(f"Error reading log file {target_file}: {ex}")
        else:
            self.debug_log_preview.setPlainText("No logs recorded yet. Perform actions or enable logging to see output.")

    def populate_alias_table(self):
        if not hasattr(self, 'alias_table'):
            return
        aliases = self.load_commaliases()
        self.alias_table.setRowCount(0)
        for row_idx, alias in enumerate(aliases):
            self.alias_table.insertRow(row_idx)
            name_item = QTableWidgetItem(alias.get("name", "Alias"))
            hk_item = QTableWidgetItem(alias.get("hotkey", "None"))
            cmd_item = QTableWidgetItem(alias.get("command1", ""))
            send_enter_str = "Yes" if alias.get("send_enter", True) else "No"
            send_enter_item = QTableWidgetItem(send_enter_str)
            float_str = "Yes" if alias.get("show_float", False) else "No"
            float_item = QTableWidgetItem(float_str)

            self.alias_table.setItem(row_idx, 0, name_item)
            self.alias_table.setItem(row_idx, 1, hk_item)
            self.alias_table.setItem(row_idx, 2, cmd_item)
            self.alias_table.setItem(row_idx, 3, send_enter_item)
            self.alias_table.setItem(row_idx, 4, float_item)

        self.update_floating_hotkey_buttons()

    def open_add_alias_dialog(self):
        dialog = AliasEditDialog(parent=self)
        if dialog.exec() == QDialog.Accepted:
            new_data = dialog.get_alias_data()
            aliases = self.load_commaliases()
            aliases.append(new_data)
            self.save_commaliases(aliases)
            self.populate_alias_table()

    def edit_selected_alias(self):
        if not hasattr(self, 'alias_table'):
            return
        selected_rows = self.alias_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "Selection Required", "Please select an alias row to edit.")
            return
        row = selected_rows[0].row()
        aliases = self.load_commaliases()
        if 0 <= row < len(aliases):
            dialog = AliasEditDialog(alias=aliases[row], parent=self)
            if dialog.exec() == QDialog.Accepted:
                updated_data = dialog.get_alias_data()
                aliases[row] = updated_data
                self.save_commaliases(aliases)
                self.populate_alias_table()

    def delete_selected_alias(self):
        if not hasattr(self, 'alias_table'):
            return
        selected_rows = self.alias_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "Selection Required", "Please select an alias row to delete.")
            return
        row = selected_rows[0].row()
        aliases = self.load_commaliases()
        if 0 <= row < len(aliases):
            name = aliases[row].get("name", "Selected Alias")
            reply = QMessageBox.question(
                self, "Confirm Deletion", f"Are you sure you want to delete alias '{name}'?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                del aliases[row]
                self.save_commaliases(aliases)
                self.populate_alias_table()

    def refresh_m59_config_keys(self):
        try:
            used_keys = parse_config_ini()
            if used_keys:
                keys_str = ", ".join(sorted(list(used_keys))[:15])
                if len(used_keys) > 15:
                    keys_str += "..."
                QMessageBox.information(self, "M59 Config Keys", f"Detected {len(used_keys)} keybindings in Meridian 59 config.ini:\n{keys_str}")
            else:
                QMessageBox.information(self, "M59 Config Keys", "No M59 config.ini key conflicts detected.")
        except Exception as ex:
            QMessageBox.information(self, "M59 Config Status", f"Could not read config.ini:\n{ex}")

    # ==================================================================
    # SECTION 3: COMMUNICATIONS & CHAT LOGGER PAGE
    # ==================================================================
    def build_chat_logger_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # 1. Top Banner Card: Title & Floating Chatbox Launcher
        top_card = QFrame()
        top_card.setProperty("class", "WebCard")
        tc_layout = QHBoxLayout(top_card)
        tc_layout.setContentsMargins(16, 14, 16, 14)
        tc_layout.setSpacing(16)

        t_box = QVBoxLayout()
        t_box.setSpacing(3)
        t_lbl = QLabel("💬 Communications & Chat Logger")
        t_lbl.setStyleSheet("font-size: 16px; font-weight: 800; color: #f8fafc;")
        t_desc = QLabel("Real-time multi-channel stream with message filters, historical logs, and floating chatbox.")
        t_desc.setStyleSheet("font-size: 12px; color: #94a3b8;")
        t_box.addWidget(t_lbl)
        t_box.addWidget(t_desc)
        tc_layout.addLayout(t_box, 1)

        # Floating Chatbox Launcher Button
        self.floating_chat_btn = QPushButton("💬 Floating Chatbox")
        self.floating_chat_btn.setProperty("class", "WebBtnPrimary")
        self.floating_chat_btn.setToolTip("Open always-on-top floating chatbox that can be placed over the game chat")
        self.floating_chat_btn.clicked.connect(self.trigger_launch_floating_chat)
        tc_layout.addWidget(self.floating_chat_btn)

        layout.addWidget(top_card)

        # 2. Header Filter Bar: Channels & Search Controls
        hdr_card = QFrame()
        hdr_card.setProperty("class", "WebCard")
        hc_layout = QHBoxLayout(hdr_card)
        hc_layout.setContentsMargins(14, 10, 14, 10)
        hc_layout.setSpacing(10)

        # Live Stream Mode Indicator Button
        self.mode_btn = QPushButton("🟢 LIVE STREAM")
        self.mode_btn.setProperty("class", "WebBtnSecondary")
        self.mode_btn.setStyleSheet("color: #94a3b8; font-weight: 800;")
        self.mode_btn.clicked.connect(self.return_to_live_stream)
        hc_layout.addWidget(self.mode_btn)

        # Channel Filters
        self.channel_btns = {}
        channels = [
            ("all", "All Channels"),
            ("private", "Private Messages"),
            ("chat", "Chat / Say"),
            ("combat", "Combat Log"),
            ("improves", "Improves"),
            ("system", "System Broadcasts")
        ]

        for cid, label in channels:
            btn = QPushButton(label)
            btn.setProperty("class", "WebTabBtn")
            if cid == "all":
                btn.setProperty("active", "true")
            btn.clicked.connect(lambda checked=False, c=cid: self.set_chat_channel_filter(c))
            self.channel_btns[cid] = btn
            hc_layout.addWidget(btn)

        hc_layout.addStretch()

        # Clear Stream Button
        clear_btn = QPushButton("Clear Stream")
        clear_btn.setProperty("class", "WebBtnSecondary")
        clear_btn.clicked.connect(lambda: self.chat_stream_view.clear())
        hc_layout.addWidget(clear_btn)

        # Search Query Input
        self.chat_search = QLineEdit()
        self.chat_search.setPlaceholderText("Filter chat log...")
        self.chat_search.setFixedWidth(180)
        self.chat_search.textChanged.connect(self.filter_chat_stream)
        hc_layout.addWidget(self.chat_search)

        layout.addWidget(hdr_card)

        # 3. Splitter: Historical Logs Sidebar + Stream View
        chat_splitter = QSplitter(Qt.Horizontal)
        chat_splitter.setHandleWidth(8)

        # Historical Logs Drawer Card
        hist_card = QFrame()
        hist_card.setProperty("class", "WebCard")
        hist_card.setMaximumWidth(220)
        hc_layout_inner = QVBoxLayout(hist_card)
        hc_layout_inner.setContentsMargins(12, 12, 12, 12)

        hl_title = QLabel("HISTORICAL LOGS")
        hl_title.setStyleSheet("font-size: 10px; font-weight: 800; color: #64748b; letter-spacing: 0.8px;")
        hc_layout_inner.addWidget(hl_title)

        self.hist_log_list = QListWidget()
        self.hist_log_list.setStyleSheet("""
            QListWidget {
                background-color: #030712;
                border: 1px solid #334155;
                border-radius: 8px;
            }
            QListWidget::item {
                padding: 6px 8px;
                font-size: 11px;
                color: #94a3b8;
                border-bottom: 1px solid #111827;
            }
            QListWidget::item:hover {
                background-color: #162032;
                color: #f1f5f9;
            }
        """)
        self.hist_log_list.itemClicked.connect(self.load_selected_historical_log)
        hc_layout_inner.addWidget(self.hist_log_list)

        import_log_btn = QPushButton("Import External Log")
        import_log_btn.setProperty("class", "WebBtnSecondary")
        import_log_btn.clicked.connect(self.import_log_file_dialog)
        hc_layout_inner.addWidget(import_log_btn)

        chat_splitter.addWidget(hist_card)

        # Stream View
        self.chat_stream_view = QTextEdit()
        self.chat_stream_view.setReadOnly(True)
        self.chat_stream_view.setStyleSheet("""
            QTextEdit {
                background-color: #030712;
                border: 1px solid #334155;
                border-radius: 12px;
                padding: 14px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
                line-height: 1.5;
            }
        """)
        chat_splitter.addWidget(self.chat_stream_view)

        layout.addWidget(chat_splitter, 1)

        # 4. Bottom Manual Line Input Bar
        bottom_card = QFrame()
        bottom_card.setProperty("class", "WebCard")
        bc_layout = QHBoxLayout(bottom_card)
        bc_layout.setContentsMargins(12, 10, 12, 10)
        bc_layout.setSpacing(10)

        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Paste log line or chat output (e.g. You have improved in the art of Slash...)...")
        self.chat_input.returnPressed.connect(self.parse_chat_input)
        bc_layout.addWidget(self.chat_input, 1)

        parse_btn = QPushButton("Parse Line")
        parse_btn.setProperty("class", "WebBtnPrimary")
        parse_btn.clicked.connect(self.parse_chat_input)
        bc_layout.addWidget(parse_btn)

        layout.addWidget(bottom_card)

        return page

    # ==================================================================
    # SECTION: VAULT STORAGE LEDGER PAGE (Barloque & Hungry Vaults)
    # ==================================================================
    def build_vault_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # Header Card
        hdr_card = QFrame()
        hdr_card.setProperty("class", "WebCard")
        hc_layout = QHBoxLayout(hdr_card)
        hc_layout.setContentsMargins(16, 14, 16, 14)

        t_box = QVBoxLayout()
        t_lbl = QLabel("🏦 M59 Vault Management & Storage Ledger")
        t_lbl.setStyleSheet("font-size: 16px; font-weight: 800; color: #f8fafc;")
        s_lbl = QLabel("Track and manage Barloque Vault and Hungry Vault items across application restarts.")
        s_lbl.setStyleSheet("font-size: 12px; color: #64748b;")
        t_box.addWidget(t_lbl)
        t_box.addWidget(s_lbl)
        hc_layout.addLayout(t_box)

        hc_layout.addStretch()
        layout.addWidget(hdr_card)

        # Main Splitter for Barloque & Hungry Vaults
        vault_splitter = QSplitter(Qt.Horizontal)
        vault_splitter.setHandleWidth(8)

        # 1. Barloque Vault Card
        b_card = QFrame()
        b_card.setProperty("class", "WebCard")
        bc_layout = QVBoxLayout(b_card)
        bc_layout.setContentsMargins(14, 14, 14, 14)
        bc_layout.setSpacing(8)

        bc_hdr = QHBoxLayout()
        bc_title = QLabel("🏰 BARLOQUE VAULT")
        bc_title.setStyleSheet("font-size: 11px; font-weight: 800; color: #94a3b8; letter-spacing: 0.8px;")
        bc_hdr.addWidget(bc_title)
        bc_hdr.addStretch()

        b_search = QLineEdit()
        b_search.setPlaceholderText("Filter Barloque items...")
        b_search.setFixedWidth(160)
        b_search.textChanged.connect(lambda: self.update_vault_table("barloque"))
        bc_hdr.addWidget(b_search)

        b_scan_btn = QPushButton("🔄 Scan Barloque")
        b_scan_btn.setProperty("class", "WebBtnPrimary")
        b_scan_btn.setToolTip("Triggers automated in-game vault scanning.")
        b_scan_btn.clicked.connect(lambda: self.trigger_vault_scan("barloque"))
        bc_hdr.addWidget(b_scan_btn)

        bc_layout.addLayout(bc_hdr)

        b_table = QTableWidget(0, 2)
        b_table.setMinimumHeight(240)
        b_table.verticalHeader().setVisible(False)
        b_table.setHorizontalHeaderLabels(["ITEM NAME", "QTY"])
        b_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        b_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        bc_layout.addWidget(b_table)

        b_status = QLabel("No scan data")
        b_status.setStyleSheet("font-size: 11px; color: #64748b; font-style: italic;")
        bc_layout.addWidget(b_status)

        vault_splitter.addWidget(b_card)

        # 2. Hungry Vault Card
        h_card = QFrame()
        h_card.setProperty("class", "WebCard")
        hc_layout2 = QVBoxLayout(h_card)
        hc_layout2.setContentsMargins(14, 14, 14, 14)
        hc_layout2.setSpacing(8)

        hc_hdr = QHBoxLayout()
        hc_title = QLabel("🏝️ HUNGRY VAULT")
        hc_title.setStyleSheet("font-size: 11px; font-weight: 800; color: #3b82f6; letter-spacing: 0.8px;")
        hc_hdr.addWidget(hc_title)
        hc_hdr.addStretch()

        h_search = QLineEdit()
        h_search.setPlaceholderText("Filter Hungry items...")
        h_search.setFixedWidth(160)
        h_search.textChanged.connect(lambda: self.update_vault_table("hungry"))
        hc_hdr.addWidget(h_search)

        h_scan_btn = QPushButton("🔄 Scan Hungry")
        h_scan_btn.setProperty("class", "WebBtnSecondary")
        h_scan_btn.setToolTip("Triggers automated in-game vault scanning.")
        h_scan_btn.clicked.connect(lambda: self.trigger_vault_scan("hungry"))
        hc_hdr.addWidget(h_scan_btn)

        hc_layout2.addLayout(hc_hdr)

        h_table = QTableWidget(0, 2)
        h_table.setMinimumHeight(240)
        h_table.verticalHeader().setVisible(False)
        h_table.setHorizontalHeaderLabels(["ITEM NAME", "QTY"])
        h_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        h_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hc_layout2.addWidget(h_table)

        h_status = QLabel("No scan data")
        h_status.setStyleSheet("font-size: 11px; color: #64748b; font-style: italic;")
        hc_layout2.addWidget(h_status)

        vault_splitter.addWidget(h_card)

        vault_splitter.setSizes([500, 500])
        layout.addWidget(vault_splitter, 1)

        self.vault_page_widgets = {
            "barloque": {"table": b_table, "search": b_search, "status": b_status, "btn": b_scan_btn},
            "hungry": {"table": h_table, "search": h_search, "status": h_status, "btn": h_scan_btn}
        }

        return page

    def update_bank_ui(self):
        """Updates clean text displays for Mainland Bank and Island Bank across tiles."""
        if hasattr(self, 'bank_manager') and self.bank_manager:
            mb = self.bank_manager.balances.get("mainland", 0)
            ib = self.bank_manager.balances.get("island", 0)
            tot = mb + ib
            if hasattr(self, 'bank_mainland_lbl') and self.bank_mainland_lbl:
                self.bank_mainland_lbl.setText(f"Mainland Bank: {mb:,} shillings")
            if hasattr(self, 'bank_island_lbl') and self.bank_island_lbl:
                self.bank_island_lbl.setText(f"Island Bank: {ib:,} shillings")
            if hasattr(self, 'vault_bank_mainland_lbl') and self.vault_bank_mainland_lbl:
                self.vault_bank_mainland_lbl.setText(f"Mainland Bank: {mb:,} shillings")
            if hasattr(self, 'vault_bank_island_lbl') and self.vault_bank_island_lbl:
                self.vault_bank_island_lbl.setText(f"Island Bank: {ib:,} shillings")
            if hasattr(self, 'dock_bank_total_lbl') and self.dock_bank_total_lbl:
                self.dock_bank_total_lbl.setText(f"Total: {tot:,} sh")
            if hasattr(self, 'dock_bank_mainland_lbl') and self.dock_bank_mainland_lbl:
                self.dock_bank_mainland_lbl.setText(f"{mb:,} sh")
            if hasattr(self, 'dock_bank_island_lbl') and self.dock_bank_island_lbl:
                self.dock_bank_island_lbl.setText(f"{ib:,} sh")

    def update_vault_table(self, vt):
        """Populates vault table widgets (tile and page) with filtered data."""
        widget_groups = []
        if hasattr(self, 'vault_widgets') and vt in self.vault_widgets:
            widget_groups.append(self.vault_widgets[vt])
        if hasattr(self, 'vault_page_widgets') and vt in self.vault_page_widgets:
            widget_groups.append(self.vault_page_widgets[vt])

        if not widget_groups:
            return

        items = self.vault_data.get(vt, [])
        last_scan = self.vault_last_scan.get(vt, "No scan data")

        for wg in widget_groups:
            table = wg["table"]
            search_txt = wg["search"].text().lower().strip()
            status_lbl = wg["status"]

            table.setRowCount(0)
            filtered_count = 0
            for item in items:
                name = item.get("item", "")
                qty = str(item.get("quantity", "1"))
                if not search_txt or search_txt in name.lower():
                    row = table.rowCount()
                    table.insertRow(row)
                    table.setItem(row, 0, QTableWidgetItem(name.title()))
                    qty_item = QTableWidgetItem(qty)
                    qty_item.setTextAlignment(Qt.AlignCenter)
                    table.setItem(row, 1, qty_item)
                    filtered_count += 1

            if last_scan and last_scan != "No scan data":
                status_lbl.setText(f"Last Scan: {last_scan} ({len(items)} items found)")
            else:
                status_lbl.setText("No scan data")

    def save_vault_cache_file(self, vt, items, last_scan_str):
        """Saves vault configuration and item state to disk for current character."""
        cname = self.char_name if self.char_name and self.char_name != "--" else "Unknown"
        sn = get_safe_name(cname)
        now_str = last_scan_str if last_scan_str else datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        save_data = {
            "timestamp": time.time(),
            "last_scan": now_str,
            "items": items if items is not None else []
        }
        for folder in ["settings", "logs"]:
            os.makedirs(folder, exist_ok=True)
            save_path = os.path.join(folder, f"{sn}_vault_{vt}.json")
            try:
                with open(save_path, "w", encoding="utf-8") as f:
                    json.dump(save_data, f, indent=4)
            except Exception as ex:
                print(f"[M59-VAULT] Failed saving vault file to {save_path}: {ex}", flush=True)

    def load_vault_cache(self):
        """Loads persistent vault inventory save files for current character or latest cached file."""
        cname = self.char_name if self.char_name and self.char_name != "--" else ""
        sn = get_safe_name(cname) if cname else ""

        for vt in ["barloque", "hungry"]:
            loaded = False
            paths = []
            if sn:
                paths.extend([
                    f"settings/{sn}_vault_{vt}.json",
                    f"logs/{sn}_vault_{vt}.json",
                    f"settings/{sn.lower()}_vault_{vt}.json",
                    f"logs/{sn.lower()}_vault_{vt}.json"
                ])
            paths.extend([
                f"settings/unknown_vault_{vt}.json",
                f"logs/unknown_vault_{vt}.json"
            ])
            # Search settings/ and logs/ for any existing vault file if character not identified yet
            for folder in ["settings", "logs"]:
                if os.path.exists(folder):
                    try:
                        for fn in sorted(os.listdir(folder), key=lambda x: os.path.getmtime(os.path.join(folder, x)), reverse=True):
                            if fn.endswith(f"_vault_{vt}.json"):
                                fp = os.path.join(folder, fn)
                                if fp not in paths:
                                    paths.append(fp)
                    except Exception:
                        pass

            for p in paths:
                if os.path.exists(p):
                    try:
                        with open(p, "r", encoding="utf-8", errors="ignore") as f:
                            d = json.load(f)
                            items = d.get("items", [])
                            last_scan = d.get("last_scan") or d.get("timestamp")
                            if isinstance(last_scan, (int, float)):
                                last_scan = datetime.fromtimestamp(last_scan).strftime('%Y-%m-%d %H:%M:%S')
                            elif not last_scan:
                                last_scan = "Loaded from cache"
                            self.vault_data[vt] = items
                            self.vault_last_scan[vt] = last_scan
                            self.update_vault_table(vt)
                            loaded = True
                            if sn and "unknown_vault_" in p and sn != "unknown":
                                self.save_vault_cache_file(vt, items, last_scan)
                            break
                    except Exception as ex:
                        print(f"[M59-VAULT] Failed loading vault cache from {p}: {ex}", flush=True)

            if not loaded:
                self.vault_data[vt] = []
                self.vault_last_scan[vt] = "No scan data"
                self.update_vault_table(vt)

        self.update_bank_ui()

    def trigger_vault_scan(self, vt):
        """Triggers an automated vault scan sequence in a background thread."""
        if not self.main_hwnd or not win32gui or not win32gui.IsWindow(self.main_hwnd):
            QMessageBox.warning(self, "Vault Scan Error", "Meridian 59 game client is not attached or process window handle is invalid.")
            return

        if not perform_vault_scan:
            QMessageBox.warning(self, "Vault Scan Error", "m59_vault scanner module is not available.")
            return

        for group in [getattr(self, 'vault_widgets', {}), getattr(self, 'vault_page_widgets', {})]:
            if vt in group:
                group[vt]["btn"].setEnabled(False)
                group[vt]["status"].setText("Scanning vault in progress...")

        def run_scan():
            try:
                c_name = self.char_name if self.char_name and self.char_name != "--" else "Unknown"
                inv = perform_vault_scan(self.main_hwnd, c_name, vt)
                now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                if inv is not None:
                    self.signals.vault_updated.emit(vt, inv, now_str)
                else:
                    self.signals.vault_updated.emit(vt, [], "Scan failed or window not open")
            except Exception as ex:
                print(f"[M59-VAULT] Scan error: {ex}", flush=True)
                self.signals.vault_updated.emit(vt, [], f"Scan error: {ex}")

        threading.Thread(target=run_scan, daemon=True).start()

    def on_vault_updated(self, vt, inv, last_scan_str):
        """Slot called on main thread when vault scan completes."""
        for group in [getattr(self, 'vault_widgets', {}), getattr(self, 'vault_page_widgets', {})]:
            if vt in group:
                group[vt]["btn"].setEnabled(True)
        if inv is not None and len(inv) > 0:
            self.vault_data[vt] = inv
            self.vault_last_scan[vt] = last_scan_str
            self.save_vault_cache_file(vt, inv, last_scan_str)
        elif inv is not None and "Scan failed" not in last_scan_str and "Scan error" not in last_scan_str:
            self.vault_data[vt] = inv
            self.vault_last_scan[vt] = last_scan_str
            self.save_vault_cache_file(vt, inv, last_scan_str)
        self.update_vault_table(vt)

    # ==================================================================
    # SECTION: KILL BOOK PAGE (Monsters Bestiary & Players PK Ledger)
    # ==================================================================
    def build_killbook_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # Header Card
        hdr_card = QFrame()
        hdr_card.setProperty("class", "WebCard")
        hc_layout = QHBoxLayout(hdr_card)
        hc_layout.setContentsMargins(16, 14, 16, 14)

        t_box = QVBoxLayout()
        t_lbl = QLabel("⚔️ M59 Kill Book & Bestiary")
        t_lbl.setStyleSheet("font-size: 16px; font-weight: 800; color: #f8fafc;")
        s_lbl = QLabel("All-time persistent kill records and active session combat statistics.")
        s_lbl.setStyleSheet("font-size: 12px; color: #64748b;")
        t_box.addWidget(t_lbl)
        t_box.addWidget(s_lbl)
        hc_layout.addLayout(t_box)

        hc_layout.addStretch()

        # Stat Badges
        self.kb_monsters_badge = QLabel("0 Monsters Slain")
        self.kb_monsters_badge.setStyleSheet("background-color: #064e3b; color: #94a3b8; font-size: 11px; font-weight: 800; padding: 4px 10px; border-radius: 8px;")
        hc_layout.addWidget(self.kb_monsters_badge)

        self.kb_players_badge = QLabel("0 Players Defeated")
        self.kb_players_badge.setStyleSheet("background-color: #581c87; color: #c084fc; font-size: 11px; font-weight: 800; padding: 4px 10px; border-radius: 8px;")
        hc_layout.addWidget(self.kb_players_badge)

        self.kb_total_badge = QLabel("0 Total Victories")
        self.kb_total_badge.setStyleSheet("background-color: #881337; color: #fda4af; font-size: 11px; font-weight: 800; padding: 4px 10px; border-radius: 8px;")
        hc_layout.addWidget(self.kb_total_badge)

        layout.addWidget(hdr_card)

        # Main Splitter for Monsters & Players
        kb_splitter = QSplitter(Qt.Horizontal)
        kb_splitter.setHandleWidth(8)

        # Left Card: Monsters Bestiary
        m_card = QFrame()
        m_card.setProperty("class", "WebCard")
        mc_layout = QVBoxLayout(m_card)
        mc_layout.setContentsMargins(14, 14, 14, 14)

        mc_hdr = QHBoxLayout()
        mc_title = QLabel("🧟 MONSTERS BESTIARY")
        mc_title.setStyleSheet("font-size: 11px; font-weight: 800; color: #94a3b8; letter-spacing: 0.8px;")
        mc_hdr.addWidget(mc_title)
        mc_hdr.addStretch()

        self.kb_monsters_search = QLineEdit()
        self.kb_monsters_search.setPlaceholderText("Filter monsters...")
        self.kb_monsters_search.setFixedWidth(160)
        self.kb_monsters_search.textChanged.connect(self.update_killbook_ui)
        mc_hdr.addWidget(self.kb_monsters_search)
        mc_layout.addLayout(mc_hdr)

        self.kb_monsters_table = QTableWidget(0, 4)
        self.kb_monsters_table.verticalHeader().setVisible(False)
        self.kb_monsters_table.setHorizontalHeaderLabels(["MONSTER NAME", "ALL-TIME", "SESSION", "STATUS"])
        self.kb_monsters_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.kb_monsters_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.kb_monsters_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.kb_monsters_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        mc_layout.addWidget(self.kb_monsters_table)

        kb_splitter.addWidget(m_card)

        self.kb_monsters_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.kb_monsters_table.itemSelectionChanged.connect(self.on_killbook_monster_selected)

        # Right Card: Players PK Ledger
        p_card = QFrame()
        p_card.setProperty("class", "WebCard")
        pc_layout = QVBoxLayout(p_card)
        pc_layout.setContentsMargins(14, 14, 14, 14)

        pc_hdr = QHBoxLayout()
        pc_title = QLabel("⚔️ PLAYERS PK LEDGER")
        pc_title.setStyleSheet("font-size: 11px; font-weight: 800; color: #c084fc; letter-spacing: 0.8px;")
        pc_hdr.addWidget(pc_title)
        pc_hdr.addStretch()

        self.kb_players_search = QLineEdit()
        self.kb_players_search.setPlaceholderText("Filter players...")
        self.kb_players_search.setFixedWidth(160)
        self.kb_players_search.textChanged.connect(self.update_killbook_ui)
        pc_hdr.addWidget(self.kb_players_search)
        pc_layout.addLayout(pc_hdr)

        self.kb_players_table = QTableWidget(0, 4)
        self.kb_players_table.verticalHeader().setVisible(False)
        self.kb_players_table.setHorizontalHeaderLabels(["PLAYER NAME", "ALL-TIME", "SESSION", "STATUS"])
        self.kb_players_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.kb_players_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.kb_players_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.kb_players_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        pc_layout.addWidget(self.kb_players_table)

        kb_splitter.addWidget(p_card)

        # Third Card: Monster BGF Model & Sprite Viewer (Available in section, not tile)
        v_card = QFrame()
        v_card.setProperty("class", "WebCard")
        vc_layout = QVBoxLayout(v_card)
        vc_layout.setContentsMargins(14, 14, 14, 14)

        vc_hdr = QHBoxLayout()
        self.kb_bgf_title = QLabel("🖼️ MODEL & SPRITE VIEWER")
        self.kb_bgf_title.setStyleSheet("font-size: 11px; font-weight: 800; color: #38bdf8; letter-spacing: 0.8px;")
        vc_hdr.addWidget(self.kb_bgf_title)
        vc_hdr.addStretch()
        vc_layout.addLayout(vc_hdr)

        # Canvas Container
        self.kb_bgf_canvas = QLabel("Select a monster from the Bestiary to view sprite model")
        self.kb_bgf_canvas.setAlignment(Qt.AlignCenter)
        self.kb_bgf_canvas.setMinimumSize(200, 200)
        self.kb_bgf_canvas.setStyleSheet("""
            background-color: #0f172a;
            border: 1px solid #1e293b;
            border-radius: 10px;
            color: #64748b;
            font-size: 12px;
            font-weight: 600;
            padding: 10px;
        """)
        vc_layout.addWidget(self.kb_bgf_canvas, 1)

        # Controls Container
        ctrl_box = QVBoxLayout()
        ctrl_box.setSpacing(8)

        # Pose Slider Row
        pose_row = QHBoxLayout()
        p_lbl = QLabel("Pose:")
        p_lbl.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 700;")
        self.kb_bgf_pose_slider = QSlider(Qt.Horizontal)
        self.kb_bgf_pose_slider.setRange(0, 0)
        self.kb_bgf_pose_slider.setEnabled(False)
        self.kb_bgf_pose_slider.valueChanged.connect(self.on_bgf_slider_changed)
        pose_row.addWidget(p_lbl)
        pose_row.addWidget(self.kb_bgf_pose_slider)
        ctrl_box.addLayout(pose_row)

        # Angle Slider Row
        angle_row = QHBoxLayout()
        a_lbl = QLabel("Angle:")
        a_lbl.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 700;")
        self.kb_bgf_angle_slider = QSlider(Qt.Horizontal)
        self.kb_bgf_angle_slider.setRange(0, 5)
        self.kb_bgf_angle_slider.setEnabled(False)
        self.kb_bgf_angle_slider.valueChanged.connect(self.on_bgf_slider_changed)
        angle_row.addWidget(a_lbl)
        angle_row.addWidget(self.kb_bgf_angle_slider)
        ctrl_box.addLayout(angle_row)

        # Frame Info Badge
        self.kb_bgf_info_lbl = QLabel("No sprite loaded")
        self.kb_bgf_info_lbl.setAlignment(Qt.AlignCenter)
        self.kb_bgf_info_lbl.setStyleSheet("color: #64748b; font-size: 11px; font-weight: 600;")
        ctrl_box.addWidget(self.kb_bgf_info_lbl)

        vc_layout.addLayout(ctrl_box)

        kb_splitter.addWidget(v_card)
        kb_splitter.setSizes([340, 340, 300])

        layout.addWidget(kb_splitter, 1)

        # Initial Population
        self.update_killbook_ui()

        return page

    def load_kill_book(self):
        """Loads persistent kill records for character from settings JSON file."""
        if hasattr(self, 'combat_monitor') and self.combat_monitor:
            self.combat_monitor.kill_book = self.combat_monitor._load_kill_book()
        self.update_killbook_ui()

    def on_killbook_monster_selected(self):
        """Qt Slot called when a monster row is selected in the Bestiary table."""
        selected = self.kb_monsters_table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        item = self.kb_monsters_table.item(row, 0)
        if item:
            self.load_monster_bgf_viewer(item.text())

    def load_monster_bgf_viewer(self, monster_name):
        """Loads BGF frames for selected monster and populates image viewer controls."""
        self.kb_bgf_title.setText(f"🖼️ {monster_name.upper()} SPRITE MODEL")
        self.current_bgf_frames = []
        self.current_bgf_image_pixmaps = []

        self.kb_bgf_pose_slider.blockSignals(True)
        self.kb_bgf_angle_slider.blockSignals(True)
        self.kb_bgf_pose_slider.setValue(0)
        self.kb_bgf_angle_slider.setValue(0)
        self.kb_bgf_pose_slider.setEnabled(False)
        self.kb_bgf_angle_slider.setEnabled(False)
        self.kb_bgf_pose_slider.blockSignals(False)
        self.kb_bgf_angle_slider.blockSignals(False)

        if getattr(self, "bgf_manager", None):
            cleaned_sel = ''.join(c for c in monster_name.lower() if c.isalnum() or c.isspace() or c == "'" or c == "-")
            internal_name = self.bgf_manager.mob_mapping.get(cleaned_sel)
            if not internal_name:
                internal_name = self.bgf_manager.mob_mapping.get(cleaned_sel.replace(" ", ""))
            if not internal_name:
                internal_name = self.bgf_manager.mob_mapping.get(monster_name.lower())

            if internal_name:
                bgf_path = self.bgf_manager.find_bgf_for_monster(internal_name)
                if bgf_path:
                    frames = self.bgf_manager.load_bgf_frames(bgf_path)
                    if frames:
                        self.current_bgf_frames = frames
                        self.current_bgf_num_groups = 1
                        if hasattr(self.bgf_manager, "get_bgf_header"):
                            hdr = self.bgf_manager.get_bgf_header(bgf_path)
                            if hdr and "num_groups" in hdr:
                                self.current_bgf_num_groups = hdr["num_groups"]

                        pixmaps = []
                        for f in frames:
                            img = f.get("image") if isinstance(f, dict) else f
                            pix = pil_image_to_qpixmap(img)
                            pixmaps.append(pix)
                        self.current_bgf_image_pixmaps = pixmaps

                        num_frames = len(frames)
                        if num_frames >= 6:
                            if self.current_bgf_num_groups == 6 or (num_frames in (12, 18, 24) and self.current_bgf_num_groups > 1):
                                poses_per_angle = max(1, num_frames // 6)
                                max_pose = max(0, poses_per_angle - 1)
                            else:
                                max_pose = max(0, (num_frames // 6) - 1)
                            self.kb_bgf_pose_slider.setMaximum(max_pose)
                            self.kb_bgf_pose_slider.setEnabled(max_pose > 0)
                            self.kb_bgf_angle_slider.setMaximum(5)
                            self.kb_bgf_angle_slider.setEnabled(True)
                        else:
                            # Single-pose or fewer than 6 angles
                            self.kb_bgf_pose_slider.setMaximum(0)
                            self.kb_bgf_pose_slider.setEnabled(False)
                            self.kb_bgf_angle_slider.setMaximum(max(0, num_frames - 1))
                            self.kb_bgf_angle_slider.setEnabled(num_frames > 1)
                        self.kb_bgf_pose_slider.setValue(0)
                        self.kb_bgf_angle_slider.setValue(0)
                        self.show_bgf_frame(0)
                        return

        self.kb_bgf_canvas.setPixmap(QPixmap())
        self.kb_bgf_canvas.setText("No sprite image available for this monster")
        self.kb_bgf_info_lbl.setText("No BGF sprite asset found")

    def on_bgf_slider_changed(self):
        """Handles pose & angle slider adjustments to render specific BGF frame."""
        num_frames = len(getattr(self, 'current_bgf_image_pixmaps', []))
        if num_frames == 0:
            return
        pose = self.kb_bgf_pose_slider.value()
        angle = self.kb_bgf_angle_slider.value()
        num_groups = getattr(self, 'current_bgf_num_groups', 1)
        
        try:
            from m59_bgf import resolve_bgf_frame_index
            index = resolve_bgf_frame_index(pose, angle, num_frames, num_groups)
        except Exception:
            if num_frames >= 6:
                index = pose * 6 + angle
            else:
                index = max(pose, angle)
        self.show_bgf_frame(index)

    def show_bgf_frame(self, index):
        """Displays frame at given index on BGF viewer canvas."""
        if not getattr(self, 'current_bgf_image_pixmaps', None):
            return
        num_frames = len(self.current_bgf_image_pixmaps)
        if index < 0:
            index = 0
        elif index >= num_frames:
            index = num_frames - 1
        pix = self.current_bgf_image_pixmaps[index]
        if pix and not pix.isNull():
            scaled = pix.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.kb_bgf_canvas.setPixmap(scaled)
            pose = self.kb_bgf_pose_slider.value()
            angle = self.kb_bgf_angle_slider.value()
            num_groups = getattr(self, 'current_bgf_num_groups', 1)
            if num_frames >= 6:
                is_dir_major = num_groups == 6 or (num_frames in (12, 18, 24) and num_groups > 1)
                scheme_str = "Dir-Major" if is_dir_major else "Angle-Major"
                self.kb_bgf_info_lbl.setText(f"Pose: {pose} | Angle: {angle*60}° ({angle}/5) | Frame: {index+1}/{num_frames} [{scheme_str}]")
            else:
                self.kb_bgf_info_lbl.setText(f"Angle: {angle} | Frame: {index+1}/{num_frames}")
        else:
            self.kb_bgf_canvas.setPixmap(QPixmap())
            self.kb_bgf_canvas.setText("No sprite frame image")

    def update_killbook_ui(self):
        if not hasattr(self, 'kb_monsters_table') or not hasattr(self, 'kb_players_table'):
            return

        all_time = getattr(self.combat_monitor, 'kill_book', {"monsters": {}, "players": {}})
        all_mobs = all_time.get("monsters", {}) if isinstance(all_time, dict) else {}
        all_plys = all_time.get("players", {}) if isinstance(all_time, dict) else {}

        session_mobs = self.session_kills.get("monsters", {})
        session_plys = self.session_kills.get("players", {})

        m_filter = self.kb_monsters_search.text().lower() if hasattr(self, 'kb_monsters_search') else ""
        p_filter = self.kb_players_search.text().lower() if hasattr(self, 'kb_players_search') else ""

        # Populate Monsters
        self.kb_monsters_table.setRowCount(0)
        monster_names = sorted(list(set(all_mobs.keys()) | set(session_mobs.keys())))
        total_m_count = 0

        for name in monster_names:
            at = all_mobs.get(name, 0)
            se = session_mobs.get(name, 0)
            total_m_count += max(at, se) if at > 0 else se

            if m_filter and m_filter not in name.lower():
                continue

            row = self.kb_monsters_table.rowCount()
            self.kb_monsters_table.insertRow(row)

            display_name = name.title()
            self.kb_monsters_table.setItem(row, 0, QTableWidgetItem(display_name))
            self.kb_monsters_table.setItem(row, 1, QTableWidgetItem(str(at)))
            self.kb_monsters_table.setItem(row, 2, QTableWidgetItem(f"+{se}" if se > 0 else "0"))

            status_str = "🟢 Active Hunt" if se > 0 else "Recorded"
            self.kb_monsters_table.setItem(row, 3, QTableWidgetItem(status_str))

        # Populate Players
        self.kb_players_table.setRowCount(0)
        player_names = sorted(list(set(all_plys.keys()) | set(session_plys.keys())))
        total_p_count = 0

        for name in player_names:
            at = all_plys.get(name, 0)
            se = session_plys.get(name, 0)
            total_p_count += max(at, se) if at > 0 else se

            if p_filter and p_filter not in name.lower():
                continue

            row = self.kb_players_table.rowCount()
            self.kb_players_table.insertRow(row)

            display_name = name.title()
            self.kb_players_table.setItem(row, 0, QTableWidgetItem(display_name))
            self.kb_players_table.setItem(row, 1, QTableWidgetItem(str(at)))
            self.kb_players_table.setItem(row, 2, QTableWidgetItem(f"+{se}" if se > 0 else "0"))

            status_str = "⚔️ PK Defeated" if se > 0 else "Logged"
            self.kb_players_table.setItem(row, 3, QTableWidgetItem(status_str))

        # Update Badges
        self.kb_monsters_badge.setText(f"{total_m_count} Monsters Slain")
        self.kb_players_badge.setText(f"{total_p_count} Players Defeated")
        self.kb_total_badge.setText(f"{total_m_count + total_p_count} Total Victories")

    # ==================================================================
    # INVENTORY SCRAPER & METRICS CALCULATIONS ENGINE
    # ==================================================================
    def set_progress_bar_color(self, bar, perc):
        val = int(min(100, max(0, perc)))
        bar.setValue(val)
        if perc >= 90:
            chunk_col = "#ef4444"  # Red
        elif perc >= 75:
            chunk_col = "#f97316"  # Orange
        elif perc >= 50:
            chunk_col = "#eab308"  # Yellow
        else:
            chunk_col = "#22c55e"  # Green

        bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: #1e293b;
                border: none;
                border-radius: 2px;
                text-align: center;
                color: #f8fafc;
            }}
            QProgressBar::chunk {{
                background-color: {chunk_col};
                border-radius: 2px;
            }}
        """)

    def categorize_item(self, item_name):
        name_l = item_name.lower()
        if any(k in name_l for k in ["herb", "reagent", "flower", "root", "leaf", "mushroom", "heartstone", "blood", "powder", "berry"]):
            return "🧪 Reagents"
        if any(k in name_l for k in ["potion", "flask", "vial", "bottle", "elixir", "draught", "brew"]):
            return "🍷 Potions"
        if any(k in name_l for k in ["sword", "scimitar", "dagger", "axe", "hammer", "mace", "bow", "wand", "staff", "spear", "crossbow", "bonkstick"]):
            return "⚔️ Weapons"
        if any(k in name_l for k in ["robe", "shirt", "pants", "armor", "shield", "helm", "gauntlet", "boots", "cape", "ring", "amulet", "mask", "plate", "chain", "leather", "scale"]):
            return "🛡️ Armor"
        if any(k in name_l for k in ["pie", "meat", "bread", "apple", "soup", "stew", "grapes", "cheese", "wine", "ale", "fish"]):
            return "🍖 Food & Drink"
        if any(k in name_l for k in ["shilling", "gem", "ruby", "emerald", "sapphire", "diamond", "gold"]):
            return "💎 Treasures"
        return "📦 General"

    def poll_inventory(self):
        """Polls inventory from live memory via InventoryScraper."""
        if self.inventory_scraper and self.target_pid:
            try:
                raw_items = self.inventory_scraper.scan_inventory()
                if raw_items is not None and process_inventory:
                    calc_items = []
                    for i in raw_items:
                        qty = i['qty'] if i['qty'] > 0 else 1
                        calc_items.append({'id': '0', 'name': i['name'], 'amount': qty})

                    weight, bulk, detailed, unknowns = process_inventory(calc_items)

                    might = self.attributes.get("Might", 25)
                    try:
                        might_val = int(might)
                    except (ValueError, TypeError):
                        might_val = 25

                    max_cap = 1700 + (might_val * 20)
                    w_perc = (weight / max_cap) * 100 if max_cap > 0 else 0.0
                    b_perc = (bulk / max_cap) * 100 if max_cap > 0 else 0.0

                    self.update_inventory_ui(weight, bulk, w_perc, b_perc, max_cap, detailed)
            except Exception as e:
                print(f"[M59-INV] Exception polling inventory: {e}", flush=True)

    def update_inventory_ui(self, weight, bulk, w_perc, b_perc, max_cap, detailed_items):
        self.inv_weight = weight
        self.inv_bulk = bulk
        self.inv_w_perc = w_perc
        self.inv_b_perc = b_perc
        self.inv_max_cap = max_cap
        self.inventory_items = detailed_items

        sat_perc = max(w_perc, b_perc)
        self.inv_sat_perc = sat_perc

        def _get_encumbrance_color(p):
            if p >= 90:
                return "#ef4444"  # Red: Over-encumbered / critical
            elif p >= 75:
                return "#f97316"  # Orange: High load
            elif p >= 50:
                return "#eab308"  # Yellow: Moderate load
            else:
                return "#22c55e"  # Green: Safe / ample space

        sat_col = _get_encumbrance_color(sat_perc)
        w_col = _get_encumbrance_color(w_perc)
        b_col = _get_encumbrance_color(b_perc)

        # 1. Update Header Badges
        if hasattr(self, 'inv_sat_badge'):
            self.inv_sat_badge.setText(f"{sat_perc:.1f}% Saturation")
            self.inv_sat_badge.setStyleSheet(f"background-color: #0f172a; color: {sat_col}; font-size: 11px; font-weight: 800; padding: 4px 10px; border-radius: 8px; border: 1px solid {sat_col}44;")
            self.inv_weight_badge.setText(f"{int(weight):,} / {max_cap:,} W")
            self.inv_weight_badge.setStyleSheet(f"background-color: #0f172a; color: {w_col}; font-size: 11px; font-weight: 800; padding: 4px 10px; border-radius: 8px; border: 1px solid {w_col}44;")
            self.inv_bulk_badge.setText(f"{int(bulk):,} / {max_cap:,} B")
            self.inv_bulk_badge.setStyleSheet(f"background-color: #0f172a; color: {b_col}; font-size: 11px; font-weight: 800; padding: 4px 10px; border-radius: 8px; border: 1px solid {b_col}44;")
            self.inv_count_badge.setText(f"{len(detailed_items)} Items Carried")

        # 2. Update Graphs & Capacity Meters
        if hasattr(self, 'sat_val_lbl'):
            self.sat_val_lbl.setText(f"{sat_perc:.1f}%")
            self.sat_val_lbl.setStyleSheet(f"font-size: 11px; font-weight: 900; color: {sat_col};")
            self.set_progress_bar_color(self.sat_bar, sat_perc)
            dominant = "WEIGHT" if w_perc >= b_perc else "BULK"
            self.sat_sub_lbl.setText(f"Max Cap: {max_cap:,} | Dominant: {dominant}")

            self.weight_val_lbl.setText(f"{int(weight):,} / {max_cap:,} Stone ({w_perc:.1f}%)")
            self.weight_val_lbl.setStyleSheet(f"font-size: 11px; font-weight: 900; color: {w_col};")
            self.set_progress_bar_color(self.weight_bar, w_perc)
            self.weight_sub_lbl.setText(f"{max(0, max_cap - int(weight)):,} Stone Capacity Remaining")

            self.bulk_val_lbl.setText(f"{int(bulk):,} / {max_cap:,} Vol ({b_perc:.1f}%)")
            self.bulk_val_lbl.setStyleSheet(f"font-size: 11px; font-weight: 900; color: {b_col};")
            self.set_progress_bar_color(self.bulk_bar, b_perc)
            self.bulk_sub_lbl.setText(f"{max(0, max_cap - int(bulk)):,} Vol Capacity Remaining")

        # 3. Update Carried Items Table
        self.filter_inventory_table()

        # 4. Update Dock Panel Cards
        if hasattr(self, 'dock_inv_sat_lbl'):
            self.dock_inv_sat_lbl.setText(f"{sat_perc:.1f}%")
            self.dock_inv_sat_lbl.setStyleSheet(f"font-size: 10px; font-weight: 800; color: {sat_col}; background: transparent;")
            self.set_progress_bar_color(self.dock_inv_bar, sat_perc)
            self.dock_inv_weight_lbl.setText(f"W: {int(weight):,} / {max_cap:,}")
            self.dock_inv_bulk_lbl.setText(f"B: {int(bulk):,} / {max_cap:,}")
            self.dock_inv_count_lbl.setText(f"{len(detailed_items)} Carried Items")

    def filter_inventory_table(self):
        if not hasattr(self, 'inv_table'):
            return

        query = self.inv_search_input.text().lower().strip() if hasattr(self, 'inv_search_input') else ""
        self.inv_table.setRowCount(0)

        for item in self.inventory_items:
            name = item.get('name', 'Unknown')
            if query and query not in name.lower():
                continue

            qty = item.get('qty', 1)
            w = item.get('weight', 0)
            b = item.get('bulk', 0)
            qty_str = f"x{qty}" if qty > 1 else "1"

            row = self.inv_table.rowCount()
            self.inv_table.insertRow(row)

            self.inv_table.setItem(row, 0, QTableWidgetItem(name.title()))
            self.inv_table.setItem(row, 1, QTableWidgetItem(qty_str))
            self.inv_table.setItem(row, 2, QTableWidgetItem(str(w)))
            self.inv_table.setItem(row, 3, QTableWidgetItem(str(b)))
            self.inv_table.setItem(row, 4, QTableWidgetItem(str(w + b)))

    # ==================================================================
    # SECTION: SCHOOL PROGRESSION PAGE & METRICS
    # ==================================================================
    def build_progression_page(self):
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(10, 8, 10, 8)
        page_layout.setSpacing(6)

        # Header Frame (Compact)
        hdr_frame = QFrame()
        hdr_frame.setStyleSheet("background-color: #1e293b; border: 1px solid #334155; border-radius: 6px;")
        hdr_layout = QHBoxLayout(hdr_frame)
        hdr_layout.setContentsMargins(8, 4, 8, 4)
        hdr_layout.setSpacing(8)

        icon_lbl = QLabel("📜")
        icon_lbl.setStyleSheet("font-size: 16px;")
        hdr_layout.addWidget(icon_lbl)

        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        t_lbl = QLabel("School Progression Goals")
        t_lbl.setStyleSheet("font-size: 13px; font-weight: 800; color: #f8fafc;")
        s_lbl = QLabel("Calculates spell & skill thresholds, goal sums, and remaining % to advance.")
        s_lbl.setStyleSheet("font-size: 10px; color: #94a3b8;")
        title_box.addWidget(t_lbl)
        title_box.addWidget(s_lbl)
        hdr_layout.addLayout(title_box, 1)

        self.full_prog_active_badge = QLabel("0 Active Schools")
        self.full_prog_active_badge.setStyleSheet("background-color: #0c4a6e; color: #38bdf8; font-size: 10px; font-weight: 800; padding: 2px 6px; border-radius: 4px; border: 1px solid #0284c7;")
        hdr_layout.addWidget(self.full_prog_active_badge)

        self.full_prog_known_badge = QLabel("0 Abilities Known")
        self.full_prog_known_badge.setStyleSheet("background-color: #1e1b4b; color: #a78bfa; font-size: 10px; font-weight: 800; padding: 2px 6px; border-radius: 4px; border: 1px solid #6366f1;")
        hdr_layout.addWidget(self.full_prog_known_badge)

        self.full_prog_sync_btn = QPushButton("🔄 Sync (Tab Dance)")
        self.full_prog_sync_btn.setProperty("class", "WebBtnPrimary")
        self.full_prog_sync_btn.setStyleSheet("padding: 2px 8px; font-size: 10px; min-height: 22px;")
        self.full_prog_sync_btn.setToolTip("Triggers live memory scrape to read latest spell & skill knowledge from game client")
        self.full_prog_sync_btn.clicked.connect(self.trigger_manual_sync)
        hdr_layout.addWidget(self.full_prog_sync_btn)

        page_layout.addWidget(hdr_frame)

        # Controls & Filter Bar (Compact)
        ctrl_bar = QHBoxLayout()
        ctrl_bar.setContentsMargins(0, 0, 0, 0)
        ctrl_bar.setSpacing(6)

        self.prog_search_input = QLineEdit()
        self.prog_search_input.setPlaceholderText("Filter school or ability...")
        self.prog_search_input.setFixedWidth(220)
        self.prog_search_input.setStyleSheet("padding: 2px 6px; font-size: 11px; min-height: 22px;")
        self.prog_search_input.textChanged.connect(lambda: self.update_progression_ui())
        ctrl_bar.addWidget(self.prog_search_input)

        # Intellect Selector (Auto-populated from Character Tab Dance)
        int_lbl = QLabel("Intellect:")
        int_lbl.setStyleSheet("color: #94a3b8; font-weight: 700; font-size: 10px;")
        int_lbl.setToolTip("Character Intellect (automatically pulled from Character Stats / Tab Dance)")
        ctrl_bar.addWidget(int_lbl)

        self.prog_intellect_spin = QSpinBox()
        self.prog_intellect_spin.setRange(1, 50)
        self.prog_intellect_spin.setValue(25)
        self.prog_intellect_spin.setFixedWidth(50)
        self.prog_intellect_spin.setToolTip("Current Character Intellect (pulled from Tab Dance, or adjust manually)")
        self.prog_intellect_spin.setStyleSheet("""
            QSpinBox {
                background-color: #0f172a;
                color: #38bdf8;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 1px 3px;
                font-weight: bold;
                font-size: 10px;
                min-height: 20px;
            }
        """)
        self.prog_intellect_spin.valueChanged.connect(self.on_intellect_spin_changed)
        ctrl_bar.addWidget(self.prog_intellect_spin)

        self.prog_expand_btn = QPushButton("Expand All")
        self.prog_expand_btn.setProperty("class", "WebBtnSecondary")
        self.prog_expand_btn.setStyleSheet("padding: 2px 6px; font-size: 10px; min-height: 22px;")
        self.prog_expand_btn.clicked.connect(lambda: self.full_prog_tree.expandAll())
        ctrl_bar.addWidget(self.prog_expand_btn)

        self.prog_collapse_btn = QPushButton("Collapse All")
        self.prog_collapse_btn.setProperty("class", "WebBtnSecondary")
        self.prog_collapse_btn.setStyleSheet("padding: 2px 6px; font-size: 10px; min-height: 22px;")
        self.prog_collapse_btn.clicked.connect(lambda: self.full_prog_tree.collapseAll())
        ctrl_bar.addWidget(self.prog_collapse_btn)

        self.prog_clear_btn = QPushButton("🧹 Clear")
        self.prog_clear_btn.setProperty("class", "WebBtnSecondary")
        self.prog_clear_btn.setStyleSheet("padding: 2px 6px; font-size: 10px; min-height: 22px;")
        self.prog_clear_btn.setToolTip("Clear knowledge cache")
        self.prog_clear_btn.clicked.connect(self.clear_knowledge_cache)
        ctrl_bar.addWidget(self.prog_clear_btn)

        ctrl_bar.addStretch()
        page_layout.addLayout(ctrl_bar)

        # Main QTreeWidget for Full Progression Page
        self.full_prog_tree = QTreeWidget()
        self.full_prog_tree.setHeaderLabels(["SCHOOL / ABILITY", "LEVEL", "SUM", "GOAL", "NEEDED"])
        self.full_prog_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.full_prog_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.full_prog_tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.full_prog_tree.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.full_prog_tree.header().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.full_prog_tree.setStyleSheet("""
            QTreeWidget {
                background-color: #030712;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 6px;
                font-size: 11px;
            }
            QHeaderView::section {
                background-color: #0f172a;
                color: #94a3b8;
                font-size: 10px;
                font-weight: 800;
                padding: 3px 6px;
                border: 1px solid #1e293b;
            }
            QTreeWidget::item {
                padding: 2px 2px;
            }
            QTreeWidget::item:selected {
                background-color: #1e293b;
                color: #38bdf8;
            }
        """)
        page_layout.addWidget(self.full_prog_tree, 1)
        return page

    # ==================================================================
    # SECTION: REAGENT USAGE & SPELL STATISTICS PAGE
    # ==================================================================
    def get_herb_inventory_stock(self, herb_name):
        """Calculates live inventory quantity for a specific reagent/herb."""
        if not herb_name or herb_name == "--":
            return 0
        total = 0
        h_low = herb_name.lower().strip()
        for item in getattr(self, 'inventory_items', []):
            i_name = item.get('name', '').lower().strip()
            if h_low == i_name or h_low in i_name or i_name in h_low:
                total += item.get('qty', 1)
        return total

    def build_reagents_page(self):
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(10, 8, 10, 8)
        page_layout.setSpacing(6)

        # Header Frame
        hdr_frame = QFrame()
        hdr_frame.setStyleSheet("background-color: #1e293b; border: 1px solid #334155; border-radius: 6px;")
        hdr_layout = QHBoxLayout(hdr_frame)
        hdr_layout.setContentsMargins(8, 6, 8, 6)
        hdr_layout.setSpacing(8)

        icon_lbl = QLabel("🌿")
        icon_lbl.setStyleSheet("font-size: 18px;")
        hdr_layout.addWidget(icon_lbl)

        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        t_lbl = QLabel("Reagent & Herb Usage Analytics")
        t_lbl.setStyleSheet("font-size: 13px; font-weight: 800; color: #f8fafc;")
        s_lbl = QLabel("Track herb consumption trends, analyze daily burn rates, monitor inventory stock, and forecast runouts.")
        s_lbl.setStyleSheet("font-size: 10px; color: #94a3b8;")
        title_box.addWidget(t_lbl)
        title_box.addWidget(s_lbl)
        hdr_layout.addLayout(title_box, 1)

        # Control Row in Header: Timeframe & Herb Filter
        ctrl_box = QHBoxLayout()
        ctrl_box.setSpacing(6)

        tf_lbl = QLabel("Timeframe:")
        tf_lbl.setStyleSheet("font-size: 10px; font-weight: 700; color: #94a3b8;")
        ctrl_box.addWidget(tf_lbl)

        self.reagent_timeframe_combo = QComboBox()
        self.reagent_timeframe_combo.addItems(["Last 7 Days", "Today", "Last 30 Days", "All Time"])
        self.reagent_timeframe_combo.setStyleSheet("""
            QComboBox {
                background-color: #0f172a; color: #38bdf8; font-size: 10px; font-weight: 800;
                border: 1px solid #334155; border-radius: 4px; padding: 2px 6px; min-height: 22px;
            }
        """)
        self.reagent_timeframe_combo.currentIndexChanged.connect(lambda: self.update_reagents_ui())
        ctrl_box.addWidget(self.reagent_timeframe_combo)

        hf_lbl = QLabel("Focus Reagent:")
        hf_lbl.setStyleSheet("font-size: 10px; font-weight: 700; color: #94a3b8;")
        ctrl_box.addWidget(hf_lbl)

        self.reagent_focus_combo = QComboBox()
        self.reagent_focus_combo.addItems([
            "All Reagents", "Herbs", "Elderberry", "Mushroom", "Red Mushroom",
            "Wood Shaving", "Solstice Stem", "Emeralds", "Orc Tooth", "Dragon Blood", "Spider Web"
        ])
        self.reagent_focus_combo.setStyleSheet("""
            QComboBox {
                background-color: #0f172a; color: #34d399; font-size: 10px; font-weight: 800;
                border: 1px solid #334155; border-radius: 4px; padding: 2px 6px; min-height: 22px;
            }
        """)
        self.reagent_focus_combo.currentIndexChanged.connect(lambda: self.update_reagents_ui())
        ctrl_box.addWidget(self.reagent_focus_combo)

        hdr_layout.addLayout(ctrl_box)
        page_layout.addWidget(hdr_frame)

        # Top Metric Cards (5 Cards)
        reagent_stats_box = QHBoxLayout()
        reagent_stats_box.setSpacing(6)

        def make_stat_metric(title, val_lbl_attr, sub_lbl_attr, bg_col, txt_col):
            f = QFrame()
            f.setStyleSheet(f"background-color: {bg_col}; border: 1px solid #334155; border-radius: 4px;")
            l = QVBoxLayout(f)
            l.setContentsMargins(6, 4, 6, 4)
            l.setSpacing(1)
            t = QLabel(title)
            t.setStyleSheet("font-size: 8px; font-weight: 800; color: #94a3b8; letter-spacing: 0.3px;")
            v = QLabel("0")
            v.setStyleSheet(f"font-size: 12px; font-weight: 900; color: {txt_col};")
            s = QLabel("--")
            s.setStyleSheet("font-size: 8px; font-weight: 700; color: #94a3b8;")
            setattr(self, val_lbl_attr, v)
            if sub_lbl_attr:
                setattr(self, sub_lbl_attr, s)
            l.addWidget(t)
            l.addWidget(v)
            l.addWidget(s)
            return f

        reagent_stats_box.addWidget(make_stat_metric("MOST USED REAGENT", "reagent_top_used_lbl", "reagent_top_used_sub", "#1e1b4b", "#c084fc"), 1)
        reagent_stats_box.addWidget(make_stat_metric("TOTAL SPELLS CAST", "reagent_total_casts_lbl", "reagent_total_casts_sub", "#0b1329", "#38bdf8"), 1)
        reagent_stats_box.addWidget(make_stat_metric("TOTAL REAGENTS BURNED", "reagent_total_used_lbl", "reagent_total_used_sub", "#064e3b", "#34d399"), 1)
        reagent_stats_box.addWidget(make_stat_metric("DAILY BURN RATE", "reagent_burn_rate_lbl", "reagent_burn_rate_sub", "#451a03", "#f59e0b"), 1)
        reagent_stats_box.addWidget(make_stat_metric("STOCK RUNOUT FORECAST", "reagent_stock_forecast_lbl", "reagent_stock_forecast_sub", "#701a75", "#f472b6"), 1)

        page_layout.addLayout(reagent_stats_box)

        # Usage Chart Section
        chart_frame = QFrame()
        chart_frame.setStyleSheet("background-color: #0f172a; border: 1px solid #334155; border-radius: 6px;")
        chart_layout = QVBoxLayout(chart_frame)
        chart_layout.setContentsMargins(8, 6, 8, 8)
        chart_layout.setSpacing(4)

        c_hdr = QHBoxLayout()
        c_title = QLabel("DAILY & WEEKLY REAGENT CONSUMPTION TRENDS")
        c_title.setStyleSheet("font-size: 10px; font-weight: 800; color: #38bdf8; letter-spacing: 0.5px;")
        c_hdr.addWidget(c_title)
        c_hdr.addStretch()

        self.chart_type_badge = QLabel("📊 Bar Chart")
        self.chart_type_badge.setStyleSheet("background-color: #1e293b; color: #34d399; font-size: 9px; font-weight: 800; padding: 2px 6px; border-radius: 4px; border: 1px solid #334155;")
        c_hdr.addWidget(self.chart_type_badge)
        chart_layout.addLayout(c_hdr)

        self.reagent_chart_widget = ReagentTrendChartWidget()
        chart_layout.addWidget(self.reagent_chart_widget)
        page_layout.addWidget(chart_frame)

        # Two Column Section: Top Herbs & Spell Matrix
        grid_split = QHBoxLayout()
        grid_split.setSpacing(6)

        # Left Column: Most Used Reagents with Inventory & Runout Risk
        left_col = QVBoxLayout()
        left_col.setSpacing(4)
        l_hdr = QHBoxLayout()
        l_hdr.setSpacing(4)
        l_title = QLabel("TOP REAGENTS & INVENTORY STOCK RISK")
        l_title.setStyleSheet("font-size: 10px; font-weight: 800; color: #38bdf8;")
        l_hdr.addWidget(l_title)
        l_hdr.addStretch()

        self.reagent_sort_combo = QComboBox()
        self.reagent_sort_combo.addItems(["Sort by Count (High to Low)", "Sort by Runout Risk", "Alphabetical"])
        self.reagent_sort_combo.setStyleSheet("background-color: #0f172a; color: #94a3b8; font-size: 9px; border: 1px solid #334155; border-radius: 4px; padding: 1px 4px; min-height: 20px;")
        self.reagent_sort_combo.currentIndexChanged.connect(lambda: self.update_reagents_ui())
        l_hdr.addWidget(self.reagent_sort_combo)
        left_col.addLayout(l_hdr)

        self.reagents_tree = QTreeWidget()
        self.reagents_tree.setHeaderLabels(["RANK & REAGENT", "TOTAL BURN", "DAILY AVG", "INVENTORY STOCK", "RUNOUT FORECAST"])
        self.reagents_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.reagents_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.reagents_tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.reagents_tree.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.reagents_tree.header().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.reagents_tree.setStyleSheet("""
            QTreeWidget {
                background-color: #030712;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 6px;
                font-size: 10px;
            }
            QHeaderView::section {
                background-color: #0f172a;
                color: #94a3b8;
                font-size: 9px;
                font-weight: 800;
                padding: 3px 4px;
                border: 1px solid #1e293b;
            }
            QTreeWidget::item {
                padding: 2px 1px;
            }
        """)
        left_col.addWidget(self.reagents_tree, 1)
        grid_split.addLayout(left_col, 1)

        # Right Column: Spells Cast & Reagent Matrix
        right_col = QVBoxLayout()
        right_col.setSpacing(4)
        r_hdr = QHBoxLayout()
        r_hdr.setSpacing(4)
        r_title = QLabel("SPELL REAGENT CONSUMPTION MATRIX")
        r_title.setStyleSheet("font-size: 10px; font-weight: 800; color: #a78bfa;")
        r_hdr.addWidget(r_title)
        r_hdr.addStretch()

        self.spell_cast_search = QLineEdit()
        self.spell_cast_search.setPlaceholderText("Filter spell stats...")
        self.spell_cast_search.setFixedWidth(140)
        self.spell_cast_search.setStyleSheet("padding: 1px 4px; font-size: 10px; min-height: 20px;")
        self.spell_cast_search.textChanged.connect(lambda: self.update_reagents_ui())
        r_hdr.addWidget(self.spell_cast_search)
        right_col.addLayout(r_hdr)

        self.spells_cast_tree = QTreeWidget()
        self.spells_cast_tree.setHeaderLabels(["SPELL / REAGENT", "CASTS", "HERBS SPENT", "PER CAST REQ"])
        self.spells_cast_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.spells_cast_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.spells_cast_tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.spells_cast_tree.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.spells_cast_tree.setStyleSheet("""
            QTreeWidget {
                background-color: #030712;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 6px;
                font-size: 10px;
            }
            QHeaderView::section {
                background-color: #0f172a;
                color: #94a3b8;
                font-size: 9px;
                font-weight: 800;
                padding: 3px 4px;
                border: 1px solid #1e293b;
            }
            QTreeWidget::item {
                padding: 2px 1px;
            }
        """)
        right_col.addWidget(self.spells_cast_tree, 1)
        grid_split.addLayout(right_col, 1)

        page_layout.addLayout(grid_split, 1)

        # Bottom Section: Recent Reagent Cast Log
        log_frame = QFrame()
        log_frame.setStyleSheet("background-color: #0f172a; border: 1px solid #334155; border-radius: 6px;")
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(8, 4, 8, 6)
        log_layout.setSpacing(3)

        log_hdr = QLabel("📜 RECENT REAGENT CAST LOG")
        log_hdr.setStyleSheet("font-size: 9px; font-weight: 800; color: #38bdf8;")
        log_layout.addWidget(log_hdr)

        self.reagent_history_tree = QTreeWidget()
        self.reagent_history_tree.setHeaderLabels(["DATE & TIME", "SPELL CAST", "SCHOOL", "MANA", "REAGENTS DEDUCTED"])
        self.reagent_history_tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.reagent_history_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.reagent_history_tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.reagent_history_tree.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.reagent_history_tree.header().setSectionResizeMode(4, QHeaderView.Stretch)
        self.reagent_history_tree.setFixedHeight(120)
        self.reagent_history_tree.setStyleSheet("""
            QTreeWidget {
                background-color: #030712;
                color: #f8fafc;
                border: 1px solid #1e293b;
                border-radius: 4px;
                font-size: 10px;
            }
            QHeaderView::section {
                background-color: #0f172a;
                color: #94a3b8;
                font-size: 8px;
                font-weight: 800;
                padding: 2px 4px;
                border: 1px solid #1e293b;
            }
            QTreeWidget::item {
                padding: 1px 1px;
            }
        """)
        log_layout.addWidget(self.reagent_history_tree)
        page_layout.addWidget(log_frame)

        return page

    def save_knowledge_cache(self):
        """Persists knowledge cache to JSON settings file."""
        try:
            os.makedirs("settings", exist_ok=True)
            if self.char_name and self.char_name != "--":
                sn = get_safe_name(self.char_name)
                with open(f"settings/{sn}_knowledge.json", "w", encoding="utf-8") as f:
                    json.dump(self.knowledge_cache, f, indent=2)
            with open("settings/last_knowledge.json", "w", encoding="utf-8") as f:
                json.dump(self.knowledge_cache, f, indent=2)
        except Exception as e:
            print(f"[M59-PROG] Error saving knowledge cache: {e}", flush=True)

    def load_knowledge_cache(self, char_name=None):
        """Loads persistent knowledge cache from JSON settings file or defaults to sample."""
        loaded = False
        name_to_check = char_name or (self.char_name if self.char_name != "--" else None)
        if name_to_check:
            sn = get_safe_name(name_to_check)
            p = f"settings/{sn}_knowledge.json"
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        d = json.load(f)
                        if isinstance(d, dict) and d:
                            self.knowledge_cache = d
                            loaded = True
                except Exception as e:
                    print(f"[M59-PROG] Error loading character knowledge: {e}", flush=True)

        if not loaded and os.path.exists("settings/last_knowledge.json"):
            try:
                with open("settings/last_knowledge.json", "r", encoding="utf-8") as f:
                    d = json.load(f)
                    if isinstance(d, dict) and d:
                        self.knowledge_cache = d
                        loaded = True
            except Exception as e:
                print(f"[M59-PROG] Error loading last knowledge: {e}", flush=True)

        self.update_progression_ui()

    def clear_knowledge_cache(self):
        """Clears knowledge cache."""
        self.knowledge_cache = {}
        self.save_knowledge_cache()
        self.update_progression_ui()

    def save_attributes_cache(self):
        """Persists character attributes (Might, Intellect, etc.) to JSON settings file."""
        try:
            os.makedirs("settings", exist_ok=True)
            if self.char_name and self.char_name != "--":
                sn = get_safe_name(self.char_name)
                with open(f"settings/{sn}_attributes.json", "w", encoding="utf-8") as f:
                    json.dump(self.attributes, f, indent=2)
            with open("settings/last_attributes.json", "w", encoding="utf-8") as f:
                json.dump(self.attributes, f, indent=2)
        except Exception as e:
            print(f"[M59-ATTR] Error saving attributes cache: {e}", flush=True)

    def load_attributes_cache(self, char_name=None):
        """Loads persistent character attributes from JSON settings file and updates GUI labels."""
        loaded = False
        name_to_check = char_name or (self.char_name if self.char_name != "--" else None)
        if name_to_check:
            sn = get_safe_name(name_to_check)
            p = f"settings/{sn}_attributes.json"
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        d = json.load(f)
                        if isinstance(d, dict) and d:
                            self.attributes.update(d)
                            loaded = True
                except Exception as e:
                    print(f"[M59-ATTR] Error loading character attributes: {e}", flush=True)

        if not loaded and os.path.exists("settings/last_attributes.json"):
            try:
                with open("settings/last_attributes.json", "r", encoding="utf-8") as f:
                    d = json.load(f)
                    if isinstance(d, dict) and d:
                        self.attributes.update(d)
                        loaded = True
            except Exception as e:
                print(f"[M59-ATTR] Error loading last attributes: {e}", flush=True)

        if loaded:
            for k, val in self.attributes.items():
                if k in getattr(self, 'attr_labels', {}):
                    self.attr_labels[k].setText(str(val))
            if hasattr(self, 'prog_intellect_spin') and self.prog_intellect_spin and self.attributes.get("Intellect") not in (None, "--", ""):
                try:
                    ival = int(self.attributes["Intellect"])
                    self.prog_intellect_spin.blockSignals(True)
                    self.prog_intellect_spin.setValue(ival)
                    self.prog_intellect_spin.blockSignals(False)
                except Exception:
                    pass

    def on_intellect_spin_changed(self, val):
        """Called when user manually modifies the progression intellect spinbox."""
        self.attributes["Intellect"] = val
        if "Intellect" in getattr(self, 'attr_labels', {}):
            self.attr_labels["Intellect"].setText(str(val))
        self.save_attributes_cache()
        self.update_progression_ui()

    def update_progression_ui(self, knowledge=None):
        """Calculates and refreshes the progression tree widgets using the current character's intellect pulled from tab dance."""
        if knowledge is not None:
            self.knowledge_cache = knowledge
            self.save_knowledge_cache()

        if not hasattr(self, 'calculator') or not self.calculator:
            self.calculator = SchoolCalculator()

        # Determine Intellect: prioritize current character's intellect pulled from tab dance / character attributes
        intellect = 25
        char_int = self.attributes.get("Intellect") if hasattr(self, 'attributes') else None
        if char_int not in (None, "--", ""):
            try:
                intellect = int(char_int)
                if hasattr(self, 'prog_intellect_spin') and self.prog_intellect_spin:
                    if self.prog_intellect_spin.value() != intellect:
                        self.prog_intellect_spin.blockSignals(True)
                        self.prog_intellect_spin.setValue(intellect)
                        self.prog_intellect_spin.blockSignals(False)
            except Exception:
                intellect = 25
        elif hasattr(self, 'prog_intellect_spin') and self.prog_intellect_spin:
            intellect = self.prog_intellect_spin.value()

        try:
            results_list = self.calculator.calculate_progression(self.knowledge_cache, intellect)
        except Exception as e:
            print(f"[M59-PROG] Error calculating progression: {e}", flush=True)
            results_list = []

        filter_txt = self.prog_search_input.text().lower().strip() if hasattr(self, 'prog_search_input') else ""

        trees_to_update = []
        if hasattr(self, 'full_prog_tree') and self.full_prog_tree:
            trees_to_update.append(self.full_prog_tree)
        if hasattr(self, 'dash_prog_tree') and self.dash_prog_tree:
            trees_to_update.append(self.dash_prog_tree)

        active_count = len(results_list) if isinstance(results_list, list) else 0
        total_known_abilities = len(self.knowledge_cache)

        for tree in trees_to_update:
            # Capture currently expanded schools
            expanded_schools = set()
            for idx in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(idx)
                if item and item.isExpanded():
                    # Extract plain school name from text
                    raw_text = item.text(0).strip()
                    for s_name in self.calculator.schools.keys():
                        if s_name in raw_text:
                            expanded_schools.add(s_name)

            tree.clear()

            for r in results_list:
                name = r['name']
                sd = self.calculator.schools.get(name, {})

                # Filter text check
                school_matches = (not filter_txt) or (filter_txt in name.lower())
                spells_match = False
                if filter_txt:
                    for l in range(1, 7):
                        for s in sd.get(f"Level_{l}", []):
                            if s.lower() in self.knowledge_cache and filter_txt in s.lower():
                                spells_match = True
                                break

                if filter_txt and not school_matches and not spells_match:
                    continue

                # Top-level school item matching original dashboard format
                school_item = QTreeWidgetItem(tree)
                school_item.setText(0, name)
                school_item.setFont(0, QFont("Segoe UI", 10, QFont.Bold))
                school_item.setForeground(0, QColor("#38bdf8"))

                if r.get('mastered'):
                    school_item.setText(1, "Level 6")
                    school_item.setText(2, "MASTERED")
                    school_item.setText(3, "---")
                    school_item.setText(4, "---")
                    school_item.setForeground(2, QColor("#c084fc"))
                    school_item.setForeground(4, QColor("#c084fc"))
                elif r.get('is_impossible'):
                    max_cap = r.get('max_possible', 297)
                    school_item.setText(1, f"Level {r['current_lvl']}")
                    school_item.setText(2, f"{r['current_sum']}%")
                    school_item.setText(3, f"{r['target_sum']}% (Cap: {max_cap}%)")
                    school_item.setText(4, "IMPOSSIBLE")
                    school_item.setForeground(3, QColor("#ef4444"))
                    school_item.setForeground(4, QColor("#ef4444"))
                    school_item.setToolTip(4, (
                        f"Goal of {r['target_sum']}% exceeds the maximum achievable sum of {max_cap}%\n"
                        f"for this circle. You must raise Intellect or drop other schools to learn this level."
                    ))
                elif r.get('needed', 0) == 0:
                    school_item.setText(1, f"Level {r['current_lvl']}")
                    school_item.setText(2, f"{r['current_sum']}%")
                    school_item.setText(3, f"{r['target_sum']}%")
                    school_item.setText(4, "YOU QUALIFY!")
                    school_item.setForeground(4, QColor("#10b981"))
                else:
                    school_item.setText(1, f"Level {r['current_lvl']}")
                    school_item.setText(2, f"{r['current_sum']}%")
                    school_item.setText(3, f"{r['target_sum']}%")
                    school_item.setText(4, f"{r['needed']}%")
                    school_item.setForeground(4, QColor("#f59e0b"))

                # Add only known abilities in this school, matching original format:
                # text=f"  {s}", values=(f"L{l}", f"{knowledge_cache[s.lower()]}%", "", "")
                for l in range(1, 7):
                    lk = f"Level_{l}"
                    if lk in sd:
                        for s in sd[lk]:
                            s_lower = s.lower()
                            if s_lower in self.knowledge_cache:
                                if filter_txt and (filter_txt not in s_lower) and not school_matches:
                                    continue
                                spell_pct = self.knowledge_cache[s_lower]
                                spell_item = QTreeWidgetItem(school_item)
                                spell_item.setText(0, f"  {s}")
                                spell_item.setText(1, f"L{l}")
                                spell_item.setText(2, f"{spell_pct}%")
                                spell_item.setText(3, "")
                                spell_item.setText(4, "")
                                spell_item.setForeground(0, QColor("#f8fafc"))
                                spell_item.setForeground(1, QColor("#94a3b8"))
                                spell_item.setForeground(2, QColor("#cbd5e1"))

                # Expand by default or if previously expanded
                if not expanded_schools or name in expanded_schools or filter_txt:
                    school_item.setExpanded(True)

        summary_text = f"{active_count} Active Schools"
        if hasattr(self, 'dash_prog_summary_badge'):
            self.dash_prog_summary_badge.setText(summary_text)
        if hasattr(self, 'full_prog_active_badge'):
            self.full_prog_active_badge.setText(summary_text)
        if hasattr(self, 'full_prog_known_badge'):
            self.full_prog_known_badge.setText(f"{total_known_abilities} Known Abilities")

        # Also refresh reagents statistics if available
        if hasattr(self, 'update_reagents_ui'):
            self.update_reagents_ui()

    def update_reagents_ui(self):
        """Refreshes the Reagents Usage & Spell Casting statistics dashboard."""
        if not hasattr(self, 'spell_manager') or not self.spell_manager:
            return
        if not hasattr(self, 'reagents_tree') or not hasattr(self, 'spells_cast_tree'):
            return

        from datetime import datetime, timedelta

        stats = getattr(self.spell_manager, 'reagent_stats', {})
        spells_cast = stats.get("spells_cast", {})
        reagents_used = stats.get("reagents_used", {})
        breakdown = stats.get("spell_reagent_breakdown", {})
        daily_data = stats.get("daily_usage", {})
        history_data = stats.get("history", [])

        today_str = datetime.now().strftime("%Y-%m-%d")

        timeframe = self.reagent_timeframe_combo.currentText() if hasattr(self, 'reagent_timeframe_combo') else "Last 7 Days"
        reagent_filter = self.reagent_focus_combo.currentText() if hasattr(self, 'reagent_focus_combo') else "All Reagents"

        total_casts = stats.get("total_casts", sum(spells_cast.values()))
        total_reagents = stats.get("total_reagents", sum(reagents_used.values()))

        today_casts = daily_data.get(today_str, {}).get("casts", 0)
        today_reagents = daily_data.get(today_str, {}).get("total_reagents", 0)

        # 1. Update Metric Cards
        if hasattr(self, 'reagent_total_casts_lbl'):
            self.reagent_total_casts_lbl.setText(f"{total_casts:,}")
            if hasattr(self, 'reagent_total_casts_sub'):
                self.reagent_total_casts_sub.setText(f"+{today_casts:,} today")

        if hasattr(self, 'reagent_total_used_lbl'):
            self.reagent_total_used_lbl.setText(f"{total_reagents:,}")
            if hasattr(self, 'reagent_total_used_sub'):
                self.reagent_total_used_sub.setText(f"+{today_reagents:,} today")

        # Top Reagent
        top_reagent = "None"
        top_reagent_name = ""
        top_reagent_count = 0
        if reagents_used:
            best_r = max(reagents_used.items(), key=lambda x: x[1])
            top_reagent_name = best_r[0]
            top_reagent_count = best_r[1]
            pct = (top_reagent_count / total_reagents * 100.0) if total_reagents > 0 else 0.0
            top_reagent = f"{top_reagent_name}"
            if hasattr(self, 'reagent_top_used_sub'):
                self.reagent_top_used_sub.setText(f"{top_reagent_count:,} used ({pct:.1f}%)")
        if hasattr(self, 'reagent_top_used_lbl'):
            self.reagent_top_used_lbl.setText(top_reagent or "None")

        # Daily Burn Rate Calculation
        active_days = max(1, len(daily_data))
        avg_daily_burn = total_reagents / float(active_days)
        if hasattr(self, 'reagent_burn_rate_lbl'):
            self.reagent_burn_rate_lbl.setText(f"~{avg_daily_burn:.1f} / day")
            if hasattr(self, 'reagent_burn_rate_sub'):
                self.reagent_burn_rate_sub.setText(f"Across {active_days} active days")

        # Top Herb Stock Runout Forecast
        top_stock = self.get_herb_inventory_stock(top_reagent_name)
        top_daily_burn = (top_reagent_count / float(active_days)) if active_days > 0 else 1.0
        days_left = (top_stock / top_daily_burn) if top_daily_burn > 0 else 99.0

        if hasattr(self, 'reagent_stock_forecast_lbl'):
            if top_stock > 0:
                self.reagent_stock_forecast_lbl.setText(f"~{days_left:.1f} Days Left")
                if hasattr(self, 'reagent_stock_forecast_sub'):
                    self.reagent_stock_forecast_sub.setText(f"Stock: {top_stock:,} {top_reagent_name}")
            else:
                self.reagent_stock_forecast_lbl.setText("Stock Empty")
                if hasattr(self, 'reagent_stock_forecast_sub'):
                    self.reagent_stock_forecast_sub.setText(f"0 {top_reagent_name} in bag")

        # 2. Update Usage Chart Widget
        if hasattr(self, 'reagent_chart_widget'):
            self.reagent_chart_widget.set_data(daily_data, history_data, timeframe, reagent_filter)

        # 3. Populate Left Table: Top Reagents & Runout Risk
        self.reagents_tree.clear()
        sort_mode = self.reagent_sort_combo.currentIndex() if hasattr(self, 'reagent_sort_combo') else 0

        r_items = list(reagents_used.items())
        if sort_mode == 0:
            r_items.sort(key=lambda x: x[1], reverse=True)
        elif sort_mode == 1:
            def _calc_risk(x):
                stk = self.get_herb_inventory_stock(x[0])
                burn = x[1] / float(active_days) if active_days > 0 else 1.0
                return stk / burn if burn > 0 else 999.0
            r_items.sort(key=_calc_risk)
        else:
            r_items.sort(key=lambda x: x[0].lower())

        for rank, (r_name, count) in enumerate(r_items, 1):
            if reagent_filter != "All Reagents" and reagent_filter.lower() not in r_name.lower():
                continue

            pct = (count / total_reagents * 100.0) if total_reagents > 0 else 0.0
            d_burn = count / float(active_days) if active_days > 0 else 0.0
            stk = self.get_herb_inventory_stock(r_name)
            r_days = (stk / d_burn) if d_burn > 0 else 99.0

            if stk == 0:
                runout_str = "⚠️ Out of Stock"
                status_color = "#ef4444"
            elif r_days < 3.0:
                runout_str = f"🔴 Low (~{r_days:.1f}d)"
                status_color = "#f97316"
            elif r_days < 7.0:
                runout_str = f"🟡 Moderate (~{r_days:.1f}d)"
                status_color = "#eab308"
            else:
                runout_str = f"🟢 Ample (~{r_days:.0f}d)"
                status_color = "#34d399"

            stk_str = f"{stk:,}" if stk > 0 else "0"

            item = QTreeWidgetItem(self.reagents_tree)
            item.setText(0, f"#{rank}  {r_name}")
            item.setText(1, f"{count:,} ({pct:.1f}%)")
            item.setText(2, f"~{d_burn:.1f}/day")
            item.setText(3, stk_str)
            item.setText(4, runout_str)

            item.setForeground(0, QColor("#38bdf8"))
            item.setFont(0, QFont("Segoe UI", 9, QFont.Bold))
            item.setForeground(1, QColor("#f8fafc"))
            item.setForeground(2, QColor("#cbd5e1"))
            item.setForeground(3, QColor("#34d399") if stk > 0 else QColor("#ef4444"))
            item.setForeground(4, QColor(status_color))

        # 4. Populate Right Table: Spells Cast & Reagent Matrix
        self.spells_cast_tree.clear()
        filter_query = self.spell_cast_search.text().lower().strip() if hasattr(self, 'spell_cast_search') else ""

        s_items = sorted(spells_cast.items(), key=lambda x: x[1], reverse=True)
        for s_name, cast_c in s_items:
            if filter_query and filter_query not in s_name.lower():
                continue

            info = self.spell_manager.find_spell_info(s_name) if hasattr(self.spell_manager, 'find_spell_info') else None
            req_dict = info.get("reagents", {}) if info else {}
            req_str = ", ".join([f"{c} {r}" for r, c in req_dict.items()]) if req_dict else "None"

            s_break = breakdown.get(s_name, {})
            tot_spell_r = sum(s_break.values())

            spell_node = QTreeWidgetItem(self.spells_cast_tree)
            spell_node.setText(0, f"  {s_name}")
            spell_node.setText(1, f"{cast_c:,} casts")
            spell_node.setText(2, f"{tot_spell_r:,} total")
            spell_node.setText(3, f"Req: {req_str}")

            spell_node.setForeground(0, QColor("#f472b6"))
            spell_node.setFont(0, QFont("Segoe UI", 9, QFont.Bold))
            spell_node.setForeground(1, QColor("#f8fafc"))
            spell_node.setForeground(2, QColor("#34d399"))
            spell_node.setForeground(3, QColor("#94a3b8"))

            if s_break:
                for r_sub, r_cnt in s_break.items():
                    sub_item = QTreeWidgetItem(spell_node)
                    sub_item.setText(0, f"    • {r_sub}")
                    sub_item.setText(1, "")
                    sub_item.setText(2, f"{r_cnt:,} consumed")
                    per_cast_amt = req_dict.get(r_sub, 1)
                    sub_item.setText(3, f"{per_cast_amt}x per cast")
                    sub_item.setForeground(0, QColor("#cbd5e1"))
                    sub_item.setForeground(2, QColor("#94a3b8"))
                    sub_item.setForeground(3, QColor("#64748b"))

            spell_node.setExpanded(True)

        # 5. Populate Bottom Log Table: Recent Cast Log
        if hasattr(self, 'reagent_history_tree'):
            self.reagent_history_tree.clear()
            for h_item in reversed(history_data[-50:]):
                h_date = h_item.get("date", today_str)
                ts = h_item.get("ts", "")
                dt_str = f"{h_date} {ts}".strip()
                s_name = h_item.get("spell", "Unknown")
                school = h_item.get("school", "Unknown")
                mana = h_item.get("mana", 0)
                reqs = h_item.get("reagents", {})
                req_desc = ", ".join([f"{c} {r}" for r, c in reqs.items()]) if reqs else "--"

                log_node = QTreeWidgetItem(self.reagent_history_tree)
                log_node.setText(0, dt_str)
                log_node.setText(1, s_name)
                log_node.setText(2, school)
                log_node.setText(3, f"{mana} MP" if mana else "--")
                log_node.setText(4, req_desc)

                log_node.setForeground(0, QColor("#94a3b8"))
                log_node.setForeground(1, QColor("#f8fafc"))
                log_node.setForeground(2, QColor("#38bdf8"))
                log_node.setForeground(3, QColor("#c084fc"))
                log_node.setForeground(4, QColor("#34d399"))

    # ==================================================================
    # SECTION 4: SETTINGS & PREFERENCES (CATEGORIZED TABS)
    # ==================================================================
    def build_settings_page(self):
        page = QWidget()
        main_layout = QVBoxLayout(page)
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(14)

        # Header Title Bar
        hdr_card = QFrame()
        hdr_card.setProperty("class", "WebCard")
        hc_layout = QHBoxLayout(hdr_card)
        hc_layout.setContentsMargins(16, 12, 16, 12)

        title_box = QVBoxLayout()
        t_lbl = QLabel("⚙️ Companion Settings & Preferences")
        t_lbl.setStyleSheet("font-size: 16px; font-weight: 800; color: #f8fafc;")
        s_lbl = QLabel("Manage interface typography, sound alerts, debugging options, and software updates.")
        s_lbl.setStyleSheet("font-size: 12px; color: #64748b;")
        title_box.addWidget(t_lbl)
        title_box.addWidget(s_lbl)
        hc_layout.addLayout(title_box)
        hc_layout.addStretch()

        main_layout.addWidget(hdr_card)

        # Settings Categorized Tabs
        tab_widget = QTabWidget()
        tab_widget.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #1e293b;
                background-color: #090d16;
                border-radius: 8px;
                padding: 10px;
            }
            QTabBar::tab {
                background: #0f172a;
                color: #94a3b8;
                border: 1px solid #1e293b;
                border-bottom: none;
                padding: 9px 18px;
                margin-right: 4px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: 700;
                font-size: 12px;
            }
            QTabBar::tab:selected {
                background: #1e293b;
                color: #38bdf8;
                border-color: #38bdf8;
                border-bottom: 2px solid #38bdf8;
            }
            QTabBar::tab:hover:!selected {
                background: #131d31;
                color: #f8fafc;
            }
        """)

        # --------------------------------------------------------------
        # TAB 1: TYPOGRAPHY & SIZING (No Sliders, +/- Buttons Only)
        # --------------------------------------------------------------
        typo_scroll = QScrollArea()
        typo_scroll.setWidgetResizable(True)
        typo_scroll.setFrameShape(QFrame.NoFrame)
        typo_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        typo_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        typo_page = QWidget()
        typo_layout = QVBoxLayout(typo_page)
        typo_layout.setContentsMargins(12, 12, 12, 12)
        typo_layout.setSpacing(14)

        # Typo top actions
        typo_top_row = QHBoxLayout()
        typo_info = QLabel("Adjust UI font sizing with incremental step buttons. Sizing changes apply instantly.")
        typo_info.setStyleSheet("font-size: 12px; color: #94a3b8;")
        typo_top_row.addWidget(typo_info)
        typo_top_row.addStretch()

        reset_btn = QPushButton("↺ Reset All Sizes")
        reset_btn.setProperty("class", "WebBtnSecondary")
        reset_btn.setToolTip("Reset all font sizes to default (13px)")
        reset_btn.clicked.connect(self.reset_font_settings)
        typo_top_row.addWidget(reset_btn)
        typo_layout.addLayout(typo_top_row)

        # Helper for Font Step Control Card
        def make_font_step_card(group_key, title_text, icon_str, desc_text, min_fs, max_fs, preview_type):
            card = QFrame()
            card.setProperty("class", "WebCard")
            c_layout = QVBoxLayout(card)
            c_layout.setContentsMargins(14, 14, 14, 14)
            c_layout.setSpacing(10)

            # Title & Stepper Row
            tr = QHBoxLayout()
            g_title = QLabel(f"{icon_str}  {title_text}")
            g_title.setStyleSheet("font-size: 14px; font-weight: 800; color: #e2e8f0;")
            tr.addWidget(g_title)
            tr.addStretch()

            current_val = self.font_settings.get(group_key, 13)

            # Minus Button
            btn_minus = QPushButton("－")
            btn_minus.setFixedSize(36, 30)
            btn_minus.setCursor(Qt.PointingHandCursor)
            btn_minus.setStyleSheet("""
                QPushButton {
                    background: #1e293b; color: #f8fafc; font-size: 15px; font-weight: 900;
                    border: 1px solid #334155; border-radius: 6px;
                }
                QPushButton:hover {
                    background: #334155; border-color: #38bdf8; color: #38bdf8;
                }
                QPushButton:pressed {
                    background: #0f172a;
                }
            """)

            # Value Label Badge
            val_badge = QLabel(f"{current_val} px")
            val_badge.setFixedWidth(72)
            val_badge.setAlignment(Qt.AlignCenter)
            val_badge.setStyleSheet("""
                QLabel {
                    background: #030712; color: #38bdf8; font-size: 13px; font-weight: 800;
                    border: 1px solid #334155; border-radius: 6px; padding: 4px;
                }
            """)

            # Plus Button
            btn_plus = QPushButton("＋")
            btn_plus.setFixedSize(36, 30)
            btn_plus.setCursor(Qt.PointingHandCursor)
            btn_plus.setStyleSheet("""
                QPushButton {
                    background: #1e293b; color: #f8fafc; font-size: 15px; font-weight: 900;
                    border: 1px solid #334155; border-radius: 6px;
                }
                QPushButton:hover {
                    background: #334155; border-color: #38bdf8; color: #38bdf8;
                }
                QPushButton:pressed {
                    background: #0f172a;
                }
            """)

            tr.addWidget(QLabel("Size:"))
            tr.addWidget(btn_minus)
            tr.addWidget(val_badge)
            tr.addWidget(btn_plus)
            c_layout.addLayout(tr)

            # Description
            d_lbl = QLabel(desc_text)
            d_lbl.setStyleSheet("font-size: 11px; color: #94a3b8;")
            c_layout.addWidget(d_lbl)

            # Live Preview Box
            prev_box = QFrame()
            prev_box.setStyleSheet("background-color: #030712; border: 1px solid #1e293b; border-radius: 6px; padding: 8px;")
            pb_layout = QVBoxLayout(prev_box)
            pb_layout.setContentsMargins(8, 6, 8, 6)

            prev_lbl = QLabel()
            if preview_type == "player_list":
                prev_lbl.setText("• Zaphod (CREATOR)   • Elric (MURDERER)   • Balthazar (OUTLAW)   • Kaelen (INNOCENT)")
                prev_lbl.setStyleSheet(f"font-size: {current_val}px; font-weight: 700; color: #e0e0e0;")
            elif preview_type == "chat_logger":
                prev_lbl.setText("[14:20:05] [SAY] Zaphod says, \"Hail traveler, welcome to Meridian 59!\"\n[14:20:12] [IMPROVE] You have improved in Swordplay to 78%!")
                prev_lbl.setStyleSheet(f"font-size: {current_val}px; color: #e2e8f0; font-family: 'Consolas', monospace;")
            elif preview_type == "dashboard":
                prev_lbl.setText("Might: 50   Intellect: 45   Stamina: 50   Agility: 40   Mysticism: 30")
                prev_lbl.setStyleSheet(f"font-size: {current_val}px; font-weight: 800; color: #94a3b8;")
            elif preview_type == "clock":
                prev_lbl.setText("14:35:10 - Meridian 59 World Time")
                prev_lbl.setStyleSheet(f"font-size: {current_val}px; font-weight: 900; color: #f8fafc; font-family: 'Consolas', monospace;")
            elif preview_type == "sidebar":
                prev_lbl.setText("  Dashboard (Main)      Shortcuts (Macros)      Chat Logger (Comms)      Settings (Preferences)")
                prev_lbl.setStyleSheet(f"font-size: {current_val}px; font-weight: 700; color: #94a3b8;")

            pb_layout.addWidget(prev_lbl)
            c_layout.addWidget(prev_box)

            # Step button click handlers
            def update_val(new_val):
                clamped = max(min_fs, min(max_fs, new_val))
                self.font_settings[group_key] = clamped
                val_badge.setText(f"{clamped} px")

                # Local Preview Update
                if preview_type == "player_list":
                    prev_lbl.setStyleSheet(f"font-size: {clamped}px; font-weight: 700; color: #e0e0e0;")
                elif preview_type == "chat_logger":
                    prev_lbl.setStyleSheet(f"font-size: {clamped}px; color: #e2e8f0; font-family: 'Consolas', monospace;")
                elif preview_type == "dashboard":
                    prev_lbl.setStyleSheet(f"font-size: {clamped}px; font-weight: 800; color: #94a3b8;")
                elif preview_type == "clock":
                    prev_lbl.setStyleSheet(f"font-size: {clamped}px; font-weight: 900; color: #f8fafc; font-family: 'Consolas', monospace;")
                elif preview_type == "sidebar":
                    prev_lbl.setStyleSheet(f"font-size: {clamped}px; font-weight: 700; color: #94a3b8;")

                # Apply globally
                self.apply_font_settings()
                self.save_gui_settings({"font_settings": self.font_settings})

            btn_minus.clicked.connect(lambda: update_val(self.font_settings.get(group_key, 13) - 1))
            btn_plus.clicked.connect(lambda: update_val(self.font_settings.get(group_key, 13) + 1))

            return card

        # Add Font Groups
        typo_layout.addWidget(make_font_step_card(
            "player_list", "Player List (Who's Online)", "👥",
            "Font sizing for online player names and alignment tags in the Who List widget.",
            10, 24, "player_list"
        ))
        typo_layout.addWidget(make_font_step_card(
            "chat_logger", "Chat Logger & Comms Log", "💬",
            "Text sizing for incoming game chat stream, combat lines, improve messages, and timestamps.",
            10, 24, "chat_logger"
        ))
        typo_layout.addWidget(make_font_step_card(
            "dashboard_cards", "Dashboard Stats & Attributes", "📊",
            "Font sizing for character stats (Might, Intellect, etc.), vital labels, and ledger rows.",
            10, 22, "dashboard"
        ))
        typo_layout.addWidget(make_font_step_card(
            "clock_panel", "World Clock & Dock Panel", "⏰",
            "Text sizing for Meridian 59 game world time clock and dock section headers.",
            12, 32, "clock"
        ))
        typo_layout.addWidget(make_font_step_card(
            "sidebar_nav", "Sidebar Navigation Menu", "🧭",
            "Font size for left sidebar menu buttons and navigation items.",
            10, 20, "sidebar"
        ))

        typo_layout.addStretch()
        typo_scroll.setWidget(typo_page)
        tab_widget.addTab(typo_scroll, "🔤 Typography")

        # --------------------------------------------------------------
        # TAB 2: AUDIO & SOUND ALERTS
        # --------------------------------------------------------------
        audio_scroll = QScrollArea()
        audio_scroll.setWidgetResizable(True)
        audio_scroll.setFrameShape(QFrame.NoFrame)
        audio_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        audio_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        audio_page = QWidget()
        audio_layout = QVBoxLayout(audio_page)
        audio_layout.setContentsMargins(12, 12, 12, 12)
        audio_layout.setSpacing(14)

        sound_card = QFrame()
        sound_card.setProperty("class", "WebCard")
        sc_layout = QVBoxLayout(sound_card)
        sc_layout.setContentsMargins(16, 16, 16, 16)
        sc_layout.setSpacing(14)

        sc_title = QLabel("🔊 Audio & Sound Alert Preferences")
        sc_title.setStyleSheet("font-size: 15px; font-weight: 800; color: #f8fafc;")
        sc_desc = QLabel("Configure audible alerts for incoming PK player attacks and Private messages (tells).")
        sc_desc.setStyleSheet("font-size: 12px; color: #64748b;")
        sc_layout.addWidget(sc_title)
        sc_layout.addWidget(sc_desc)

        # 1. PK Alert Audio Row
        pk_row = QHBoxLayout()
        pk_row.setSpacing(10)
        self.pk_chk = QCheckBox("Enable PK Audio Alerts")
        self.pk_chk.setChecked(getattr(self, 'pk_alert_enabled', True))
        self.pk_chk.setStyleSheet("color: #f8fafc; font-weight: 700; font-size: 13px;")

        self.pk_sound_combo = QComboBox()
        self.pk_sound_combo.setEditable(True)
        self.pk_sound_combo.setFixedWidth(220)
        sound_options = ["sound/alert.wav", "SystemExclamation", "SystemAsterisk", "SystemHand", "SystemQuestion"]
        cur_pk_s = getattr(self, 'pk_sound_path', "sound/alert.wav")
        if cur_pk_s not in sound_options:
            sound_options.insert(0, cur_pk_s)
        self.pk_sound_combo.addItems(sound_options)
        self.pk_sound_combo.setCurrentText(cur_pk_s)

        pk_browse_btn = QPushButton("Browse...")
        pk_browse_btn.setProperty("class", "WebBtnSecondary")
        def browse_pk():
            fn, _ = QFileDialog.getOpenFileName(self, "Select PK Alert Sound", "sound", "Audio Files (*.wav *.mp3);;All Files (*)")
            if fn:
                self.pk_sound_combo.setCurrentText(fn)
                self.save_sound_settings()
        pk_browse_btn.clicked.connect(browse_pk)

        pk_test_btn = QPushButton("▶ Test PK Sound")
        pk_test_btn.setProperty("class", "WebBtnPrimary")
        pk_test_btn.clicked.connect(lambda: play_audio_file(self.pk_sound_combo.currentText()))

        pk_row.addWidget(self.pk_chk)
        pk_row.addWidget(self.pk_sound_combo)
        pk_row.addWidget(pk_browse_btn)
        pk_row.addWidget(pk_test_btn)
        pk_row.addStretch()
        sc_layout.addLayout(pk_row)

        # 2. Private Message Audio Row
        tell_row = QHBoxLayout()
        tell_row.setSpacing(10)
        self.tell_chk = QCheckBox("Enable Private Message Audio Alerts")
        self.tell_chk.setChecked(getattr(self, 'tell_sound_enabled', True))
        self.tell_chk.setStyleSheet("color: #f8fafc; font-weight: 700; font-size: 13px;")

        self.tell_sound_combo = QComboBox()
        self.tell_sound_combo.setEditable(True)
        self.tell_sound_combo.setFixedWidth(220)
        tell_options = ["sound/dm_chime.wav", "sound/dm_chime.mp3", "SystemAsterisk", "SystemExclamation", "SystemHand"]
        cur_tell_s = getattr(self, 'tell_sound_path', "sound/dm_chime.wav")
        if cur_tell_s not in tell_options:
            tell_options.insert(0, cur_tell_s)
        self.tell_sound_combo.addItems(tell_options)
        self.tell_sound_combo.setCurrentText(cur_tell_s)

        tell_browse_btn = QPushButton("Browse...")
        tell_browse_btn.setProperty("class", "WebBtnSecondary")
        def browse_tell():
            fn, _ = QFileDialog.getOpenFileName(self, "Select Private Message Sound", "sound", "Audio Files (*.wav *.mp3);;All Files (*)")
            if fn:
                self.tell_sound_combo.setCurrentText(fn)
                self.save_sound_settings()
        tell_browse_btn.clicked.connect(browse_tell)

        tell_test_btn = QPushButton("▶ Test Private Sound")
        tell_test_btn.setProperty("class", "WebBtnPrimary")
        tell_test_btn.clicked.connect(lambda: play_audio_file(self.tell_sound_combo.currentText()))

        tell_row.addWidget(self.tell_chk)
        tell_row.addWidget(self.tell_sound_combo)
        tell_row.addWidget(tell_browse_btn)
        tell_row.addWidget(tell_test_btn)
        tell_row.addStretch()
        sc_layout.addLayout(tell_row)

        # 3. PK Red Box Visual Overlay Row
        redbox_row = QHBoxLayout()
        redbox_row.setSpacing(10)
        self.pk_redbox_chk = QCheckBox("Enable Visual Red Box Overlay Alert around Game Window")
        self.pk_redbox_chk.setChecked(getattr(self, 'pk_frame_enabled', True))
        self.pk_redbox_chk.setStyleSheet("color: #ef4444; font-weight: 700; font-size: 13px;")

        def test_redbox():
            if getattr(self, 'pk_frame', None):
                self.pk_frame.flash(5)
        pk_redbox_test_btn = QPushButton("🟥 Test Red Box Overlay (5s)")
        pk_redbox_test_btn.setProperty("class", "WebBtnSecondary")
        pk_redbox_test_btn.clicked.connect(test_redbox)

        redbox_row.addWidget(self.pk_redbox_chk)
        redbox_row.addWidget(pk_redbox_test_btn)
        redbox_row.addStretch()
        sc_layout.addLayout(redbox_row)

        def on_sound_config_changed():
            self.save_sound_settings()

        self.pk_chk.stateChanged.connect(on_sound_config_changed)
        self.pk_sound_combo.currentTextChanged.connect(on_sound_config_changed)
        self.tell_chk.stateChanged.connect(on_sound_config_changed)
        self.tell_sound_combo.currentTextChanged.connect(on_sound_config_changed)
        self.pk_redbox_chk.stateChanged.connect(on_sound_config_changed)

        # 4. Non-Disruptive Toast Notifications Card (Player Groups / Logins / Messages)
        toast_card = QFrame()
        toast_card.setProperty("class", "SettingsCard")
        tc_layout = QVBoxLayout(toast_card)
        tc_layout.setContentsMargins(16, 16, 16, 16)
        tc_layout.setSpacing(12)

        tc_title = QLabel("Floating Toast Alerts (Non-Disruptive)")
        tc_title.setProperty("class", "SettingsCardTitle")
        tc_desc = QLabel("Configure the duration, screen anchor position, and behavior of floating popup notifications for player group logins, logouts, and messages.")
        tc_desc.setProperty("class", "SettingsCardDesc")
        tc_layout.addWidget(tc_title)
        tc_layout.addWidget(tc_desc)

        # Toast Duration Row (Tactile Minus / Plus Stepper)
        dur_row = QHBoxLayout()
        dur_row.setSpacing(10)
        dur_label = QLabel("Toast Duration:")
        dur_label.setStyleSheet("color: #cbd5e1; font-weight: 600; font-size: 13px; min-width: 140px;")

        cur_dur = int(getattr(self, 'group_toast_duration_sec', 3))
        dur_val_lbl = QLabel(f"{cur_dur}s")
        dur_val_lbl.setStyleSheet("font-size: 13px; font-weight: 800; color: #38bdf8; min-width: 35px; text-align: center;")
        dur_val_lbl.setAlignment(Qt.AlignCenter)

        dur_minus_btn = QPushButton("－")
        dur_minus_btn.setFixedSize(28, 28)
        dur_minus_btn.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #f8fafc; font-weight: 800; font-size: 14px;
                border: 1px solid #334155; border-radius: 4px;
            }
            QPushButton:hover { background-color: #334155; }
        """)

        dur_plus_btn = QPushButton("＋")
        dur_plus_btn.setFixedSize(28, 28)
        dur_plus_btn.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #f8fafc; font-weight: 800; font-size: 14px;
                border: 1px solid #334155; border-radius: 4px;
            }
            QPushButton:hover { background-color: #334155; }
        """)

        def adjust_toast_dur(delta):
            v = max(1, min(15, int(getattr(self, 'group_toast_duration_sec', 3)) + delta))
            self.group_toast_duration_sec = v
            dur_val_lbl.setText(f"{v}s")
            self.save_sound_settings()

        dur_minus_btn.clicked.connect(lambda: adjust_toast_dur(-1))
        dur_plus_btn.clicked.connect(lambda: adjust_toast_dur(1))

        dur_row.addWidget(dur_label)
        dur_row.addWidget(dur_minus_btn)
        dur_row.addWidget(dur_val_lbl)
        dur_row.addWidget(dur_plus_btn)
        dur_row.addStretch()
        tc_layout.addLayout(dur_row)

        # Toast Screen Position Row
        pos_row = QHBoxLayout()
        pos_row.setSpacing(10)
        pos_label = QLabel("Screen Anchor:")
        pos_label.setStyleSheet("color: #cbd5e1; font-weight: 600; font-size: 13px; min-width: 140px;")

        self.toast_pos_combo = QComboBox()
        self.toast_pos_combo.setFixedWidth(200)
        self.toast_pos_combo.addItem("Bottom-Right (Default)", "bottom-right")
        self.toast_pos_combo.addItem("Top-Right", "top-right")
        self.toast_pos_combo.addItem("Bottom-Left", "bottom-left")
        self.toast_pos_combo.addItem("Top-Left", "top-left")

        cur_pos = getattr(self, 'group_toast_position', 'bottom-right')
        idx = self.toast_pos_combo.findData(cur_pos)
        if idx >= 0:
            self.toast_pos_combo.setCurrentIndex(idx)

        self.toast_pos_combo.currentIndexChanged.connect(lambda: self.save_sound_settings())

        toast_test_btn = QPushButton("🔔 Test Toast Alert")
        toast_test_btn.setProperty("class", "WebBtnPrimary")
        toast_test_btn.clicked.connect(lambda: self.show_group_toast_notification(
            title="Player Logged In",
            message="<b>TestPlayer</b> (Friends) is now online!",
            icon_type="login",
            player_name="TestPlayer",
            group_name="Friends"
        ))

        pos_row.addWidget(pos_label)
        pos_row.addWidget(self.toast_pos_combo)
        pos_row.addWidget(toast_test_btn)
        pos_row.addStretch()
        tc_layout.addLayout(pos_row)

        audio_layout.addWidget(sound_card)
        audio_layout.addWidget(toast_card)
        audio_layout.addStretch()
        audio_scroll.setWidget(audio_page)
        tab_widget.addTab(audio_scroll, "🔊 Audio & Alerts")

        # --------------------------------------------------------------
        # TAB 3: DIAGNOSTICS & DEBUGGING LOGS
        # --------------------------------------------------------------
        debug_scroll = QScrollArea()
        debug_scroll.setWidgetResizable(True)
        debug_scroll.setFrameShape(QFrame.NoFrame)
        debug_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        debug_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        debug_page = QWidget()
        debug_layout = QVBoxLayout(debug_page)
        debug_layout.setContentsMargins(12, 12, 12, 12)
        debug_layout.setSpacing(14)

        dbg_card = QFrame()
        dbg_card.setProperty("class", "WebCard")
        dbg_card_layout = QVBoxLayout(dbg_card)
        dbg_card_layout.setContentsMargins(16, 16, 16, 16)
        dbg_card_layout.setSpacing(12)

        dbg_title = QLabel("🐞 Diagnostics & Logging Management")
        dbg_title.setStyleSheet("font-size: 15px; font-weight: 800; color: #f8fafc;")
        dbg_desc = QLabel("Configure standard output (stdout), debug trace levels, and progression engine diagnostic log files.")
        dbg_desc.setStyleSheet("font-size: 12px; color: #64748b;")
        dbg_card_layout.addWidget(dbg_title)
        dbg_card_layout.addWidget(dbg_desc)

        # Checkbox 1: Stdout / Console Output
        self.dbg_console_chk = QCheckBox("Enable Standard Console Output (stdout)")
        self.dbg_console_chk.setChecked(getattr(self, 'console_output_enabled', True))
        self.dbg_console_chk.setStyleSheet("color: #f8fafc; font-weight: 700; font-size: 13px;")
        dbg_card_layout.addWidget(self.dbg_console_chk)
        c_desc = QLabel("  Streams live companion messages and system events to the terminal console.")
        c_desc.setStyleSheet("font-size: 11px; color: #64748b; margin-left: 22px;")
        dbg_card_layout.addWidget(c_desc)

        # Checkbox 2: Detailed Debug Messages
        self.dbg_debug_level_chk = QCheckBox("Enable Verbose Debug Level Traces")
        self.dbg_debug_level_chk.setChecked(getattr(self, 'console_debug_enabled', True))
        self.dbg_debug_level_chk.setStyleSheet("color: #f8fafc; font-weight: 700; font-size: 13px;")
        dbg_card_layout.addWidget(self.dbg_debug_level_chk)
        d_desc = QLabel("  Includes detailed internal telemetry, scraping events, and spell trance timings.")
        d_desc.setStyleSheet("font-size: 11px; color: #64748b; margin-left: 22px;")
        dbg_card_layout.addWidget(d_desc)

        # Checkbox 3: Dedicated Progression Tracker Log
        self.dbg_prog_chk = QCheckBox("Enable Progression Tracker Log (logs/progression_debug.log)")
        self.dbg_prog_chk.setChecked(getattr(self, 'progression_log_enabled', True))
        self.dbg_prog_chk.setStyleSheet("color: #38bdf8; font-weight: 700; font-size: 13px;")
        dbg_card_layout.addWidget(self.dbg_prog_chk)
        p_desc = QLabel("  Dedicated log recording school progression points, target vs cap math, spell validation, and impossible state flags.")
        p_desc.setStyleSheet("font-size: 11px; color: #64748b; margin-left: 22px;")
        dbg_card_layout.addWidget(p_desc)

        # Checkbox 4: Central Companion Diagnostic File
        self.dbg_file_chk = QCheckBox("Enable Companion Runtime Log (logs/companion_debug.log)")
        self.dbg_file_chk.setChecked(getattr(self, 'file_debug_enabled', True))
        self.dbg_file_chk.setStyleSheet("color: #f8fafc; font-weight: 700; font-size: 13px;")
        dbg_card_layout.addWidget(self.dbg_file_chk)
        f_desc = QLabel("  Writes all application errors, warnings, and session traces to the local logs file.")
        f_desc.setStyleSheet("font-size: 11px; color: #64748b; margin-left: 22px;")
        dbg_card_layout.addWidget(f_desc)

        # State change connectors
        def on_debug_config_changed():
            self.save_debug_settings()
        self.dbg_console_chk.stateChanged.connect(on_debug_config_changed)
        self.dbg_debug_level_chk.stateChanged.connect(on_debug_config_changed)
        self.dbg_prog_chk.stateChanged.connect(on_debug_config_changed)
        self.dbg_file_chk.stateChanged.connect(on_debug_config_changed)

        # Debug Actions Row
        dbg_btn_row = QHBoxLayout()
        dbg_btn_row.setSpacing(10)

        open_logs_btn = QPushButton("📁 Open Logs Folder")
        open_logs_btn.setProperty("class", "WebBtnSecondary")
        open_logs_btn.setToolTip("Open the local logs/ directory in Windows Explorer")
        open_logs_btn.clicked.connect(self.open_logs_folder)
        dbg_btn_row.addWidget(open_logs_btn)

        clear_logs_btn = QPushButton("🧹 Clear Log Files")
        clear_logs_btn.setProperty("class", "WebBtnSecondary")
        clear_logs_btn.setToolTip("Safely empty existing log files")
        clear_logs_btn.clicked.connect(self.clear_app_logs)
        dbg_btn_row.addWidget(clear_logs_btn)

        refresh_preview_btn = QPushButton("🔄 Refresh Log View")
        refresh_preview_btn.setProperty("class", "WebBtnPrimary")
        refresh_preview_btn.setToolTip("Load recent entries from progression and companion log files")
        refresh_preview_btn.clicked.connect(self.refresh_debug_log_preview)
        dbg_btn_row.addWidget(refresh_preview_btn)

        dbg_btn_row.addStretch()
        dbg_card_layout.addLayout(dbg_btn_row)

        # Live Log Preview Box
        preview_title = QLabel("📋 Recent Log Preview:")
        preview_title.setStyleSheet("font-size: 12px; font-weight: 700; color: #94a3b8; margin-top: 6px;")
        dbg_card_layout.addWidget(preview_title)

        self.debug_log_preview = QTextEdit()
        self.debug_log_preview.setReadOnly(True)
        self.debug_log_preview.setMinimumHeight(180)
        self.debug_log_preview.setStyleSheet("""
            QTextEdit {
                background-color: #030712;
                border: 1px solid #1e293b;
                border-radius: 6px;
                color: #38bdf8;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11px;
                padding: 8px;
            }
        """)
        dbg_card_layout.addWidget(self.debug_log_preview)
        self.refresh_debug_log_preview()

        debug_layout.addWidget(dbg_card)
        debug_layout.addStretch()
        debug_scroll.setWidget(debug_page)
        tab_widget.addTab(debug_scroll, "🐞 Debug & Logs")

        # --------------------------------------------------------------
        # TAB 4: SOFTWARE UPDATES & ABOUT
        # --------------------------------------------------------------
        upd_scroll = QScrollArea()
        upd_scroll.setWidgetResizable(True)
        upd_scroll.setFrameShape(QFrame.NoFrame)
        upd_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        upd_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        upd_page = QWidget()
        upd_layout = QVBoxLayout(upd_page)
        upd_layout.setContentsMargins(12, 12, 12, 12)
        upd_layout.setSpacing(14)

        update_card = QFrame()
        update_card.setProperty("class", "WebCard")
        uc_layout = QVBoxLayout(update_card)
        uc_layout.setContentsMargins(16, 16, 16, 16)
        uc_layout.setSpacing(12)

        u_hdr = QHBoxLayout()
        u_title = QLabel("🚀 Software Updates")
        u_title.setStyleSheet("font-size: 15px; font-weight: 800; color: #f8fafc;")
        u_hdr.addWidget(u_title)
        u_hdr.addStretch()

        v_badge = QLabel(f"  v{self.version}  ")
        v_badge.setStyleSheet("background-color: #065f46; color: #34d399; font-weight: 800; font-size: 11px; padding: 4px 8px; border-radius: 6px;")
        u_hdr.addWidget(v_badge)
        uc_layout.addLayout(u_hdr)

        u_desc = QLabel("Check GitHub for the latest releases, performance improvements, and feature updates.")
        u_desc.setStyleSheet("font-size: 12px; color: #64748b;")
        uc_layout.addWidget(u_desc)

        u_notice = QLabel("⚠️ <b>Setup Note:</b> Ensure M59 Companion runs inside its own dedicated folder (e.g., <code>C:\\M59Companion\\</code>) so settings and logs persist properly.")
        u_notice.setWordWrap(True)
        u_notice.setStyleSheet("font-size: 11px; color: #c7d2fe; background-color: #1e1b4b; border: 1px solid #4338ca; border-radius: 6px; padding: 8px 10px;")
        uc_layout.addWidget(u_notice)

        u_btn_row = QHBoxLayout()
        u_btn_row.setSpacing(10)

        check_upd_btn = QPushButton("🔄 Check for Updates")
        check_upd_btn.setProperty("class", "WebBtnPrimary")
        check_upd_btn.clicked.connect(self.trigger_manual_update_check)
        u_btn_row.addWidget(check_upd_btn)

        exe_upd_btn = QPushButton("🌐 Download Raw EXE")
        exe_upd_btn.setProperty("class", "WebBtnSecondary")
        exe_upd_btn.setToolTip("Opens your browser directly to download M59Companion.exe")
        exe_upd_btn.clicked.connect(lambda: m59_updater.open_browser(m59_updater.REPO_URLS[0]["exe_url"]))
        u_btn_row.addWidget(exe_upd_btn)

        gh_btn = QPushButton("🌐 Open GitHub Repo")
        gh_btn.setProperty("class", "WebBtnSecondary")
        gh_btn.clicked.connect(lambda: m59_updater.open_browser())
        u_btn_row.addWidget(gh_btn)

        u_btn_row.addStretch()
        uc_layout.addLayout(u_btn_row)

        upd_layout.addWidget(update_card)
        upd_layout.addStretch()
        upd_scroll.setWidget(upd_page)
        tab_widget.addTab(upd_scroll, "🚀 Software Updates")

        main_layout.addWidget(tab_widget, 1)
        return page

    def start_background_update_check(self):
        """Starts a recurring 5-minute background timer and thread to check for GitHub updates."""
        def _check():
            try:
                res = check_all_releases(self.version)
                if res.get("update_available") or res.get("stable_update_available"):
                    self.signals.update_detected.emit(res)
            except Exception as ex:
                print(f"[M59-UPDATER] Background check error: {ex}", flush=True)

        # Initial background check after 2s
        QTimer.singleShot(2000, lambda: threading.Thread(target=_check, daemon=True).start())

        # Setup 5-minute timer (300,000 ms)
        if not hasattr(self, 'update_check_timer') or self.update_check_timer is None:
            self.update_check_timer = QTimer(self)
            self.update_check_timer.timeout.connect(lambda: threading.Thread(target=_check, daemon=True).start())
            self.update_check_timer.start(300000)

    def show_update_toast(self, release_data):
        """Displays a non-intrusive toast banner when an update is available."""
        if not hasattr(self, 'update_toast_widget'):
            return
        ver = release_data.get("latest_version") or release_data.get("stable_version") or "Newer"
        if ver and getattr(self, "_dismissed_update_version", None) == ver:
            return

        self.current_release_data = release_data
        self.toast_msg_lbl.setText(f"🚀 Software Update Available: v{ver}! (Installed: v{str(self.version).lstrip('v')})")

        try:
            self.toast_action_btn.clicked.disconnect()
        except Exception:
            pass
        self.toast_action_btn.clicked.connect(lambda: show_qt_update_dialog(self, release_data))

        self.update_toast_widget.setVisible(True)

    def hide_update_toast(self):
        """Hides the update toast banner until dismissed."""
        ver = None
        if hasattr(self, 'current_release_data') and self.current_release_data:
            ver = self.current_release_data.get("latest_version") or self.current_release_data.get("stable_version")
        self._dismissed_update_version = ver
        if hasattr(self, 'update_toast_widget'):
            self.update_toast_widget.setVisible(False)

    def on_update_detected(self, release_data):
        """Displays non-intrusive toast notification when an update is detected."""
        self.show_update_toast(release_data)

    def trigger_manual_update_check(self):
        """Manually checks for releases, opening the update dialog or informing user."""
        def _run():
            res = check_all_releases(self.version)
            def _ui():
                if res.get("error"):
                    QMessageBox.warning(self, "Update Check Failed", f"Could not reach update servers:\n{res['error']}")
                else:
                    show_qt_update_dialog(self, res)
            QTimer.singleShot(0, _ui)
        threading.Thread(target=_run, daemon=True).start()

    def reset_font_settings(self):
        """Resets all font size groups to default values."""
        defaults = {
            "player_list": 13,
            "chat_logger": 13,
            "dashboard_cards": 13,
            "clock_panel": 13,
            "sidebar_nav": 13,
        }
        self.font_settings.update(defaults)
        self.apply_font_settings()
        self.save_gui_settings({"font_settings": self.font_settings})
        if hasattr(self, 'stacked_widget') and hasattr(self, 'page_settings'):
            current_idx = self.stacked_widget.indexOf(self.page_settings)
            new_page = self.build_settings_page()
            self.stacked_widget.removeWidget(self.page_settings)
            self.page_settings = new_page
            self.stacked_widget.insertWidget(current_idx, self.page_settings)
            self.stacked_widget.setCurrentIndex(current_idx)

    def apply_font_settings(self):
        """Applies font size changes across all functional UI components in real-time."""
        # 1. Player List (Who List)
        if hasattr(self, 'wholist_data'):
            self.update_wholist_gui(self.wholist_data)

        # 2. Chat Logger Text Stream
        if hasattr(self, 'chat_stream_view') and self.chat_stream_view:
            self.filter_chat_stream()

        # 3. Sidebar Navigation Menu
        if hasattr(self, 'nav_list') and self.nav_list:
            fs = self.font_settings.get("sidebar_nav", 13)
            nav_font = QFont("Segoe UI", fs)
            nav_font.setWeight(QFont.Bold)
            self.nav_list.setFont(nav_font)
            for i in range(self.nav_list.count()):
                item = self.nav_list.item(i)
                if item:
                    item.setFont(nav_font)
            self.nav_list.setStyleSheet(f"""
                QListWidget#NavList {{
                    background-color: transparent;
                    border: none;
                    outline: none;
                }}
                QListWidget#NavList::item {{
                    background-color: transparent;
                    color: #94a3b8;
                    border-radius: 6px;
                    padding: 6px 10px;
                    font-size: {fs}px;
                    font-weight: 700;
                    margin-bottom: 2px;
                }}
                QListWidget#NavList::item:selected {{
                    background-color: rgba(16, 185, 129, 0.15);
                    color: #94a3b8;
                    border: 1px solid rgba(16, 185, 129, 0.3);
                }}
                QListWidget#NavList::item:hover:!selected {{
                    background-color: #111827;
                    color: #f8fafc;
                }}
            """)

        # 4. Dock World Clock
        if hasattr(self, 'dock_game_time_lbl') and self.dock_game_time_lbl:
            fs = self.font_settings.get("clock_panel", 13)
            self.dock_game_time_lbl.setStyleSheet(f"""
                font-size: {fs}px;
                font-weight: 800;
                color: #f8fafc;
                font-family: 'Consolas', monospace;
                background: transparent;
                border: none;
                padding: 0px;
            """)

        # 5. Dashboard Attribute Labels & Character Title
        if hasattr(self, 'attr_labels') and self.attr_labels:
            fs = self.font_settings.get("dashboard_cards", 13)
            for key, lbl in self.attr_labels.items():
                lbl.setStyleSheet(f"font-size: {fs + 3}px; font-weight: 900; color: #f1f5f9; margin-top: 2px;")

    def show_splash_overlay(self, mode="searching", title=None, msg=None):
        """Displays or updates the frameless splash screen overlay with status messages."""
        if not self.splash_screen:
            self.splash_screen = M59SplashScreen()
        self.splash_screen.set_status(mode, title, msg)
        self.splash_screen.show()
        self.splash_screen.raise_()

    def hide_splash_overlay(self):
        """Closes and releases the splash screen overlay once initialized."""
        if self.splash_screen:
            try:
                self.splash_screen.close()
            except Exception:
                pass
            self.splash_screen = None

    # ------------------------------------------------------------------
    # Backend Engine Initialization & Attachment
    # ------------------------------------------------------------------
    def init_lifecycle_engine(self):
        """Starts InstanceManager to search and auto-attach to meridian.exe."""
        print("[M59-ENGINE] Initializing InstanceManager game attachment loop...", flush=True)
        if InstanceManager is not None:
            def _on_conn(pm, pid):
                print(f"\n[M59-ENGINE] >>> Game Connected Signal Triggered for PID {pid} <<<", flush=True)
                self.signals.game_connected.emit(pm, pid)

            def _on_disc(pid):
                print(f"\n[M59-ENGINE] >>> Game Disconnected Signal Triggered for PID {pid} <<<", flush=True)
                self.signals.game_disconnected.emit(pid)

            def _on_multi(instances):
                print(f"[M59-ENGINE] Multiple instances found ({len(instances)}). Auto-selecting active process...", flush=True)
                selected_pid = instances[0]['pid']
                for inst in instances:
                    if " --- " in inst.get("title", ""):
                        selected_pid = inst['pid']
                        break
                print(f"[M59-ENGINE] Auto-assigning PID {selected_pid}", flush=True)
                self.lifecycle.assign_instance(selected_pid)

            self.lifecycle = InstanceManager(
                target_name=GAME_EXE,
                on_connect_cb=_on_conn,
                on_disconnect_cb=_on_disc,
                on_multiple_found=_on_multi
            )
            self.lifecycle.start()
            print(f"[M59-ENGINE] InstanceManager started monitoring for '{GAME_EXE}'", flush=True)
        else:
            print("[M59-ENGINE] InstanceManager module not found! Engine offline.", flush=True)
            self.status_txt.setText("⚪ Engine Offline (Standalone)")
            self.status_txt.setStyleSheet("font-size: 11px; font-weight: 700; color: #94a3b8;")

    def on_game_connected(self, pm, pid):
        self.pm_obj = pm
        self.target_pid = pid
        print(f"[M59-ATTACH] Attached to process PID: {pid}", flush=True)

        self.main_hwnd = find_game_hwnd(pid)
        print(f"[M59-ATTACH] Located main window HWND: {self.main_hwnd}", flush=True)
        if getattr(self, 'pk_frame', None):
            self.pk_frame.set_target_hwnd(self.main_hwnd)

        self.status_txt.setText(f"🟢 Attached: meridian.exe (PID: {pid})")
        self.status_txt.setStyleSheet("font-size: 11px; font-weight: 700; color: #94a3b8;")

        # Update Splash Screen Status
        self.show_splash_overlay("login", "↻ WAITING FOR CHARACTER LOGIN", "Process attached. Please select a character in Meridian 59.")

        # Start WhoList Monitor
        if WhoListMonitor and pid:
            if self.wholist_monitor:
                try: self.wholist_monitor.stop()
                except: pass
            def _on_who(players):
                self.signals.wholist_updated.emit(players)
            self.wholist_monitor = WhoListMonitor(pid, _on_who)
            self.wholist_monitor.start()
            print("[M59-ATTACH] Started WhoListMonitor background thread.", flush=True)

        # Initialize Inventory Scraper
        if InventoryScraper and pm:
            try:
                self.inventory_scraper = InventoryScraper(pm)
                print("[M59-ATTACH] InventoryScraper initialized for active process.", flush=True)
            except Exception as e:
                print(f"[M59-ATTACH] InventoryScraper init error: {e}", flush=True)

        QTimer.singleShot(1000, self.poll_inventory)

        # Re-detect installation resource directory if attached to process
        try:
            from m59_map import detect_installation
            rooms_dir, _, _ = detect_installation()
            if rooms_dir and getattr(self, "bgf_manager", None):
                self.bgf_manager.resource_dir = rooms_dir
        except Exception:
            pass

        # Reset initial sync state flags for fresh connection
        self._initial_sync_done = False
        self._initial_sync_started = False

        # Start login check loop
        self.check_for_login()

    def on_identity_found(self, cid):
        if cid and cid != "--" and cid != self.char_name:
            print(f"[M59-LOGIN] Background identity resolved: '{cid}'", flush=True)
            prev_char = self.char_name
            self.char_name = cid
            self.char_name_lbl.setText(f"CHARACTER: {self.char_name}")
            self.char_sub_lbl.setText(f"ATTACHED & SYNCED | PID: {self.target_pid} | HWND: {self.main_hwnd}")
            self.combat_monitor = CombatMonitor(self.char_name)
            self.spell_manager.set_character(self.char_name)
            self.load_kill_book()
            self.bank_manager.load_balances(self.char_name)
            self.load_vault_cache()
            self.load_attributes_cache(self.char_name)
            self.load_knowledge_cache(self.char_name)
            self.load_dms_cache(self.char_name)

            if not getattr(self, '_initial_sync_done', False) and not getattr(self, '_initial_sync_started', False):
                self._initial_sync_started = True
                self.show_splash_overlay("initializing", "↻ INITIALIZING GAME STATE", f"Synchronizing memory state for {self.char_name}...")
                self.trigger_manual_sync(is_initial=True)

    def check_for_login(self):
        """Continuously polls window title to detect character login, logoff, and timeout states."""
        if win32gui and self.target_pid:
            # Re-verify window handle if lost or recreated
            if not self.main_hwnd or not win32gui.IsWindow(self.main_hwnd):
                self.main_hwnd = find_game_hwnd(self.target_pid)
                if getattr(self, 'pk_frame', None):
                    self.pk_frame.set_target_hwnd(self.main_hwnd)

            if self.main_hwnd and win32gui.IsWindow(self.main_hwnd):
                try:
                    title = win32gui.GetWindowText(self.main_hwnd)
                    title_clean = title.strip()
                    title_lower = title_clean.lower()

                    # Exact titles or keywords indicating logged off / character selection screen
                    logged_off_keywords = [
                        "select character", "character selection", "login", "server select"
                    ]
                    is_bare_m59 = title_lower in ["meridian 59", "meridian59", ""]
                    is_logged_off = is_bare_m59 or any(kw in title_lower for kw in logged_off_keywords)

                    if not is_logged_off:
                        # Character is logged into the game!
                        scraped_char = "--"

                        # 1. Try parsing character name & room from " --- " title format
                        if " --- " in title:
                            parts = title.split(" --- ")
                            if len(parts) >= 2:
                                sub_parts = parts[0].split(" - ")
                                if len(sub_parts) >= 2:
                                    cand = sub_parts[-1].strip()
                                    if cand and cand.lower() not in ["--", "meridian 59", "meridian59"]:
                                        scraped_char = cand
                                room_candidate = parts[1].strip()
                                if room_candidate and room_candidate != getattr(self, 'current_room_name', None):
                                    self.signals.room_changed.emit(room_candidate)

                        # 2. Try parsing character name from " - " title format if not found yet
                        if scraped_char == "--" and " - " in title:
                            sub_parts = title.split(" - ")
                            if len(sub_parts) >= 2:
                                cand = sub_parts[1].strip()
                                if cand and cand.lower() not in ["--", "meridian 59", "meridian59", "login", "select character", "main screen"]:
                                    scraped_char = cand

                        # 3. If character name was already known and logged in, keep it
                        if scraped_char == "--" and self.char_name != "--":
                            scraped_char = self.char_name

                        # 4. Perform background identity capture ONLY on initial startup if name still unknown
                        if not getattr(self, '_initial_sync_done', False):
                            if (scraped_char == "--" or self.char_name == "--") and capture_identity and not getattr(self, '_is_capturing_identity', False):
                                now = time.time()
                                last_try = getattr(self, '_last_identity_capture_time', 0)
                                if now - last_try > 5:
                                    self._last_identity_capture_time = now
                                    self._is_capturing_identity = True
                                    def bio_worker():
                                        try:
                                            print("[M59-LOGIN] Non-blocking identity capture started...", flush=True)
                                            cid = capture_identity(self.main_hwnd, self.target_pid)
                                            if cid and cid != "--":
                                                self.signals.identity_found.emit(cid)
                                        except Exception as ex:
                                            print(f"[M59-LOGIN] Background bio capture exception: {ex}", flush=True)
                                        finally:
                                            self._is_capturing_identity = False
                                    threading.Thread(target=bio_worker, daemon=True).start()

                        # 5. Handle character name update / login transition
                        if scraped_char != "--" and self.char_name != scraped_char:
                            prev_char = self.char_name
                            self.char_name = scraped_char
                            print(f"[M59-LOGIN] Character login detected: '{self.char_name}' (was '{prev_char}')", flush=True)

                            self.char_name_lbl.setText(f"CHARACTER: {self.char_name}")
                            self.char_sub_lbl.setText(f"ATTACHED & SYNCED | PID: {self.target_pid} | HWND: {self.main_hwnd}")
                            self.combat_monitor = CombatMonitor(self.char_name)
                            self.spell_manager.set_character(self.char_name)
                            self.load_kill_book()
                            self.bank_manager.load_balances(self.char_name)
                            self.load_vault_cache()
                            self.load_attributes_cache(self.char_name)
                            self.load_knowledge_cache(self.char_name)
                            self.load_dms_cache(self.char_name)

                            # Trigger initial memory scrape ONLY ONCE on initial process startup
                            if not getattr(self, '_initial_sync_done', False) and not getattr(self, '_initial_sync_started', False):
                                self._initial_sync_started = True
                                self.show_splash_overlay("initializing", "↻ INITIALIZING GAME STATE", f"Synchronizing memory state for {self.char_name}...")
                                self.trigger_manual_sync(is_initial=True)

                        # 6. CRITICAL FALLBACK: If game is logged in, but char_name is still "--" and initial sync hasn't run yet, start sync!
                        elif not getattr(self, '_initial_sync_done', False) and not getattr(self, '_initial_sync_started', False):
                            self._initial_sync_started = True
                            print("[M59-LOGIN] Active game session detected. Starting initial sync cycle while identity resolves...", flush=True)
                            self.char_sub_lbl.setText(f"ATTACHED & SYNCING | PID: {self.target_pid} | HWND: {self.main_hwnd}")
                            self.show_splash_overlay("initializing", "↻ INITIALIZING GAME STATE", "Synchronizing memory state...")
                            self.trigger_manual_sync(is_initial=True)

                    else:
                        # Character is NOT logged in (timed out or back at character select / login screen)
                        # IF initial sync was already performed once, silently wait without resetting or interrupting the user
                        if not getattr(self, '_initial_sync_done', False):
                            self._initial_sync_started = False
                            if self.char_name != "--":
                                print(f"[M59-LOGIN] Timeout / logoff detected! Resetting state from '{self.char_name}' to '--'.", flush=True)
                                self.char_name = "--"
                                self.char_name_lbl.setText("CHARACTER: --")
                                self.char_sub_lbl.setText(f"ATTACHMENT: Waiting for character login | PID: {self.target_pid}")

                                # Show splash overlay for login wait
                                self.show_splash_overlay("login", "↻ WAITING FOR CHARACTER LOGIN", "Session timed out or logged off. Please select a character in Meridian 59.")
                except Exception as e:
                    print(f"[M59-LOGIN] Error checking login status: {e}", flush=True)

        # Schedule continuous loop re-check every 1.5 seconds
        QTimer.singleShot(1500, self.check_for_login)

    def on_game_disconnected(self, pid):
        print(f"[M59-DISC] Game process PID {pid} disconnected.", flush=True)
        self.status_txt.setText("🟡 Searching for meridian.exe...")
        self.status_txt.setStyleSheet("font-size: 11px; font-weight: 700; color: #f59e0b;")

        self.char_name = "--"
        self.target_pid = None
        self.pm_obj = None
        self.main_hwnd = None
        self._initial_sync_done = False
        self._initial_sync_started = False
        if getattr(self, 'pk_frame', None):
            self.pk_frame.set_target_hwnd(None)
        self.inventory_scraper = None

        self.char_name_lbl.setText("CHARACTER: --")
        self.char_sub_lbl.setText("ATTACHMENT: Waiting for active Meridian 59 game process...")

        if self.wholist_monitor:
            try: self.wholist_monitor.stop()
            except: pass
            self.wholist_monitor = None

        self.update_inventory_ui(0, 0, 0.0, 0.0, 1700, [])

        self.show_splash_overlay("searching", "↻ SCANNING FOR GAME PROCESS", "Please launch Meridian 59 (meridian.exe) to continue...")

    # ------------------------------------------------------------------
    # Manual & Automatic Memory Sync Engine
    # ------------------------------------------------------------------
    def trigger_manual_sync(self, is_initial=False):
        print("[M59-SYNC] Manual Memory Scrape Cycle Triggered.", flush=True)
        if not self.pm_obj or not self.main_hwnd:
            print("[M59-SYNC] Memory scrape skipped: Game process not attached.", flush=True)
            return

        self._is_initial_sync = is_initial
        self.sync_btn.setEnabled(False)
        self.sync_btn.setText("⏳ Scraping...")

        def scrape_worker():
            try:
                if cycle_tabs_and_scrape and MemoryReader:
                    mr = MemoryReader(self.pm_obj)
                    kn, st = cycle_tabs_and_scrape(self.main_hwnd, mr)
                    if st:
                        print(f"[M59-SYNC] Memory Scrape Success! Received stats: {st}", flush=True)
                        self.signals.sync_stats_received.emit(st)
                    if kn:
                        print(f"[M59-SYNC] Memory Scrape Success! Received knowledge dict ({len(kn)} entries): {kn}", flush=True)
                        self.signals.knowledge_updated.emit(kn)
                    if not st and not kn:
                        print("[M59-SYNC] Memory scrape yielded no stats (UI tab transition needed).", flush=True)
            except Exception as e:
                print(f"[M59-SYNC] Error during memory scrape: {e}", flush=True)
            finally:
                self.signals.scrape_finished.emit()

        threading.Thread(target=scrape_worker, daemon=True).start()

    def on_scrape_finished(self):
        """Qt Slot called on main GUI thread when memory scrape cycle finishes."""
        self.sync_btn.setEnabled(True)
        self.sync_btn.setText("🔄 Sync")

        if getattr(self, "_is_initial_sync", False):
            self._is_initial_sync = False
            self._initial_sync_done = True
            self.show_splash_overlay("connected", f"🟢 CONNECTED: {self.char_name.upper()}", "Game state & memory synchronized successfully!")
            QTimer.singleShot(1200, self.hide_splash_overlay)

        # Post-sync game client focus restoration
        def restore_game_focus():
            if self.main_hwnd and win32gui and win32gui.IsWindow(self.main_hwnd):
                try:
                    win32gui.SetForegroundWindow(self.main_hwnd)
                    lparam = (100 & 0xFFFF) | ((100 & 0xFFFF) << 16)
                    win32gui.PostMessage(self.main_hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
                    win32gui.PostMessage(self.main_hwnd, win32con.WM_LBUTTONUP, 0, lparam)
                    win32gui.PostMessage(self.main_hwnd, win32con.WM_KEYDOWN, win32con.VK_ESCAPE, 0)
                    win32gui.PostMessage(self.main_hwnd, win32con.WM_KEYUP, win32con.VK_ESCAPE, 0)
                except Exception as ex:
                    print(f"[M59-SYNC] Post-splash focus restore notice: {ex}", flush=True)

        QTimer.singleShot(1400 if getattr(self, "_is_initial_sync", False) else 300, restore_game_focus)

    def update_gui_stats(self, stats):
        print(f"[M59-GUI] Updating GUI with real scraped stats: {stats}", flush=True)

        # Update Vitals
        if "HP" in stats:
            self.hp_current = stats["HP"]
            if self.hp_max == 0 or self.hp_current > self.hp_max:
                self.hp_max = max(self.hp_current, 150)
            pct = int((self.hp_current / self.hp_max) * 100) if self.hp_max > 0 else 0
            self.hp_bar_widget['v_lbl'].setText(f"{self.hp_current} / {self.hp_max}")
            self.hp_bar_widget['pbar'].setValue(pct)

        if "MP" in stats:
            self.mp_current = stats["MP"]
            if self.mp_max == 0 or self.mp_current > self.mp_max:
                self.mp_max = max(self.mp_current, 250)
            pct = int((self.mp_current / self.mp_max) * 100) if self.mp_max > 0 else 0
            self.mp_bar_widget['v_lbl'].setText(f"{self.mp_current} / {self.mp_max}")
            self.mp_bar_widget['pbar'].setValue(pct)

        if "VG" in stats:
            self.vg_current = stats["VG"]
            if self.vg_max == 0 or self.vg_current > self.vg_max:
                self.vg_max = max(self.vg_current, 200)
            pct = int((self.vg_current / self.vg_max) * 100) if self.vg_max > 0 else 0
            self.vg_bar_widget['v_lbl'].setText(f"{self.vg_current} / {self.vg_max}")
            self.vg_bar_widget['pbar'].setValue(pct)

        # Update Attributes
        attr_keys = ["Might", "Intellect", "Stamina", "Agility", "Mysticism", "Aim", "Karma"]
        for k in attr_keys:
            if k in stats:
                self.attributes[k] = stats[k]
                if k in self.attr_labels:
                    self.attr_labels[k].setText(str(stats[k]))

        self.save_attributes_cache()

        if "Intellect" in stats:
            try:
                int_val = int(stats["Intellect"])
                if hasattr(self, 'prog_intellect_spin') and self.prog_intellect_spin:
                    self.prog_intellect_spin.blockSignals(True)
                    self.prog_intellect_spin.setValue(int_val)
                    self.prog_intellect_spin.blockSignals(False)
            except Exception:
                pass
            self.update_progression_ui()

        if "Might" in stats:
            self.poll_inventory()

    def update_wholist_gui(self, players):
        current_players = players or {}
        prev_players = getattr(self, 'previous_online_players', {})

        # Check for Login / Logout transitions for group alerts (if not the very first load)
        if prev_players:
            curr_keys = {str(k).lower(): (str(k), str(v)) for k, v in current_players.items()}
            prev_keys = {str(k).lower(): (str(k), str(v)) for k, v in prev_players.items()}

            # 1. New Logins
            for p_low, (orig_name, st) in curr_keys.items():
                if p_low not in prev_keys:
                    grp_name = self.get_player_group(orig_name)
                    if grp_name and grp_name in self.player_groups:
                        g_cfg = self.player_groups[grp_name]
                        if g_cfg.get("alert_login", True):
                            self.show_group_toast_notification(
                                title=f"Player Logged In",
                                message=f"<b>{orig_name}</b> ({grp_name}) is now online!",
                                icon_type="login",
                                player_name=orig_name,
                                group_name=grp_name
                            )
                        if g_cfg.get("sound_enabled", True):
                            self.play_tell_alert()

            # 2. Logouts
            for p_low, (orig_name, st) in prev_keys.items():
                if p_low not in curr_keys:
                    grp_name = self.get_player_group(orig_name)
                    if grp_name and grp_name in self.player_groups:
                        g_cfg = self.player_groups[grp_name]
                        if g_cfg.get("alert_logout", False):
                            self.show_group_toast_notification(
                                title=f"Player Logged Out",
                                message=f"<b>{orig_name}</b> ({grp_name}) has logged off.",
                                icon_type="logout",
                                player_name=orig_name,
                                group_name=grp_name
                            )
                        if g_cfg.get("sound_enabled", True):
                            self.play_tell_alert()

        # Record all currently online players into discovered_players cache
        discovered_changed = False
        if not hasattr(self, 'discovered_players') or not isinstance(self.discovered_players, dict):
            self.discovered_players = {}
        for p_name, p_stat in current_players.items():
            clean_p = str(p_name).strip().strip('"')
            if clean_p and clean_p.lower() not in self.discovered_players:
                self.discovered_players[clean_p.lower()] = {
                    "name": clean_p,
                    "last_status": str(p_stat)
                }
                discovered_changed = True
            elif clean_p and clean_p.lower() in self.discovered_players:
                # Update display name or status if changed
                if self.discovered_players[clean_p.lower()].get("last_status") != str(p_stat):
                    self.discovered_players[clean_p.lower()]["last_status"] = str(p_stat)
                    discovered_changed = True

        if discovered_changed:
            self.save_discovered_players()

        self.previous_online_players = dict(current_players)
        self.wholist_data = current_players
        self.who_list_widget.clear()

        query = self.who_search_input.text().lower().strip()

        status_colors = {
            "INNOCENT": "#e0e0e0",
            "WHITE": "#e0e0e0",
            "OUTLAW": "#ff9f43",
            "ORANGE": "#ff9f43",
            "MURDERER": "#ff6b6b",
            "RED": "#ff6b6b",
            "STAFF": "#48dbfb",
            "BLUE": "#48dbfb",
            "CREATOR": "#ffd32a",
            "YELLOW": "#ffd32a"
        }

        def player_sort_key(item):
            name, status = item
            s = str(status).upper() if status else "INNOCENT"
            if s in ("MURDERER", "RED"):
                group = 0
            elif s in ("OUTLAW", "ORANGE"):
                group = 1
            elif s in ("INNOCENT", "WHITE"):
                group = 3
            else:
                # Other colors (CREATOR / YELLOW, STAFF / BLUE, BARD, ADMIN, etc.)
                group = 2

            p_lower = str(name).lower()
            unread_c = getattr(self, 'unread_dms', {}).get(p_lower, 0)
            has_unread_flag = 0 if unread_c > 0 else 1

            return (group, has_unread_flag, p_lower)

        fs = getattr(self, 'font_settings', {}).get("player_list", 13)
        online_keys_lower = {str(k).lower() for k in self.wholist_data.keys()}
        collapsed_set = getattr(self, 'collapsed_groups', set())

        # Helper function to render a single player row
        def render_player_row(name, status, is_offline=False, last_ts="", target_list_widget=None):
            dst_list = target_list_widget if target_list_widget is not None else self.who_list_widget
            color = "#94a3b8" if is_offline else status_colors.get(str(status).upper(), "#e0e0e0")

            item_widget = QWidget()
            item_layout = QHBoxLayout(item_widget)
            item_layout.setContentsMargins(6, 1, 6, 1)
            item_layout.setSpacing(4)

            p_prefix = "⚪ " if is_offline else ""
            name_lbl = QLabel(f"{p_prefix}{name}")
            name_lbl.setStyleSheet(f"font-size: {fs}px; font-weight: 700; color: {color}; background: transparent;")
            item_layout.addWidget(name_lbl)
            item_layout.addStretch()

            if is_offline and last_ts:
                ts_lbl = QLabel(last_ts)
                ts_lbl.setStyleSheet("font-size: 9px; color: #64748b; background: transparent;")
                item_layout.addWidget(ts_lbl)

            p_lower = str(name).lower()
            unread_c = getattr(self, 'unread_dms', {}).get(p_lower, 0)
            if unread_c > 0:
                dm_badge = QPushButton(f"💬 {unread_c}")
                dm_badge.setCursor(Qt.PointingHandCursor)
                dm_badge.setStyleSheet("""
                    QPushButton {
                        background-color: #581c87;
                        color: #f0abfc;
                        font-weight: 800;
                        font-size: 10px;
                        padding: 1px 5px;
                        border-radius: 3px;
                        border: 1px solid #c084fc;
                    }
                    QPushButton:hover {
                        background-color: #7e22ce;
                        color: #ffffff;
                    }
                """)
                tip_suffix = " (Offline)" if is_offline else ""
                dm_badge.setToolTip(f"{unread_c} unread message(s) from {name}{tip_suffix}. Click to view DMs.")
                dm_badge.clicked.connect(lambda checked=False, n=name: self.open_dm_with_player(n))
                item_layout.addWidget(dm_badge)
            else:
                dm_btn = QPushButton("💬")
                dm_btn.setCursor(Qt.PointingHandCursor)
                dm_btn.setStyleSheet("""
                    QPushButton {
                        background-color: transparent;
                        color: #64748b;
                        font-size: 10px;
                        padding: 1px 3px;
                        border: none;
                        border-radius: 3px;
                    }
                    QPushButton:hover {
                        background-color: #1e293b;
                        color: #c084fc;
                    }
                """)
                dm_btn.setToolTip(f"Send Direct Message to {name}")
                dm_btn.clicked.connect(lambda checked=False, n=name: self.open_dm_with_player(n))
                item_layout.addWidget(dm_btn)

            list_item = QListWidgetItem()
            list_item.setSizeHint(QSize(0, max(22, fs + 6)))
            list_item.setData(Qt.UserRole, name)
            dst_list.addItem(list_item)
            dst_list.setItemWidget(list_item, item_widget)

        # Helper function to render a section / group header
        def render_section_header(grp_key, label_text, count_text, header_color="#38bdf8", is_collapsed=False, badge_bg="#0c4a6e"):
            hdr_item = QListWidgetItem()
            hdr_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            hdr_item.setSizeHint(QSize(0, 24))
            hdr_item.setData(Qt.UserRole + 1, grp_key) # Tag as section header

            hdr_widget = QWidget()
            hdr_widget.setCursor(Qt.PointingHandCursor)
            hw_layout = QHBoxLayout(hdr_widget)
            hw_layout.setContentsMargins(6, 3, 6, 2)
            hw_layout.setSpacing(6)

            arrow = "▶" if is_collapsed else "▼"
            hdr_lbl = QLabel(f"{arrow} {label_text.upper()}")
            hdr_lbl.setStyleSheet(f"font-size: 10px; font-weight: 800; color: {header_color}; letter-spacing: 0.6px; background: transparent;")
            hw_layout.addWidget(hdr_lbl)
            hw_layout.addStretch()

            cnt_badge = QLabel(f" {count_text} ")
            cnt_badge.setStyleSheet(f"background-color: {badge_bg}; color: #f8fafc; font-size: 9px; font-weight: 800; border-radius: 4px; padding: 1px 4px;")
            hw_layout.addWidget(cnt_badge)

            # Click on header toggles collapse
            def on_hdr_clicked(event, g=grp_key):
                if event.button() == Qt.LeftButton:
                    self.toggle_group_collapse(g)
            hdr_widget.mousePressEvent = on_hdr_clicked

            self.who_list_widget.addItem(hdr_item)
            self.who_list_widget.setItemWidget(hdr_item, hdr_widget)

        # Separate online players into custom groups and ungrouped ("Other Players")
        custom_groups_online = {g: [] for g in self.player_groups.keys()}
        ungrouped_online = []
        player_to_custom_group = {}
        for g_name, g_data in self.player_groups.items():
            for m in g_data.get("members", []):
                player_to_custom_group[m.lower()] = g_name

        for name, status in self.wholist_data.items():
            p_low = str(name).lower()
            if p_low in player_to_custom_group:
                g_target = player_to_custom_group[p_low]
                if g_target in custom_groups_online:
                    custom_groups_online[g_target].append((name, status))
                else:
                    ungrouped_online.append((name, status))
            else:
                ungrouped_online.append((name, status))

        has_custom_groups = bool(self.player_groups)

        # -------------------------------------------------------------
        # 1. RENDER CUSTOM GROUPS (ONLINE MEMBERS)
        # -------------------------------------------------------------
        for g_name in sorted(self.player_groups.keys()):
            g_online = custom_groups_online.get(g_name, [])
            g_sorted = sorted(g_online, key=player_sort_key)
            g_matching = [p for p in g_sorted if not query or query in str(p[0]).lower()]
            total_g_members = len(self.player_groups[g_name].get("members", []))
            is_collapsed = (g_name in collapsed_set)

            render_section_header(
                grp_key=g_name,
                label_text=f"📁 {g_name}",
                count_text=f"{len(g_online)}/{total_g_members} Online",
                header_color="#38bdf8",
                is_collapsed=is_collapsed,
                badge_bg="#0c4a6e"
            )

            if not is_collapsed:
                if not g_matching:
                    empty_item = QListWidgetItem()
                    empty_item.setFlags(Qt.NoItemFlags)
                    empty_item.setSizeHint(QSize(0, 20))
                    empty_lbl = QLabel(f"   <span style='color: #64748b; font-size: 11px; font-style: italic;'>No members currently online</span>")
                    empty_lbl.setStyleSheet("background: transparent;")
                    self.who_list_widget.addItem(empty_item)
                    self.who_list_widget.setItemWidget(empty_item, empty_lbl)
                else:
                    for name, status in g_matching:
                        render_player_row(name, status, is_offline=False)

        # -------------------------------------------------------------
        # 2. RENDER UNGROUPED PLAYERS ("OTHER PLAYERS" / GENERAL LIST)
        # -------------------------------------------------------------
        ungrouped_sorted = sorted(ungrouped_online, key=player_sort_key)
        ungrouped_matching = [p for p in ungrouped_sorted if not query or query in str(p[0]).lower()]

        if has_custom_groups:
            is_other_collapsed = ("Other Players" in collapsed_set)
            render_section_header(
                grp_key="Other Players",
                label_text="👥 Other Players",
                count_text=f"{len(ungrouped_online)} Online",
                header_color="#94a3b8",
                is_collapsed=is_other_collapsed,
                badge_bg="#1e293b"
            )
            if not is_other_collapsed:
                for name, status in ungrouped_matching:
                    render_player_row(name, status, is_offline=False)
        else:
            # No custom groups configured, render flat standard list
            for name, status in ungrouped_matching:
                render_player_row(name, status, is_offline=False)

        # -------------------------------------------------------------
        # 3. OFFLINE GROUP (COLLAPSIBLE, AT BOTTOM OF PLAYER LIST)
        # -------------------------------------------------------------
        # Collect all tracked players who are currently offline
        # - Discovered players who were logged in and are now offline
        # - Group members who are offline
        # - Players with unread/historical DMs who are offline
        offline_players_map = {} # name_lower -> dict(disp_name, unread_c, last_ts, group, last_status)
        
        # 1. Include all discovered players not currently online
        for p_low, p_info in getattr(self, 'discovered_players', {}).items():
            if p_low not in online_keys_lower:
                disp = p_info.get("name", p_low.capitalize()) if isinstance(p_info, dict) else str(p_info)
                l_stat = p_info.get("last_status", "INNOCENT") if isinstance(p_info, dict) else "INNOCENT"
                offline_players_map[p_low] = {
                    "disp_name": disp,
                    "unread_c": getattr(self, 'unread_dms', {}).get(p_low, 0),
                    "last_ts": "",
                    "group": self.get_player_group(disp),
                    "last_status": l_stat
                }

        # 2. Include all group members not currently online
        for g_name, g_data in self.player_groups.items():
            for m in g_data.get("members", []):
                m_low = m.lower()
                if m_low not in online_keys_lower:
                    if m_low in offline_players_map:
                        offline_players_map[m_low]["group"] = g_name
                        offline_players_map[m_low]["disp_name"] = m
                    else:
                        offline_players_map[m_low] = {
                            "disp_name": m,
                            "unread_c": getattr(self, 'unread_dms', {}).get(m_low, 0),
                            "last_ts": "",
                            "group": g_name,
                            "last_status": "INNOCENT"
                        }

        # 3. Include DM threads
        for p_key, count in getattr(self, 'unread_dms', {}).items():
            if p_key not in online_keys_lower:
                thread_info = getattr(self, 'player_dms', {}).get(p_key, {})
                disp_name = thread_info.get("player_name", p_key.capitalize())
                msgs = thread_info.get("messages", [])
                last_ts = msgs[-1]["ts"] if msgs else ""
                if p_key in offline_players_map:
                    offline_players_map[p_key]["unread_c"] = count
                    offline_players_map[p_key]["last_ts"] = last_ts
                else:
                    offline_players_map[p_key] = {
                        "disp_name": disp_name,
                        "unread_c": count,
                        "last_ts": last_ts,
                        "group": self.get_player_group(disp_name),
                        "last_status": "INNOCENT"
                    }

        if hasattr(self, 'who_offline_list_widget'):
            self.who_offline_list_widget.clear()

        if offline_players_map:
            offline_list = list(offline_players_map.values())
            # Sort offline players: unread messages first, custom group members next, then alphabetical name
            offline_list.sort(key=lambda x: (
                0 if x["unread_c"] > 0 else (1 if x["group"] else 2),
                x["disp_name"].lower()
            ))
            matching_offline = [p for p in offline_list if not query or query in p["disp_name"].lower()]

            # Default offline group to collapsed if not explicitly set
            is_offline_collapsed = ("__OFFLINE__" in collapsed_set)
            unread_total = sum(p["unread_c"] for p in offline_list)
            cnt_str = f"{len(offline_list)} Offline" + (f" • {unread_total} Unread" if unread_total > 0 else "")

            if hasattr(self, 'who_offline_dock'):
                self.who_offline_dock.setVisible(True)
                self.who_offline_cnt_badge.setText(cnt_str)
                if is_offline_collapsed:
                    self.who_offline_arrow_lbl.setText("▶")
                    self.who_offline_list_widget.setVisible(False)
                else:
                    self.who_offline_arrow_lbl.setText("▼")
                    self.who_offline_list_widget.setVisible(True)
                    for p_info in matching_offline:
                        render_player_row(
                            name=p_info["disp_name"],
                            status=p_info.get("last_status", "INNOCENT"),
                            is_offline=True,
                            last_ts=p_info["last_ts"],
                            target_list_widget=self.who_offline_list_widget
                        )
            else:
                render_section_header(
                    grp_key="__OFFLINE__",
                    label_text="⚪ Offline",
                    count_text=cnt_str,
                    header_color="#64748b",
                    is_collapsed=is_offline_collapsed,
                    badge_bg="#334155"
                )
                if not is_offline_collapsed:
                    for p_info in matching_offline:
                        render_player_row(
                            name=p_info["disp_name"],
                            status=p_info.get("last_status", "INNOCENT"),
                            is_offline=True,
                            last_ts=p_info["last_ts"]
                        )
        else:
            if hasattr(self, 'who_offline_dock'):
                self.who_offline_cnt_badge.setText("0 Offline")
                self.who_offline_arrow_lbl.setText("▶")
                self.who_offline_list_widget.setVisible(False)
                self.who_offline_dock.setVisible(False)

        self.who_count_badge.setText(f"{len(self.wholist_data)} Online")

    # ------------------------------------------------------------------
    # Clock & Timer Tick Handler
    # ------------------------------------------------------------------
    def on_clock_tick(self):
        self.session_seconds += 1
        # Update World Clock
        info = get_game_time()
        time_str = format_game_time(info, use_24h=self.use_24h_clock)
        self.dock_game_time_lbl.setText(time_str)

    # ------------------------------------------------------------------
    # Log Processing & Parsing Engine
    # ------------------------------------------------------------------
    def start_chat_monitor(self):
        """Monitors game HWND 1005 (Chat edit control) directly in real-time."""
        print("[M59-CHAT] Starting live HWND chat control monitor...", flush=True)

        def monitor_task():
            last_lines = []
            ch_hwnd = None

            while True:
                time.sleep(0.4)
                if not self.main_hwnd or not win32gui:
                    ch_hwnd = None
                    continue

                try:
                    if win32gui.IsWindow(self.main_hwnd):
                        ch_hwnd = win32gui.GetDlgItem(self.main_hwnd, 1005)

                    if ch_hwnd and win32gui.IsWindow(ch_hwnd) and get_text_from_hwnd:
                        raw_text = get_text_from_hwnd(ch_hwnd)
                        if raw_text:
                            cur_lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
                            if cur_lines:
                                if not last_lines:
                                    last_lines = cur_lines[-50:]
                                else:
                                    new_lines = []
                                    found = -1
                                    tail = list(last_lines)
                                    while tail:
                                        tl = len(tail)
                                        search = cur_lines[-100-tl:] if len(cur_lines) > 100 else cur_lines
                                        off = len(cur_lines) - len(search)
                                        for i in range(len(search) - tl, -1, -1):
                                            if search[i:i+tl] == tail:
                                                found = off + i + tl
                                                break
                                        if found != -1:
                                            break
                                        tail.pop(0)

                                    if found != -1:
                                        new_lines = cur_lines[found:]
                                    else:
                                        new_lines = cur_lines

                                    if new_lines:
                                        safe_n = get_safe_name(self.char_name if self.char_name and self.char_name != "--" else "game")
                                        log_p = os.path.join("settings", f"{safe_n}_chat.log")
                                        os.makedirs("settings", exist_ok=True)
                                        ts = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")

                                        try:
                                            with open(log_p, "a", encoding="utf-8") as f:
                                                for line in new_lines:
                                                    f.write(f"{ts} {line}\n")
                                                    self.signals.log_line_received.emit(line)
                                                f.flush()
                                        except Exception:
                                            for line in new_lines:
                                                self.signals.log_line_received.emit(line)

                                        last_lines = cur_lines[-50:]
                except Exception:
                    pass

        threading.Thread(target=monitor_task, daemon=True).start()

    def start_log_tail_loop(self):
        """Periodically tails active character's or most recent chat log file."""
        print("[M59-TAIL] Log tailing loop initialized.", flush=True)

        def tail_task():
            last_pos = 0
            last_file = ""
            while True:
                time.sleep(0.4)
                log_p = None
                if self.char_name and self.char_name != "--":
                    safe_n = get_safe_name(self.char_name)
                    log_p = os.path.join("settings", f"{safe_n}_chat.log")

                if not log_p or not os.path.exists(log_p):
                    if os.path.exists("settings"):
                        log_files = [os.path.join("settings", f) for f in os.listdir("settings") if f.endswith(".log") and "debug" not in f.lower()]
                        if log_files:
                            log_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                            log_p = log_files[0]

                if log_p and os.path.exists(log_p):
                    try:
                        curr_size = os.path.getsize(log_p)
                        if log_p != last_file or curr_size < last_pos:
                            last_file = log_p
                            last_pos = curr_size
                            print(f"[M59-TAIL] Tailing active chat log file: {log_p}", flush=True)

                        if curr_size > last_pos:
                            with open(log_p, "r", encoding="utf-8", errors="ignore") as f:
                                f.seek(last_pos)
                                lines = f.readlines()
                                for l in lines:
                                    if l.strip():
                                        self.signals.log_line_received.emit(l.strip())
                                last_pos = f.tell()
                    except Exception as e:
                        print(f"[M59-TAIL] Error reading log file {log_p}: {e}", flush=True)

        threading.Thread(target=tail_task, daemon=True).start()

    # ------------------------------------------------------------------
    # DIRECT MESSAGES (DMs) & PLAYER MESSAGING ENGINE
    # ------------------------------------------------------------------
    def record_direct_message(self, timestamp, player_name, text, raw_text, direction="in", msg_type="tell", is_historical=False):
        """Stores direct message in player conversation thread, tracks unread counters, and triggers UI updates."""
        if not player_name or player_name.lower() in ("you", "--", "unknown"):
            return

        p_key = player_name.lower()
        if not hasattr(self, 'player_dms'):
            self.player_dms = {}
        if not hasattr(self, 'unread_dms'):
            self.unread_dms = {}
        if not hasattr(self, 'active_dm_dialogs'):
            self.active_dm_dialogs = {}
        self.active_icq_dialogs = self.active_dm_dialogs

        if p_key not in self.player_dms:
            self.player_dms[p_key] = {
                "player_name": player_name,
                "messages": []
            }
        else:
            # Preserve proper display casing from latest event
            self.player_dms[p_key]["player_name"] = player_name

        msg_entry = {
            "ts": timestamp,
            "direction": direction,
            "type": msg_type,
            "text": text,
            "raw": raw_text
        }

        # Deduplication check: Avoid adding duplicate identical messages to thread history
        existing_msgs = self.player_dms[p_key].get("messages", [])

        def _parse_ts_sec(ts_str):
            try:
                parts = str(ts_str).strip().split(":")
                if len(parts) == 3:
                    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                elif len(parts) == 2:
                    return int(parts[0]) * 60 + int(parts[1])
            except Exception:
                pass
            return None

        def _is_dup(m):
            if m.get("direction") != direction:
                return False
            if (m.get("text") or "").strip() != (text or "").strip():
                return False
            m_ts = m.get("ts")
            if m_ts == timestamp:
                return True
            s1 = _parse_ts_sec(m_ts)
            s2 = _parse_ts_sec(timestamp)
            if s1 is not None and s2 is not None:
                diff = abs(s1 - s2)
                if diff > 43200:
                    diff = 86400 - diff
                if diff <= 10:
                    return True
            else:
                return True
            return False

        is_duplicate = any(_is_dup(m) for m in existing_msgs[-10:])

        if not is_duplicate:
            self.player_dms[p_key]["messages"].append(msg_entry)

        # Check if direct message popup is actively open for this player
        is_dm_open = False
        if p_key in self.active_dm_dialogs and self.active_dm_dialogs[p_key]:
            try:
                dlg = self.active_dm_dialogs[p_key]
                if dlg.isVisible():
                    dlg.refresh_messages()
                    is_dm_open = True
            except Exception:
                pass

        # Only increment unread count for LIVE, NON-HISTORICAL, NON-DUPLICATE incoming messages
        is_history_mode = (getattr(self, 'comms_mode', 'live') == 'history')
        if direction == "in" and not is_historical and not is_history_mode and not is_duplicate:
            if is_dm_open:
                self.unread_dms[p_key] = 0
            else:
                self.unread_dms[p_key] = self.unread_dms.get(p_key, 0) + 1
            self.save_dms_cache()
            if hasattr(self, 'wholist_data'):
                self.update_wholist_gui(self.wholist_data)
            self.update_dm_ui()

    def open_dm_with_player(self, player_name):
        """Opens a Direct Message popup dialog for the specified player, marking queued messages read."""
        if not player_name or player_name == "--":
            return

        p_key = player_name.lower()
        if not hasattr(self, 'active_dm_dialogs'):
            self.active_dm_dialogs = {}
        self.active_icq_dialogs = self.active_dm_dialogs

        # If already open, bring to front, focus and refresh
        if p_key in self.active_dm_dialogs and self.active_dm_dialogs[p_key]:
            try:
                dlg = self.active_dm_dialogs[p_key]
                if dlg.isVisible():
                    dlg.raise_()
                    dlg.activateWindow()
                    dlg.refresh_messages()
                    self.mark_dm_read(player_name)
                    return
            except Exception:
                pass

        # Create new Direct Message popup
        dlg = M59DirectMessageDialog(player_name=player_name, dashboard=self)
        self.active_dm_dialogs[p_key] = dlg
        self.active_icq_dialogs[p_key] = dlg

        # Position bubble next to the player list if possible
        try:
            if hasattr(self, 'who_list_widget') and self.who_list_widget.isVisible():
                pos = self.who_list_widget.mapToGlobal(QPoint(0, 0))
                target_x = max(20, pos.x() - 415)
                target_y = max(50, pos.y() + 20)
                dlg.move(target_x, target_y)
            elif hasattr(self, 'pos'):
                dlg.move(self.pos().x() + 250, self.pos().y() + 120)
        except Exception:
            pass

        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        self.mark_dm_read(player_name)

    def mark_dm_read(self, player_name):
        """Marks unread direct messages as read for a player."""
        if not player_name:
            return
        p_key = player_name.lower()
        if hasattr(self, 'unread_dms') and p_key in self.unread_dms:
            del self.unread_dms[p_key]
            self.save_dms_cache()
            if hasattr(self, 'wholist_data'):
                self.update_wholist_gui(self.wholist_data)
            self.update_dm_ui()

    def mark_all_dms_read(self):
        """Marks all unread direct messages as read and updates UI badges across player list and comms."""
        self.unread_dms = {}
        self.save_dms_cache()
        if hasattr(self, 'wholist_data'):
            self.update_wholist_gui(self.wholist_data)
        self.update_dm_ui()
        if hasattr(self, 'active_floating_chat') and self.active_floating_chat:
            try:
                self.active_floating_chat.reset_unread_private()
            except Exception:
                pass

    def update_dm_ui(self):
        """Refreshes unread DM badge on the Private Messages filter button and active ICQ popups."""
        if not hasattr(self, 'unread_dms'):
            self.unread_dms = {}

        total_unread = sum(self.unread_dms.values())

        # Update Private Messages channel filter button badge in Chat Logger
        if hasattr(self, 'channel_btns') and "private" in self.channel_btns:
            p_btn = self.channel_btns["private"]
            if total_unread > 0:
                p_btn.setText(f"✉️ Private Messages ({total_unread})")
                p_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #581c87;
                        color: #fdf4ff;
                        font-weight: 800;
                        border: 1px solid #c084fc;
                        border-radius: 6px;
                        padding: 6px 12px;
                    }
                    QPushButton:hover {
                        background-color: #7e22ce;
                    }
                """)
            else:
                p_btn.setText("Private Messages")
                p_btn.setStyleSheet("")
                p_btn.style().unpolish(p_btn)
                p_btn.style().polish(p_btn)

        # Refresh any open ICQ dialogs
        if hasattr(self, 'active_icq_dialogs'):
            for p_key, dlg in list(self.active_icq_dialogs.items()):
                try:
                    if dlg and dlg.isVisible():
                        dlg.refresh_messages()
                except Exception:
                    pass

    def save_dms_cache(self):
        """Persists direct messages ledger to disk."""
        try:
            os.makedirs("settings", exist_ok=True)
            safe_n = get_safe_name(self.char_name if self.char_name and self.char_name != "--" else "global")
            cache_file = os.path.join("settings", f"{safe_n}_dms.json")
            data = {
                "player_dms": getattr(self, 'player_dms', {}),
                "unread_dms": getattr(self, 'unread_dms', {})
            }
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[M59-DMS] Error saving DMs cache: {e}", flush=True)

    def load_dms_cache(self, char_name=None):
        """Loads direct messages ledger from disk."""
        try:
            target_char = char_name or (self.char_name if self.char_name != "--" else "global")
            safe_n = get_safe_name(target_char)
            cache_file = os.path.join("settings", f"{safe_n}_dms.json")
            if os.path.exists(cache_file):
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.player_dms = data.get("player_dms", {})
                    self.unread_dms = data.get("unread_dms", {})
                    print(f"[M59-DMS] Loaded {len(self.player_dms)} DM conversation threads ({sum(self.unread_dms.values())} unread).", flush=True)
            else:
                self.player_dms = getattr(self, 'player_dms', {})
                self.unread_dms = getattr(self, 'unread_dms', {})
            self.update_dm_ui()
            if hasattr(self, 'who_list_widget'):
                self.update_wholist_gui(getattr(self, 'wholist_data', {}))
        except Exception as e:
            print(f"[M59-DMS] Error loading DMs cache: {e}", flush=True)

    # ------------------------------------------------------------------
    # Communication Regex Categorizer (Meridian 59 Protocol)
    # ------------------------------------------------------------------
    def categorize_communication_line(self, msg_text):
        """
        Parses raw game text according to Meridian 59 client communication patterns:
        - Tells: 'Kran tells you, ...', 'You tell Kran, ...'
        - Sends: 'Kran sends to you, ...', 'You send to Kran, ...'
        - Guild: '[Guild] Kran: ...', 'Kran sends to guild, ...'
        - Group: '[Group] Kran: ...', 'Kran sends to group, ...'
        - Chat: 'Kran says, ...', 'You say, ...'
        - Yell: 'Kran yells, ...'
        - Broadcast / System: '[Broadcast] ...', '[System] ...', server messages
        - Combat / Improves: Death, kills, improves
        Returns: (channel, is_dm, dm_direction, dm_player, dm_body, dm_type)
        """
        text = msg_text.strip()
        lower = text.lower()

        # 1. Incoming Tell: "Kran tells you, 'Hello'" or "Kran tells you, Hello"
        m = re.match(r"^([A-Za-z0-9_ -]+?)\s+tells\s+you[,\s:]+[\"']?(.*?)[\"']?$", text, re.IGNORECASE)
        if m:
            sender = m.group(1).strip()
            body = m.group(2).strip()
            return ("private", True, "in", sender, body, "tell")

        # 2. Outgoing Tell: "You tell Kran, 'Hello'"
        m = re.match(r"^You\s+tell\s+([A-Za-z0-9_ -]+?)[,\s:]+[\"']?(.*?)[\"']?$", text, re.IGNORECASE)
        if m:
            target = m.group(1).strip()
            body = m.group(2).strip()
            return ("private", True, "out", target, body, "tell")

        # 3. Incoming Send: "Kran sends to you, 'Hello'" or "Kran sends you, 'Hello'"
        m = re.match(r"^([A-Za-z0-9_ -]+?)\s+sends(?:\s+to)?\s+you[,\s:]+[\"']?(.*?)[\"']?$", text, re.IGNORECASE)
        if m:
            sender = m.group(1).strip()
            body = m.group(2).strip()
            return ("private", True, "in", sender, body, "send")

        # 4. Outgoing Send: "You send to Kran, 'Hello'"
        m = re.match(r"^You\s+send(?:\s+to)?\s+([A-Za-z0-9_ -]+?)[,\s:]+[\"']?(.*?)[\"']?$", text, re.IGNORECASE)
        if m:
            target = m.group(1).strip()
            body = m.group(2).strip()
            return ("private", True, "out", target, body, "send")

        # 5. Guild Communications: "[Guild] Kran: Hello" or "Kran sends to guild, 'Hello'"
        m = re.match(r"^\[Guild\]\s*(?:([A-Za-z0-9_ -]+?):)?\s*(.*)$", text, re.IGNORECASE)
        if m:
            return ("guild", False, None, None, None, None)
        m = re.match(r"^([A-Za-z0-9_ -]+?)\s+sends\s+to\s+guild[,\s:]+[\"']?(.*?)[\"']?$", text, re.IGNORECASE)
        if m:
            return ("guild", False, None, None, None, None)

        # 6. Group Communications: "[Group] Kran: Hello" or "Kran sends to group, 'Hello'"
        m = re.match(r"^\[Group\]\s*(?:([A-Za-z0-9_ -]+?):)?\s*(.*)$", text, re.IGNORECASE)
        if m:
            return ("group", False, None, None, None, None)
        m = re.match(r"^([A-Za-z0-9_ -]+?)\s+sends\s+to\s+group[,\s:]+[\"']?(.*?)[\"']?$", text, re.IGNORECASE)
        if m:
            return ("group", False, None, None, None, None)

        # 7. Local / Public Say & Yell: "Kran says, 'Hello'", "You say, 'Hello'", "Kran yells, ..."
        m = re.match(r"^([A-Za-z0-9_ -]+?)\s+(?:says|yells)[,\s:]+[\"']?(.*?)[\"']?$", text, re.IGNORECASE)
        if m or "[Say]" in text or "says," in lower or "yells," in lower:
            return ("chat", False, None, None, None, None)

        # 8. Combat & Kills
        if any(k in lower for k in ["killed", "fatal blow", "collapses", "slain", "strikes", "casts", "inflicts"]):
            return ("combat", False, None, None, None, None)

        # 9. Progression & Improves
        if "improved" in lower or "tougher" in lower or "more knowledgeable" in lower:
            return ("improves", False, None, None, None, None)

        # 10. Default System Broadcast
        return ("system", False, None, None, None, None)

    # ------------------------------------------------------------------
    # Log Processing Pipeline
    # ------------------------------------------------------------------
    def process_log_line(self, line, is_historical=False):
        if not line or not line.strip():
            return

        raw_line = line.strip()

        # Ignore terminal/system internal debug output lines if any slip in
        if any(raw_line.startswith(prefix) for prefix in [
            "[M59-", "DEBUG:", "INFO:", "WARNING:", "ERROR:", "Traceback", "File \"", "[PySide6]"
        ]):
            return

        # Extract timestamp if line has [YYYY-MM-DD HH:MM:SS] or [HH:MM:SS] or [HH:MM]
        ts_match = re.match(r"^\[(?:\d{4}-\d{2}-\d{2}\s+)?(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.*)$", raw_line)
        if ts_match:
            msg_ts = ts_match.group(1)
            msg_text = ts_match.group(2).strip()
        else:
            msg_ts = datetime.now().strftime("%H:%M:%S")
            msg_text = raw_line

        if not msg_text:
            return

        # Prevent duplicate entries (within short timeframe or identical timestamp+text)
        dedup_key = (msg_ts, msg_text)
        now_time = time.time()
        if hasattr(self, 'recent_log_fingerprints'):
            for stored_key, stored_time in list(self.recent_log_fingerprints):
                if stored_key == dedup_key and (now_time - stored_time) < 3.0:
                    return
                if stored_key[1] == msg_text and (now_time - stored_time) < 1.0:
                    return
            self.recent_log_fingerprints.append((dedup_key, now_time))
        else:
            self.recent_log_fingerprints = deque([(dedup_key, now_time)], maxlen=250)

        # 0. Check Spell Trance Steering & Fizzle Interception
        if not is_historical and hasattr(self, 'pending_spell_trance') and self.pending_spell_trance:
            lower_msg = msg_text.lower()
            pending = self.pending_spell_trance

            # Check for spell failure / error / fizzle / interruption
            fail_keywords = [
                "fizzles", "lose your concentration", "interrupted", "fail to cast", 
                "cannot cast", "there is no spell", "don't know", "don't have", 
                "you must be", "not enough mana", "no spell"
            ]
            if any(fizz in lower_msg for fizz in fail_keywords):
                print(f"[M59-SPELL] Spell '{pending.get('spell_name')}' failed/fizzled: {msg_text}", flush=True)
                pending["fizzled"] = True
                self.pending_spell_trance = None
            elif "focus your whole will on casting" in lower_msg:
                # Trance confirmation detected
                if not pending.get("completed") and not pending.get("fizzled"):
                    pending["trance_entered"] = True
                    pending["completed"] = True
                    steer_cmd = pending.get("steer_command")
                    target = pending.get("target_hwnd")
                    print(f"[M59-SPELL] Trance confirmed in chat log! Executing steer payload -> {steer_cmd}", flush=True)

                    def _send_immediate_steer():
                        time.sleep(0.3)
                        if target and steer_cmd:
                            send_chat_command(target, steer_cmd)
                        self.pending_spell_trance = None

                    threading.Thread(target=_send_immediate_steer, daemon=True).start()

        # 0. Check SpellManager for Spell Trance & Reagent Usage
        if hasattr(self, 'spell_manager') and self.spell_manager:
            spell_ev = self.spell_manager.process_line(msg_text, is_historical=is_historical)
            if spell_ev:
                if spell_ev.get("type") == "SPELL_CAST_SUCCESS":
                    if hasattr(self, 'update_reagents_ui'):
                        self.update_reagents_ui()
                elif spell_ev.get("type") == "TRANCE_START":
                    pass

        # 0. Check Bank updates
        if hasattr(self, 'bank_manager') and self.bank_manager.process_line(msg_text):
            self.update_bank_ui()

        # 1. Check SessionTracker for Improves
        gain = self.tracker.process_line(msg_text)
        if gain:
            found_row = -1
            for r in range(self.imp_table.rowCount()):
                item = self.imp_table.item(r, 0)
                if item and item.text().lower() == gain['name'].lower():
                    found_row = r
                    break

            if found_row != -1:
                self.imp_table.setItem(found_row, 1, QTableWidgetItem(str(gain['count'])))
                self.imp_table.setItem(found_row, 2, QTableWidgetItem(gain['delta']))
                self.imp_table.setItem(found_row, 3, QTableWidgetItem(msg_ts))
            else:
                row = self.imp_table.rowCount()
                self.imp_table.insertRow(row)
                self.imp_table.setItem(row, 0, QTableWidgetItem(gain['name']))
                self.imp_table.setItem(row, 1, QTableWidgetItem(str(gain['count'])))
                self.imp_table.setItem(row, 2, QTableWidgetItem(gain['delta']))
                self.imp_table.setItem(row, 3, QTableWidgetItem(msg_ts))

            self.improves_history.append(msg_text)
            self.imp_count_badge.setText(f"{len(self.improves_history)} Gains")
            if hasattr(self, 'dock_improves_lbl') and self.dock_improves_lbl:
                self.dock_improves_lbl.setText(str(len(self.improves_history)))
            self.add_log_entry(msg_ts, "improves", msg_text, is_historical=is_historical)

            # Update progression knowledge cache
            skill_k = gain['name'].lower()
            if skill_k != "hit points":
                cur_val = self.knowledge_cache.get(skill_k, 0)
                self.knowledge_cache[skill_k] = min(99, max(cur_val + 1, 1))
                self.save_knowledge_cache()
                self.update_progression_ui()
            return

        # 2. Check CombatMonitor for Kills / PK Alerts
        kill = self.combat_monitor.process_line(msg_text)
        if kill:
            if kill.get("type") == "PK_ALERT":
                if not is_historical and getattr(self, 'comms_mode', 'live') == 'live':
                    self.trigger_pk_alert()
            elif kill.get("type") == "KILL":
                category = kill['category']
                victim = kill['name']

                if category not in self.session_kills:
                    self.session_kills[category] = {}
                self.session_kills[category][victim] = self.session_kills[category].get(victim, 0) + 1
                session_count = self.session_kills[category][victim]

                found_row = -1
                for r in range(self.kill_table.rowCount()):
                    item = self.kill_table.item(r, 0)
                    if item and item.text().lower() == victim.lower():
                        found_row = r
                        break

                if found_row != -1:
                    self.kill_table.setItem(found_row, 1, QTableWidgetItem(category.capitalize()))
                    self.kill_table.setItem(found_row, 2, QTableWidgetItem(str(session_count)))
                    self.kill_table.setItem(found_row, 3, QTableWidgetItem(msg_ts))
                else:
                    row = self.kill_table.rowCount()
                    self.kill_table.insertRow(row)
                    self.kill_table.setItem(row, 0, QTableWidgetItem(victim))
                    self.kill_table.setItem(row, 1, QTableWidgetItem(category.capitalize()))
                    self.kill_table.setItem(row, 2, QTableWidgetItem(str(session_count)))
                    self.kill_table.setItem(row, 3, QTableWidgetItem(msg_ts))

                self.kills_history.append(msg_text)
                total_session_kills = sum(sum(c.values()) for c in self.session_kills.values())
                self.kill_count_badge.setText(f"{total_session_kills} Kills")
                self.add_log_entry(msg_ts, "combat", msg_text, is_historical=is_historical)

                if hasattr(self, 'update_killbook_ui'):
                    self.update_killbook_ui()
                return

        # 3. Categorize line using communication parsing engine
        channel, is_dm, dm_dir, dm_player, dm_body, dm_type = self.categorize_communication_line(msg_text)

        # If direct message (tell or send), record in player DM ledger
        if is_dm and dm_player:
            self.record_direct_message(msg_ts, dm_player, dm_body, msg_text, direction=dm_dir, msg_type=dm_type, is_historical=is_historical)

        self.add_log_entry(msg_ts, channel, msg_text, is_historical=is_historical)

    def add_log_entry(self, timestamp, channel, text, is_historical=False):
        entry = {"ts": timestamp, "channel": channel, "text": text}
        self.chat_logs.append(entry)

        is_history_mode = (getattr(self, 'comms_mode', 'live') == 'history')
        if channel in ("private", "guild", "group") and not is_historical and not is_history_mode:
            self.play_tell_alert()

        self.render_chat_line(entry)

        if hasattr(self, 'active_floating_chat') and self.active_floating_chat:
            try:
                self.active_floating_chat.append_entry(entry, is_historical=(is_historical or is_history_mode))
            except Exception:
                pass
                pass

    def render_chat_line(self, entry):
        ch = entry.get('channel', 'system')
        color = "#e2e8f0"
        if ch == "improves":
            color = "#34d399"
        elif ch == "combat":
            color = "#f87171"
        elif ch == "private":
            color = "#c084fc"
        elif ch == "guild":
            color = "#a855f7"
        elif ch == "group":
            color = "#38bdf8"
        elif ch == "chat":
            color = "#60a5fa"
        elif ch == "system":
            color = "#fbbf24"

        fs = getattr(self, 'font_settings', {}).get("chat_logger", 13)
        ts_fs = max(9, fs - 2)
        raw_text = entry.get('text', '')
        text_escaped = raw_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        line_html = f"<div style='margin-bottom: 4px;'><span style='color: #64748b; font-size: {ts_fs}px;'>[{entry['ts']}]</span> <span style='color: {color}; font-weight: 600; font-size: {fs}px;'>{text_escaped}</span></div>"

        # Check filter matching: "private" filter matches private, guild, and group
        matches_channel = False
        if self.active_channel == "all":
            matches_channel = True
        elif self.active_channel == "private" and ch in ("private", "guild", "group"):
            matches_channel = True
        elif self.active_channel == ch:
            matches_channel = True

        if matches_channel:
            query = self.chat_search.text().lower()
            if not query or query in raw_text.lower():
                self.chat_stream_view.append(line_html)
                self.chat_stream_view.moveCursor(QTextCursor.End)

    def set_chat_channel_filter(self, channel_id):
        self.active_channel = channel_id
        for cid, btn in self.channel_btns.items():
            btn.setProperty("active", "true" if cid == channel_id else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        if channel_id == "private":
            self.mark_all_dms_read()
        self.filter_chat_stream()

    def filter_chat_stream(self):
        self.chat_stream_view.clear()
        query = self.chat_search.text().lower()
        for entry in self.chat_logs:
            ch = entry.get('channel', 'system')
            matches_channel = False
            if self.active_channel == "all":
                matches_channel = True
            elif self.active_channel == "private" and ch in ("private", "guild", "group"):
                matches_channel = True
            elif self.active_channel == ch:
                matches_channel = True

            if matches_channel:
                if not query or query in entry['text'].lower():
                    self.render_chat_line(entry)

    def parse_chat_input(self):
        text = self.chat_input.text().strip()
        if not text:
            return
        self.process_log_line(text)
        self.chat_input.clear()

    # ------------------------------------------------------------------
    # Historical Log Files Loader & Importer
    # ------------------------------------------------------------------
    def refresh_historical_logs_list(self):
        self.hist_log_list.clear()
        if not os.path.exists("settings"):
            os.makedirs("settings", exist_ok=True)

        files = [f for f in os.listdir("settings") if f.endswith(".log") and "debug" not in f.lower()]
        files.sort(key=lambda x: os.path.getmtime(os.path.join("settings", x)), reverse=True)

        for f in files:
            self.hist_log_list.addItem(f)

    def load_selected_historical_log(self, item):
        filename = item.text()
        file_path = os.path.join("settings", filename)
        if os.path.exists(file_path):
            self.comms_mode = "history"
            self.mode_btn.setText(f"📂 HISTORY: {filename[:12]}..")
            self.mode_btn.setStyleSheet("color: #60a5fa; font-weight: 800;")
            self.chat_logs.clear()
            self.chat_stream_view.clear()

            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.strip():
                        self.process_log_line(line.strip(), is_historical=True)

    def return_to_live_stream(self):
        self.comms_mode = "live"
        self.mode_btn.setText("🟢 LIVE STREAM")
        self.mode_btn.setStyleSheet("color: #94a3b8; font-weight: 800;")
        self.chat_logs.clear()
        self.chat_stream_view.clear()
        if hasattr(self, 'active_floating_chat') and self.active_floating_chat:
            try:
                self.active_floating_chat.filter_chat()
            except Exception:
                pass

    def import_log_file_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Import Meridian 59 Log File", "", "Log Files (*.log *.txt)")
        if file_path and os.path.exists(file_path):
            self.comms_mode = "history"
            self.mode_btn.setText("📂 IMPORTED")
            self.mode_btn.setStyleSheet("color: #c084fc; font-weight: 800;")
            self.chat_logs.clear()
            self.chat_stream_view.clear()

            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.strip():
                        self.process_log_line(line.strip(), is_historical=True)

    # ----------------------------------------------------------------------
    # GPS Navigation Engine & UI Handlers
    # ----------------------------------------------------------------------
    def create_room_completer(self, parent_widget):
        if not hasattr(self, 'gps_manager') or not self.gps_manager:
            return None
        options = self.gps_manager.get_room_options()
        self.gps_room_options = options
        display_names = [opt['display'] for opt in options]

        completer = QCompleter(display_names, parent_widget)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setMaxVisibleItems(8)

        popup = completer.popup()
        popup.setStyleSheet("""
            QAbstractItemView {
                background-color: #0b1120;
                color: #f8fafc;
                border: 1px solid #334155;
                selection-background-color: #2563eb;
                selection-color: #ffffff;
                font-size: 11px;
                padding: 3px;
            }
        """)
        return completer

    def get_target_rid_from_text(self, text):
        text = (text or "").strip()
        if not text or not hasattr(self, 'gps_room_options'):
            return None
        for opt in self.gps_room_options:
            if opt['display'].lower() == text.lower():
                return opt['rid']
        for opt in self.gps_room_options:
            if opt['name'].lower() == text.lower():
                return opt['rid']
        for opt in self.gps_room_options:
            if text.lower() in opt['display'].lower():
                return opt['rid']
        return self.gps_manager.resolve_name_to_rid(text)

    def refresh_dock_layouts(self):
        if hasattr(self, 'dock_sub_grid') and self.dock_sub_grid:
            self.dock_sub_grid.refresh_layout()
        if hasattr(self, 'dock_grid') and self.dock_grid:
            self.dock_grid.refresh_layout()

    def toggle_navigation(self, source_text=None):
        if hasattr(self, 'gps_manager') and self.gps_manager and (self.gps_manager.current_path or self.gps_manager.current_destination_rid):
            self.stop_navigation()
        else:
            self.start_navigation(source_text=source_text)

    def start_navigation(self, target_rid=None, source_text=None):
        if target_rid is None and source_text:
            target_rid = self.get_target_rid_from_text(source_text)

        if target_rid is None and hasattr(self, 'gps_main_search'):
            target_rid = self.get_target_rid_from_text(self.gps_main_search.text())

        if target_rid is None and hasattr(self, 'dock_gps_search'):
            target_rid = self.get_target_rid_from_text(self.dock_gps_search.text())

        if not target_rid:
            if hasattr(self, 'gps_instruction_lbl'):
                self.gps_instruction_lbl.setText("Please enter or select a valid destination.")
            if hasattr(self, 'dock_gps_dir_lbl'):
                self.dock_gps_dir_lbl.setText("INVALID DESTINATION")
                self.dock_gps_dir_lbl.setStyleSheet("font-size: 11px; font-weight: 800; color: #ef4444;")
            if hasattr(self, 'dock_gps_detail_lbl'):
                self.dock_gps_detail_lbl.setText("Room not found in GPS dataset!")
                self.dock_gps_detail_lbl.setStyleSheet("font-size: 10px; font-weight: 600; color: #f87171;")
            if hasattr(self, 'dock_gps_status_lbl'):
                self.dock_gps_status_lbl.setText("ERROR")
            self.refresh_dock_layouts()
            return

        cur_room = getattr(self, 'current_room_name', 'Unknown Location')
        start_rid = self.gps_manager.resolve_name_to_rid(cur_room)

        if not start_rid:
            if hasattr(self, 'gps_instruction_lbl'):
                self.gps_instruction_lbl.setText(f"Cannot resolve current location ({cur_room})")
            if hasattr(self, 'dock_gps_dir_lbl'):
                self.dock_gps_dir_lbl.setText("UNKNOWN LOCATION")
                self.dock_gps_dir_lbl.setStyleSheet("font-size: 11px; font-weight: 800; color: #f59e0b;")
            if hasattr(self, 'dock_gps_detail_lbl'):
                self.dock_gps_detail_lbl.setText(f"Location: {cur_room}")
                self.dock_gps_detail_lbl.setStyleSheet("font-size: 10px; font-weight: 600; color: #fbbf24;")
            if hasattr(self, 'dock_gps_status_lbl'):
                self.dock_gps_status_lbl.setText("ERROR")
            self.refresh_dock_layouts()
            return

        path = self.gps_manager.find_path(start_rid, target_rid)
        if path is not None:
            self.gps_manager.current_path = path
            self.gps_manager.current_step_index = 0
            self.gps_manager.current_destination_rid = target_rid

            target_name = self.gps_manager.dataset.get(target_rid, {}).get('name', 'Target')
            if hasattr(self, 'gps_main_target_lbl'):
                self.gps_main_target_lbl.setText(f"🎯 Target: {target_name}")
            if hasattr(self, 'dock_gps_target_lbl'):
                self.dock_gps_target_lbl.setText(f"🎯 Target: {target_name}")

            if hasattr(self, 'gps_main_btn') and self.gps_main_btn:
                self.gps_main_btn.setText("⏹")
                self.gps_main_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #dc2626;
                        color: #ffffff;
                        border: none;
                        border-radius: 4px;
                        font-size: 12px;
                        font-weight: 800;
                    }
                    QPushButton:hover {
                        background-color: #ef4444;
                    }
                """)
            if hasattr(self, 'dock_gps_toggle_btn') and self.dock_gps_toggle_btn:
                self.dock_gps_toggle_btn.setText("⏹")
                self.dock_gps_toggle_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #dc2626;
                        color: #ffffff;
                        border: none;
                        border-radius: 3px;
                        font-size: 10px;
                        font-weight: 800;
                    }
                    QPushButton:hover {
                        background-color: #ef4444;
                    }
                """)

            self.update_gps_navigation_ui()
        else:
            if hasattr(self, 'gps_instruction_lbl'):
                self.gps_instruction_lbl.setText("NO PATH FOUND in dataset!")
            if hasattr(self, 'dock_gps_dir_lbl'):
                self.dock_gps_dir_lbl.setText("NO PATH FOUND")
                self.dock_gps_dir_lbl.setStyleSheet("font-size: 11px; font-weight: 800; color: #ef4444;")
            if hasattr(self, 'dock_gps_detail_lbl'):
                self.dock_gps_detail_lbl.setText("Destination not reachable")
                self.dock_gps_detail_lbl.setStyleSheet("font-size: 10px; font-weight: 600; color: #f87171;")
            if hasattr(self, 'dock_gps_status_lbl'):
                self.dock_gps_status_lbl.setText("NO PATH")
            self.refresh_dock_layouts()

    def stop_navigation(self):
        if hasattr(self, 'gps_manager') and self.gps_manager:
            self.gps_manager.current_destination_rid = None
            self.gps_manager.current_path = []

        if hasattr(self, 'gps_main_btn') and self.gps_main_btn:
            self.gps_main_btn.setText("▶")
            self.gps_main_btn.setStyleSheet("""
                QPushButton {
                    background-color: #16a34a;
                    color: #ffffff;
                    border: none;
                    border-radius: 4px;
                    font-size: 12px;
                    font-weight: 800;
                }
                QPushButton:hover {
                    background-color: #22c55e;
                }
            """)
        if hasattr(self, 'dock_gps_toggle_btn') and self.dock_gps_toggle_btn:
            self.dock_gps_toggle_btn.setText("▶")
            self.dock_gps_toggle_btn.setStyleSheet("""
                QPushButton {
                    background-color: #16a34a;
                    color: #ffffff;
                    border: none;
                    border-radius: 3px;
                    font-size: 10px;
                    font-weight: 800;
                }
                QPushButton:hover {
                    background-color: #22c55e;
                }
            """)

        if hasattr(self, 'gps_instruction_lbl'):
            self.gps_instruction_lbl.setText("Select a destination to begin navigation...")
            self.gps_instruction_lbl.setStyleSheet("font-size: 12px; font-weight: 700; color: #f1f5f9;")
        if hasattr(self, 'gps_main_target_lbl'):
            self.gps_main_target_lbl.setText("🎯 Target: None")
        if hasattr(self, 'dock_gps_target_lbl'):
            self.dock_gps_target_lbl.setText("🎯 Target: None")
        if hasattr(self, 'dock_gps_dir_lbl'):
            self.dock_gps_dir_lbl.setText("SELECT DESTINATION")
            self.dock_gps_dir_lbl.setStyleSheet("font-size: 10px; font-weight: 800; color: #38bdf8;")
        if hasattr(self, 'dock_gps_detail_lbl'):
            self.dock_gps_detail_lbl.setText("Enter destination & press ▶")
            self.dock_gps_detail_lbl.setStyleSheet("font-size: 9px; font-weight: 600; color: #94a3b8;")
        if hasattr(self, 'dock_gps_status_lbl'):
            self.dock_gps_status_lbl.setText("READY")
        if hasattr(self, 'gps_route_list'):
            self.gps_route_list.clear()
            item = QListWidgetItem(" No active route. Enter a destination above to preview trip steps.")
            item.setForeground(QColor("#64748b"))
            self.gps_route_list.addItem(item)
            self.gps_route_list.show()
        if hasattr(self, 'gps_route_title_lbl'):
            self.gps_route_title_lbl.show()

        self.refresh_dock_layouts()

    def update_gps_navigation_ui(self):
        if not hasattr(self, 'gps_manager') or not self.gps_manager:
            return

        path = self.gps_manager.current_path
        step_idx = self.gps_manager.current_step_index

        if hasattr(self, 'gps_route_list'):
            self.gps_route_list.clear()

        if not path:
            if hasattr(self, 'gps_route_list'):
                self.gps_route_list.clear()
                item = QListWidgetItem(" No active route. Enter a destination above to preview trip steps.")
                item.setForeground(QColor("#64748b"))
                self.gps_route_list.addItem(item)
                self.gps_route_list.show()
            if hasattr(self, 'gps_route_title_lbl'):
                self.gps_route_title_lbl.show()

            msg = "ARRIVED! You have reached your destination."
            if hasattr(self, 'gps_instruction_lbl'):
                self.gps_instruction_lbl.setText(msg)
                self.gps_instruction_lbl.setStyleSheet("font-size: 12px; font-weight: 800; color: #4ade80;")
            if hasattr(self, 'dock_gps_dir_lbl'):
                self.dock_gps_dir_lbl.setText("🏁 ARRIVED!")
                self.dock_gps_dir_lbl.setStyleSheet("font-size: 10px; font-weight: 800; color: #4ade80;")
            if hasattr(self, 'dock_gps_detail_lbl'):
                self.dock_gps_detail_lbl.setText("Destination reached.")
                self.dock_gps_detail_lbl.setStyleSheet("font-size: 9px; font-weight: 600; color: #86efac;")
            if hasattr(self, 'dock_gps_status_lbl'):
                self.dock_gps_status_lbl.setText("ARRIVED")

            if hasattr(self, 'gps_main_btn') and self.gps_main_btn:
                self.gps_main_btn.setText("▶")
                self.gps_main_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #16a34a;
                        color: #ffffff;
                        border: none;
                        border-radius: 4px;
                        font-size: 12px;
                        font-weight: 800;
                    }
                    QPushButton:hover {
                        background-color: #22c55e;
                    }
                """)
            if hasattr(self, 'dock_gps_toggle_btn') and self.dock_gps_toggle_btn:
                self.dock_gps_toggle_btn.setText("▶")
                self.dock_gps_toggle_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #16a34a;
                        color: #ffffff;
                        border: none;
                        border-radius: 3px;
                        font-size: 10px;
                        font-weight: 800;
                    }
                    QPushButton:hover {
                        background-color: #22c55e;
                    }
                """)

            self.refresh_dock_layouts()
            return

        if step_idx >= len(path):
            self.gps_manager.current_path = []
            self.update_gps_navigation_ui()
            return

        from_rid, exit_info = path[step_idx]
        total_steps = len(path)

        arrival_pos = None
        if step_idx == 0:
            arrival_pos = self.gps_manager.dataset.get(from_rid, {}).get('teleport')
        else:
            _, prev_exit = path[step_idx-1]
            arrival_pos = prev_exit.get('to_pos')

        instr = self.gps_manager.get_friendly_instruction(
            from_rid, exit_info, step=step_idx+1, total=total_steps, arrival_pos=arrival_pos
        )

        lines = [line.strip() for line in instr.split("\n") if line.strip()]
        step_tag = lines[0] if len(lines) > 0 else f"[{step_idx+1}/{total_steps}]"
        dir_str = lines[1] if len(lines) > 1 else lines[0]
        action_str = lines[2] if len(lines) > 2 else ""

        if hasattr(self, 'gps_instruction_lbl'):
            self.gps_instruction_lbl.setText(instr)
            self.gps_instruction_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #38bdf8;")

        if hasattr(self, 'dock_gps_dir_lbl'):
            self.dock_gps_dir_lbl.setText(f"{dir_str}  {step_tag}")
            self.dock_gps_dir_lbl.setStyleSheet("font-size: 11px; font-weight: 800; color: #38bdf8;")

        if hasattr(self, 'dock_gps_detail_lbl'):
            self.dock_gps_detail_lbl.setText(action_str if action_str else "Proceed to exit")
            self.dock_gps_detail_lbl.setStyleSheet("font-size: 10px; font-weight: 600; color: #f1f5f9;")

        if hasattr(self, 'dock_gps_status_lbl'):
            self.dock_gps_status_lbl.setText(f"STEP {step_idx+1}/{total_steps}")

        self.refresh_dock_layouts()

        if hasattr(self, 'gps_route_list'):
            self.gps_route_list.show()
            if hasattr(self, 'gps_route_title_lbl'):
                self.gps_route_title_lbl.show()
            for i, (rid, info) in enumerate(path):
                prefix = ">> " if i == step_idx else "   "
                room_name = self.gps_manager.dataset.get(rid, {}).get('name', 'Unknown')
                dest_name = self.gps_manager.dataset.get(info['to_rid'], {}).get('name', 'Unknown')
                item_txt = f"{prefix}{i+1}. {room_name} -> {dest_name}"
                item = QListWidgetItem(item_txt)
                if i == step_idx:
                    item.setForeground(QColor("#38bdf8"))
                    item.setBackground(QColor("#0f172a"))
                else:
                    item.setForeground(QColor("#cbd5e1"))
                self.gps_route_list.addItem(item)
            self.gps_route_list.setCurrentRow(step_idx)

    def load_pvp_icons(self):
        """Loads and crops PvP status indicator icons from imgs directory at a compact size."""
        self.pvp_icons = {}
        icon_files = {
            "Standard PVP": "open_pvp.jpg",
            "Guild PVP Only": "guild_combat.jpg",
            "Safe (No PVP)": "no_pvp.jpg",
            "Safe Logoff": "safe_to_log.jpg"
        }
        size = 20
        for key, filename in icon_files.items():
            path = resource_path(os.path.join("imgs", filename))
            if os.path.exists(path):
                try:
                    pix = QPixmap(path)
                    if not pix.isNull():
                        w = pix.width()
                        h = pix.height()
                        min_dim = min(w, h)
                        cropped = pix.copy((w - min_dim) // 2, (h - min_dim) // 2, min_dim, min_dim)
                        scaled_pix = cropped.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                        self.pvp_icons[key] = scaled_pix
                except Exception as ex:
                    print(f"[M59-PVP] Failed loading icon {filename}: {ex}", flush=True)

    def update_pvp_status_ui(self, room_name):
        """Updates PvP indicator icons and short, clear alt tooltips in dock panel and main GPS view."""
        if not hasattr(self, 'gps_manager') or not self.gps_manager or not hasattr(self, 'pvp_icons'):
            return

        rid = self.gps_manager.resolve_name_to_rid(room_name) if room_name and room_name != "Unknown Location" else None
        room_data = self.gps_manager.dataset.get(rid, {}) if rid else {}
        pvp_status = room_data.get("pvp_status", "Standard PVP") if rid else "Unknown"
        raw_flags = room_data.get("raw_flags", "") if rid else ""

        if pvp_status == "Safe (No PVP)":
            status_key = "Safe (No PVP)"
            tooltip_text = "Safe: No PvP Allowed"
        elif pvp_status == "Guild PVP Only":
            status_key = "Guild PVP Only"
            tooltip_text = "Guild PvP Only"
        elif pvp_status == "Arena (No Death Penalty)":
            status_key = "Safe (No PVP)"
            tooltip_text = "Arena: No Death Penalty"
        elif rid:
            status_key = "Standard PVP"
            tooltip_text = "Open PvP: Combat Allowed"
        else:
            status_key = None
            tooltip_text = "Location Unknown"

        # Update Dock Panel Icons
        if hasattr(self, 'dock_pvp_icon_1'):
            if status_key and status_key in self.pvp_icons:
                self.dock_pvp_icon_1.setPixmap(self.pvp_icons[status_key])
                self.dock_pvp_icon_1.setToolTip(tooltip_text)
                self.dock_pvp_icon_1.show()
            else:
                self.dock_pvp_icon_1.clear()
                self.dock_pvp_icon_1.hide()

        if hasattr(self, 'dock_pvp_icon_2'):
            if "ROOM_SAFELOGOFF" in raw_flags and "Safe Logoff" in self.pvp_icons:
                self.dock_pvp_icon_2.setPixmap(self.pvp_icons["Safe Logoff"])
                self.dock_pvp_icon_2.setToolTip("Safe Logoff: Instant Safe Logout")
                self.dock_pvp_icon_2.show()
            else:
                self.dock_pvp_icon_2.clear()
                self.dock_pvp_icon_2.hide()

        # Update Main View Icons
        if hasattr(self, 'main_pvp_icon_1'):
            if status_key and status_key in self.pvp_icons:
                self.main_pvp_icon_1.setPixmap(self.pvp_icons[status_key])
                self.main_pvp_icon_1.setToolTip(tooltip_text)
                self.main_pvp_icon_1.show()
            else:
                self.main_pvp_icon_1.clear()
                self.main_pvp_icon_1.hide()

        if hasattr(self, 'main_pvp_icon_2'):
            if "ROOM_SAFELOGOFF" in raw_flags and "Safe Logoff" in self.pvp_icons:
                self.main_pvp_icon_2.setPixmap(self.pvp_icons["Safe Logoff"])
                self.main_pvp_icon_2.setToolTip("Safe Logoff: Instant Safe Logout")
                self.main_pvp_icon_2.show()
            else:
                self.main_pvp_icon_2.clear()
                self.main_pvp_icon_2.hide()

    def update_gps_room(self, room_name):
        if not room_name or room_name == "Unknown Location":
            self.update_pvp_status_ui("Unknown Location")
            return
        self.current_room_name = room_name

        if hasattr(self, 'gps_main_loc_lbl'):
            self.gps_main_loc_lbl.setText(f"📍 CURRENT: {room_name}")
        if hasattr(self, 'dock_gps_loc_lbl'):
            self.dock_gps_loc_lbl.setText(f"📍 {room_name}")

        self.update_pvp_status_ui(room_name)

        if hasattr(self, 'gps_manager') and self.gps_manager:
            was_t, msg = self.gps_manager.process_room_update(room_name)
            self.monitor_gps_navigation(room_name)

    def monitor_gps_navigation(self, current_room_name):
        if not hasattr(self, 'gps_manager') or not self.gps_manager or not self.gps_manager.current_destination_rid:
            return

        if not self.gps_manager.current_path:
            dest_rid = self.gps_manager.current_destination_rid
            dest_name = self.gps_manager.dataset.get(dest_rid, {}).get('name', '')
            if current_room_name.lower() != dest_name.lower():
                self.stop_navigation()
            return

        path = self.gps_manager.current_path
        step_idx = self.gps_manager.current_step_index

        if step_idx >= len(path):
            self.gps_manager.current_path = []
            self.update_gps_navigation_ui()
            return

        # Check if we moved into next room in route
        next_room_rid = path[step_idx][1]['to_rid']
        next_room_name = self.gps_manager.dataset.get(next_room_rid, {}).get('name', '')

        if current_room_name.lower() == next_room_name.lower():
            self.gps_manager.current_step_index += 1
            if self.gps_manager.current_step_index >= len(path):
                self.gps_manager.current_path = []
            self.update_gps_navigation_ui()
            return

        # Check if still in from_room
        curr_step_from_rid = path[step_idx][0]
        curr_step_from_name = self.gps_manager.dataset.get(curr_step_from_rid, {}).get('name', '')
        if current_room_name.lower() == curr_step_from_name.lower():
            return

        # Check for skipped steps or shortcuts taken
        for i in range(step_idx + 1, len(path)):
            check_rid = path[i][1]['to_rid']
            if current_room_name.lower() == self.gps_manager.dataset.get(check_rid, {}).get('name', '').lower():
                self.gps_manager.current_step_index = i + 1
                if self.gps_manager.current_step_index >= len(path):
                    self.gps_manager.current_path = []
                self.update_gps_navigation_ui()
                return

        # Off-track detection -> recalculate path automatically
        start_rid = self.gps_manager.resolve_name_to_rid(current_room_name)
        if start_rid:
            recalculated = self.gps_manager.find_path(start_rid, self.gps_manager.current_destination_rid)
            if recalculated is not None:
                self.gps_manager.current_path = recalculated
                self.gps_manager.current_step_index = 0
                self.update_gps_navigation_ui()


# ----------------------------------------------------------------------
# Application Entry Point & Process Lifecycle Safety
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import signal
    import atexit
    import tempfile
    from PySide6.QtCore import QLockFile

    # 1. Single Instance Lock Enforcement to prevent duplicate rogue background processes
    lock_path = os.path.join(tempfile.gettempdir(), "m59_companion_v3.lock")
    lock_file = QLockFile(lock_path)
    lock_file.setStaleLockTime(3000)  # considers lock stale if previous instance crashed >3s ago
    if not lock_file.tryLock(100):
        print("[M59-INIT] Another instance of Meridian 59 Companion is already active. Exiting cleanly.", flush=True)
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Set Window / App Icon
    icon_path = resource_path(os.path.join("imgs", "m59comp.ico"))
    if not os.path.exists(icon_path):
        icon_path = resource_path(os.path.join("imgs", "m59comp.png"))
    if not os.path.exists(icon_path):
        icon_path = resource_path(os.path.join("imgs", "m59comp.jpg"))
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = M59CompanionApp()

    # 2. Comprehensive Termination Cleanup Handler
    _cleaned_up = False
    def global_cleanup():
        global _cleaned_up
        if _cleaned_up:
            return
        _cleaned_up = True
        try:
            cleanup_all_appbars()
        except Exception:
            pass
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass
        try:
            if 'window' in locals() and window:
                window.close()
        except Exception:
            pass
        try:
            if 'lock_file' in locals() and lock_file and lock_file.isLocked():
                lock_file.unlock()
        except Exception:
            pass
        # Force terminate any remaining background threads/sockets immediately
        os._exit(0)

    app.aboutToQuit.connect(global_cleanup)
    atexit.register(global_cleanup)

    # 3. OS Signal Interception (SIGINT / SIGTERM)
    try:
        signal.signal(signal.SIGINT, lambda sig, frame: global_cleanup())
        signal.signal(signal.SIGTERM, lambda sig, frame: global_cleanup())
    except Exception:
        pass

    window.show()

    # Launch splash screen overlay during process scan
    window.show_splash_overlay("searching")

    ret_code = app.exec()
    global_cleanup()
    sys.exit(ret_code)
