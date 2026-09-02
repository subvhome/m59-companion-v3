# -*- coding: utf-8 -*-
"""
M59 Floating Overlays & Docked HUD Widgets
Includes:
- InstantToolTipFilter
- PKFrame (PK Alert HUD Overlay)
- QtFloatingHotkeyButton (Dockable Macro Button)
- QtFloatingEludeBar (Buff/Elude Timer HUD)
- CompactMorphComboBox (Creature Transformation Dropdown)
- QtFloatingMorphBar (Creature Transformation Bar)
- QtFloatingChatBox (Translucent In-Game Chat HUD)
- M59ToastNotification (Desktop Notification Toasts)
"""

import sys
import os
import time
import json
import csv
import re
import math
import ctypes
import threading
from datetime import datetime

try:
    import win32gui
    import win32con
    import win32process
except Exception:
    win32gui = None
    win32con = None
    win32process = None

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QFrame, QTextEdit, QComboBox, QDialog, QAbstractItemView, QMenu,
    QToolTip, QStyleOptionComboBox, QStyle, QSizeGrip
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QPoint, QRect, QEvent, QSize
from PySide6.QtGui import (
    QFont, QIcon, QColor, QTextCursor, QPixmap, QImage, QPainter, QPen, QBrush,
    QLinearGradient, QCursor, QGuiApplication
)

from m59_utils import GAME_EXE, get_safe_name, find_game_hwnd, resource_path
try:
    from m59_vault import send_chat_command
except Exception:
    send_chat_command = None
from m59_audio import play_audio_file
from m59_logging import get_logger

class InstantToolTipFilter(QObject):
    """Global event filter that causes tooltips to display INSTANTLY (0ms delay) on mouse hover."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._active_widget = None
        self._active_tip = None

    def eventFilter(self, obj, event):
        et = event.type()

        # Immediate ToolTip display on Enter or ToolTip event
        if et == QEvent.Type.Enter or et == QEvent.Type.ToolTip:
            if isinstance(obj, QWidget):
                tip = obj.toolTip()
                if tip:
                    self._active_widget = obj
                    self._active_tip = tip
                    QToolTip.showText(QCursor.pos(), tip, obj)
                    if et == QEvent.Type.ToolTip:
                        return True  # Suppress default Qt 700ms delayed tooltip timer
        elif et == QEvent.Type.Leave:
            if self._active_widget == obj:
                self._active_widget = None
                self._active_tip = None
                QToolTip.hideText()
        elif et == QEvent.Type.MouseMove:
            if isinstance(obj, QWidget):
                # Item-based widgets (QTableWidget, QListWidget, QTreeWidget, QComboBox)
                if hasattr(obj, 'itemAt'):
                    pos = event.pos() if hasattr(event, 'pos') else QPoint(0, 0)
                    item = obj.itemAt(pos)
                    if item and hasattr(item, 'toolTip'):
                        itip = item.toolTip()
                        if itip and itip != self._active_tip:
                            self._active_widget = obj
                            self._active_tip = itip
                            QToolTip.showText(QCursor.pos(), itip, obj)
                elif obj != self._active_widget and hasattr(obj, 'toolTip'):
                    tip = obj.toolTip()
                    if tip and tip != self._active_tip:
                        self._active_widget = obj
                        self._active_tip = tip
                        QToolTip.showText(QCursor.pos(), tip, obj)

        return super().eventFilter(obj, event)

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
