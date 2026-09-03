# -*- coding: utf-8 -*-
"""
M59 Dialog Windows & Signals Module
Provides:
- M59PlayerGroupDialog: Group/Guild manager for categorized WhoList rosters
- M59DirectMessageDialog: Direct Message/Tell conversation window
- AliasEditDialog: In-game command alias macro modal editor
- PKStatsDialog: Comprehensive Player Kill & death statistical ledger dialog
- M59SplashScreen: Borderless animated process scanner startup splash
- GameBridgeSignal: Qt Signal bridge for background game events
- M59StandaloneDockWindow: Detachable Desktop AppBar panel (Clock + WhoList)
"""

import sys
import os
import time
import json
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

try:
    from PySide6.QtWidgets import (
        QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
        QTableWidget, QTableWidgetItem, QFrame, QHeaderView, QTextEdit,
        QDialog, QCheckBox, QAbstractItemView, QMessageBox, QMenu, QSplashScreen,
        QFormLayout, QComboBox, QGroupBox, QProgressBar
    )
    from PySide6.QtCore import Qt, QTimer, Signal, QObject, QPoint, QRect, QEvent, QSize
    from PySide6.QtGui import (
        QFont, QIcon, QColor, QTextCursor, QPixmap, QImage, QPainter, QPen, QBrush,
        QLinearGradient, QCursor, QGuiApplication
    )
except ImportError:
    class _DummyQt:
        PointingHandCursor = None
        Horizontal = None
        WindowContextHelpButtonHint = 0
        def __getattr__(self, name):
            return 0
    QApplication = QWidget = QVBoxLayout = QHBoxLayout = QLabel = QPushButton = QLineEdit = object
    QTableWidget = QTableWidgetItem = QFrame = QHeaderView = QTextEdit = object
    QDialog = QCheckBox = QAbstractItemView = QMessageBox = QMenu = QSplashScreen = object
    QFormLayout = QComboBox = QGroupBox = QProgressBar = object
    Qt = _DummyQt()
    QTimer = QObject = QPoint = QRect = QEvent = QSize = object
    Signal = lambda *a, **k: None
    QFont = QIcon = QColor = QTextCursor = QPixmap = QImage = QPainter = QPen = QBrush = object
    QLinearGradient = QCursor = QGuiApplication = object

from m59_utils import resource_path
from m59_ui_theme import FLUID_WEB_QSS
from m59_ui_cards import PKGraphChartWidget
from m59_appbar import register_window_appbar, unregister_window_appbar, reset_desktop_workarea
try:
    from m59_vault import send_chat_command
except Exception:
    send_chat_command = None

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


class PKStatsDialog(QDialog):
    """
    Modal Dialog providing comprehensive Player Kill (PK) statistics & interactive graphs.
    """
    def __init__(self, parent=None, kill_book=None):
        super().__init__(parent)
        self.setWindowTitle("⚔️ PK Combat Analytics & Target Intelligence")
        self.resize(850, 620)
        self.setMinimumSize(750, 520)
        self.setStyleSheet("""
            QDialog {
                background-color: #030712;
                color: #f8fafc;
            }
        """)
        self.kill_book = kill_book or {}
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        hdr_frame = QFrame()
        hdr_frame.setStyleSheet("background-color: #0f172a; border: 1px solid #1e293b; border-radius: 8px; padding: 10px;")
        h_layout = QHBoxLayout(hdr_frame)
        h_layout.setContentsMargins(10, 6, 10, 6)

        t_box = QVBoxLayout()
        title_lbl = QLabel("⚔️ Player Kills (PK) Analytics")
        title_lbl.setStyleSheet("font-size: 15px; font-weight: 800; color: #f8fafc;")
        sub_lbl = QLabel("Track time-of-day victim activity patterns, rival stats, and kill history.")
        sub_lbl.setStyleSheet("font-size: 11px; color: #64748b;")
        t_box.addWidget(title_lbl)
        t_box.addWidget(sub_lbl)
        h_layout.addLayout(t_box)
        h_layout.addStretch()

        self.kpi_total = QLabel("0 PK Victories")
        self.kpi_total.setStyleSheet("background-color: #581c87; color: #c084fc; font-size: 11px; font-weight: 800; padding: 4px 10px; border-radius: 6px;")
        h_layout.addWidget(self.kpi_total)

        self.kpi_targets = QLabel("0 Targets")
        self.kpi_targets.setStyleSheet("background-color: #0c4a6e; color: #38bdf8; font-size: 11px; font-weight: 800; padding: 4px 10px; border-radius: 6px;")
        h_layout.addWidget(self.kpi_targets)

        self.kpi_peak_hour = QLabel("Peak: --:00")
        self.kpi_peak_hour.setStyleSheet("background-color: #78350f; color: #fde047; font-size: 11px; font-weight: 800; padding: 4px 10px; border-radius: 6px;")
        h_layout.addWidget(self.kpi_peak_hour)

        layout.addWidget(hdr_frame)

        ctrl_layout = QHBoxLayout()
        ctrl_layout.setSpacing(8)

        t_lbl = QLabel("Target:")
        t_lbl.setStyleSheet("color: #94a3b8; font-weight: 700; font-size: 11px;")
        ctrl_layout.addWidget(t_lbl)

        self.target_combo = QComboBox()
        self.target_combo.addItem("All PK Targets")
        self.target_combo.setMinimumWidth(130)
        ctrl_layout.addWidget(self.target_combo)

        m_lbl = QLabel("Chart View:")
        m_lbl.setStyleSheet("color: #94a3b8; font-weight: 700; font-size: 11px;")
        ctrl_layout.addWidget(m_lbl)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Hourly Distribution", "Day of Week", "Top PK Targets"])
        self.mode_combo.setMinimumWidth(150)
        ctrl_layout.addWidget(self.mode_combo)

        tf_lbl = QLabel("Timeframe:")
        tf_lbl.setStyleSheet("color: #94a3b8; font-weight: 700; font-size: 11px;")
        ctrl_layout.addWidget(tf_lbl)

        self.timeframe_combo = QComboBox()
        self.timeframe_combo.addItems(["All Time", "Last 30 Days", "Last 7 Days", "Today"])
        self.timeframe_combo.setMinimumWidth(110)
        ctrl_layout.addWidget(self.timeframe_combo)

        ctrl_layout.addStretch()

        self.demo_btn = QPushButton("➕ Demo Kills")
        self.demo_btn.setProperty("class", "WebBtnSecondary")
        self.demo_btn.setStyleSheet("padding: 2px 8px; font-size: 10px;")
        self.demo_btn.setToolTip("Populate sample PK kills for testing statistics & graphs")
        self.demo_btn.clicked.connect(self.add_demo_pk_data)
        ctrl_layout.addWidget(self.demo_btn)

        layout.addLayout(ctrl_layout)

        combo_style = """
            QComboBox {
                background-color: #0f172a;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: 600;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: #0f172a;
                color: #f8fafc;
                selection-background-color: #1e293b;
            }
        """
        self.target_combo.setStyleSheet(combo_style)
        self.mode_combo.setStyleSheet(combo_style)
        self.timeframe_combo.setStyleSheet(combo_style)

        self.chart_widget = PKGraphChartWidget()
        layout.addWidget(self.chart_widget)

        lbl_hist = QLabel("📜 RECENT PLAYER KILL HISTORY LOG")
        lbl_hist.setStyleSheet("font-size: 11px; font-weight: 800; color: #c084fc; letter-spacing: 0.8px; margin-top: 4px;")
        layout.addWidget(lbl_hist)

        self.hist_table = QTableWidget(0, 5)
        self.hist_table.verticalHeader().setVisible(False)
        self.hist_table.setHorizontalHeaderLabels(["TARGET / VICTIM", "DATE & TIME", "DAY", "HOUR", "LOCATION"])
        self.hist_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.hist_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.hist_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.hist_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.hist_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.hist_table.setStyleSheet("""
            QTableWidget {
                background-color: #030712;
                color: #f8fafc;
                border: 1px solid #1e293b;
                border-radius: 6px;
                gridline-color: #0f172a;
            }
            QHeaderView::section {
                background-color: #0f172a;
                color: #94a3b8;
                font-size: 10px;
                font-weight: 800;
                padding: 4px;
                border: 1px solid #1e293b;
            }
            QTableWidget::item { padding: 2px 4px; font-size: 11px; }
            QTableWidget::item:selected { background-color: #1e293b; color: #c084fc; }
        """)
        self.hist_table.setMaximumHeight(160)
        layout.addWidget(self.hist_table)

        self.target_combo.currentIndexChanged.connect(self.refresh_ui)
        self.mode_combo.currentIndexChanged.connect(self.refresh_ui)
        self.timeframe_combo.currentIndexChanged.connect(self.refresh_ui)

        self.populate_targets()
        self.refresh_ui()

    def populate_targets(self):
        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        self.target_combo.addItem("All PK Targets")
        
        history = self.kill_book.get("player_kills_history", []) if isinstance(self.kill_book, dict) else []
        targets = set()
        for r in history:
            if isinstance(r, dict) and r.get("victim"):
                targets.add(r["victim"].title())
        
        plys = self.kill_book.get("players", {}) if isinstance(self.kill_book, dict) else {}
        for p in plys.keys():
            targets.add(p.title())

        for t in sorted(list(targets)):
            self.target_combo.addItem(t)
        self.target_combo.blockSignals(False)

    def refresh_ui(self):
        history = self.kill_book.get("player_kills_history", []) if isinstance(self.kill_book, dict) else []
        plys = self.kill_book.get("players", {}) if isinstance(self.kill_book, dict) else {}

        if not history and plys:
            history = []
            from datetime import datetime
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            now_d = datetime.now().strftime("%Y-%m-%d")
            for victim, count in plys.items():
                for _ in range(count):
                    history.append({
                        "victim": victim,
                        "timestamp": now_str,
                        "date": now_d,
                        "time": "12:00:00",
                        "hour": 12,
                        "day_of_week": "Wednesday",
                        "room": "Recorded History"
                    })
            if isinstance(self.kill_book, dict):
                self.kill_book["player_kills_history"] = history

        target = self.target_combo.currentText()
        mode = self.mode_combo.currentText()
        timeframe = self.timeframe_combo.currentText()

        self.chart_widget.set_data(history, target_filter=target, timeframe_filter=timeframe, chart_mode=mode)

        filtered_recs = self.chart_widget._filter_records()
        total_pks = len(filtered_recs)
        unique_targets = len(set(r.get("victim", "").lower() for r in filtered_recs if r.get("victim")))

        hours_count = [0] * 24
        for r in filtered_recs:
            try:
                h = int(r.get("hour", 0))
                if 0 <= h < 24:
                    hours_count[h] += 1
            except Exception:
                pass
        max_h = hours_count.index(max(hours_count)) if max(hours_count) > 0 else -1
        peak_str = f"Peak: {max_h:02d}:00" if max_h != -1 else "Peak: --:00"

        self.kpi_total.setText(f"{total_pks} PK Victories")
        self.kpi_targets.setText(f"{unique_targets} Targets")
        self.kpi_peak_hour.setText(peak_str)

        self.hist_table.setRowCount(0)
        sorted_recs = sorted(filtered_recs, key=lambda x: x.get("timestamp", ""), reverse=True)

        for r in sorted_recs:
            row = self.hist_table.rowCount()
            self.hist_table.insertRow(row)

            v_item = QTableWidgetItem(r.get("victim", "Unknown").title())
            ts_item = QTableWidgetItem(r.get("timestamp", r.get("date", "--")))
            d_item = QTableWidgetItem(r.get("day_of_week", "--"))
            h_item = QTableWidgetItem(f"{r.get('hour', 0):02d}:00")
            r_item = QTableWidgetItem(r.get("room", "Unknown"))

            self.hist_table.setItem(row, 0, v_item)
            self.hist_table.setItem(row, 1, ts_item)
            self.hist_table.setItem(row, 2, d_item)
            self.hist_table.setItem(row, 3, h_item)
            self.hist_table.setItem(row, 4, r_item)

    def add_demo_pk_data(self):
        import random
        from datetime import datetime, timedelta

        if "player_kills_history" not in self.kill_book or not isinstance(self.kill_book["player_kills_history"], list):
            self.kill_book["player_kills_history"] = []

        sample_victims = ["Psychochild", "Dusk", "Kafai", "Elu", "Morpheus", "ShadowStalker"]
        sample_rooms = ["Marion Town Square", "Barloque Bank", "Tos Arena", "Jorvik Forest", "Cor Noth Guild"]

        now = datetime.now()
        for i in range(25):
            days_ago = random.randint(0, 14)
            hour_val = random.choice([14, 15, 19, 20, 21, 21, 22, 22, 23])
            minute_val = random.randint(0, 59)
            dt = now - timedelta(days=days_ago)
            dt = dt.replace(hour=hour_val, minute=minute_val)

            victim = random.choice(sample_victims)
            rec = {
                "victim": victim,
                "timestamp": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "date": dt.strftime("%Y-%m-%d"),
                "time": dt.strftime("%H:%M:%S"),
                "hour": hour_val,
                "day_of_week": dt.strftime("%A"),
                "room": random.choice(sample_rooms)
            }
            self.kill_book["player_kills_history"].append(rec)

            if "players" not in self.kill_book or not isinstance(self.kill_book["players"], dict):
                self.kill_book["players"] = {}
            self.kill_book["players"][victim] = self.kill_book["players"].get(victim, 0) + 1

        self.populate_targets()
        self.refresh_ui()


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


class M59FirstTimeSyncDialog(QDialog):
    """
    Modern modal dialog shown when a News Globe is first scanned and no messages
    are currently archived in SQLite for that board.
    """
    def __init__(self, newsgroup_name: str, total_count: int, room_name: str = "", parent: QWidget = None):
        super().__init__(parent)
        self.newsgroup_name = newsgroup_name
        self.total_count = total_count
        self.room_name = room_name or "Unknown Chamber"
        self.setWindowTitle("🔮 News Globe Discovered")
        self.setFixedSize(500, 340)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.init_ui()

    def init_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #0b1322;
                border: 1px solid #1e293b;
                border-radius: 8px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        # 1. Header Card with Globe Icon
        hdr_layout = QHBoxLayout()
        hdr_layout.setSpacing(14)

        globe_icon = QLabel("🔮")
        globe_icon.setStyleSheet("font-size: 32px; background: transparent;")
        hdr_layout.addWidget(globe_icon)

        hdr_text_layout = QVBoxLayout()
        hdr_text_layout.setSpacing(2)

        title_lbl = QLabel("News Globe Detected!")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: 800; color: #f8fafc;")
        hdr_text_layout.addWidget(title_lbl)

        clean_ng = self.newsgroup_name.replace("_", " ")
        sub_lbl = QLabel(f"Board: <span style='color: #38bdf8; font-weight: 700;'>{clean_ng}</span>  •  Room: <span style='color: #94a3b8;'>{self.room_name}</span>")
        sub_lbl.setStyleSheet("font-size: 11px; color: #64748b;")
        hdr_text_layout.addWidget(sub_lbl)

        hdr_layout.addLayout(hdr_text_layout, 1)
        layout.addLayout(hdr_layout)

        # 2. Main Description Card
        desc_frame = QFrame()
        desc_frame.setStyleSheet("""
            QFrame {
                background-color: #0f1a30;
                border: 1px solid #1e293b;
                border-radius: 6px;
                padding: 12px;
            }
        """)
        desc_layout = QVBoxLayout(desc_frame)
        desc_layout.setContentsMargins(12, 10, 12, 10)
        desc_layout.setSpacing(6)

        msg_lbl = QLabel(
            f"This is your first time encountering this news globe.<br>"
            f"There are <b style='color: #38bdf8;'>{self.total_count} articles</b> available in the public catalog.<br><br>"
            f"Would you like to archive and download all message bodies to your local SQLite database for instant offline searching and reading?"
        )
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet("font-size: 12px; color: #cbd5e1; line-height: 1.4;")
        desc_layout.addWidget(msg_lbl)
        layout.addWidget(desc_frame)

        # 3. Prominent Warning Box
        warn_frame = QFrame()
        warn_frame.setStyleSheet("""
            QFrame {
                background-color: #2a1608;
                border: 1px solid #d97706;
                border-radius: 6px;
            }
        """)
        warn_layout = QHBoxLayout(warn_frame)
        warn_layout.setContentsMargins(12, 8, 12, 8)
        warn_layout.setSpacing(10)

        warn_icon = QLabel("⚠️")
        warn_icon.setStyleSheet("font-size: 16px; background: transparent;")
        warn_layout.addWidget(warn_icon)

        warn_txt = QLabel("<b>Warning:</b> Please do not leave this room mid-download. Leaving before completion may disrupt packet synchronization.")
        warn_txt.setWordWrap(True)
        warn_txt.setStyleSheet("font-size: 11px; color: #fbbf24;")
        warn_layout.addWidget(warn_txt, 1)
        layout.addWidget(warn_frame)

        layout.addStretch(1)

        # 4. Buttons Action Row
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        btn_dismiss = QPushButton("✖ Later / Dismiss")
        btn_dismiss.setCursor(Qt.PointingHandCursor)
        btn_dismiss.setFixedHeight(34)
        btn_dismiss.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #94a3b8;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 6px 14px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #334155;
                color: #f8fafc;
            }
        """)
        btn_dismiss.clicked.connect(self.reject)
        btn_layout.addWidget(btn_dismiss)

        btn_layout.addStretch(1)

        btn_download = QPushButton(f"📥 Download Archive ({self.total_count} Messages)")
        btn_download.setCursor(Qt.PointingHandCursor)
        btn_download.setDefault(True)
        btn_download.setFixedHeight(34)
        btn_download.setStyleSheet("""
            QPushButton {
                background-color: #0284c7;
                color: #ffffff;
                border: 1px solid #38bdf8;
                border-radius: 4px;
                padding: 6px 18px;
                font-size: 12px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #0369a1;
            }
        """)
        btn_download.clicked.connect(self.accept)
        btn_layout.addWidget(btn_download)

        layout.addLayout(btn_layout)


class M59GlobeDownloadProgressDialog(QDialog):
    """
    Non-blocking modal progress dialog that shows N of N message download progress
    with a progress bar, current article subject, and chamber warning.
    """
    signal_cancel = Signal()

    def __init__(self, newsgroup_name: str, total_count: int, parent: QWidget = None):
        super().__init__(parent)
        self.newsgroup_name = newsgroup_name
        self.total_count = max(1, total_count)
        self.setWindowTitle("📥 Archiving Globe Messages")
        self.setFixedSize(480, 240)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.init_ui()

    def init_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #0b1322;
                border: 1px solid #1e293b;
                border-radius: 8px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        # Header Title
        clean_ng = self.newsgroup_name.replace("_", " ")
        self.lbl_title = QLabel(f"📥 Archiving {clean_ng}")
        self.lbl_title.setStyleSheet("font-size: 15px; font-weight: 800; color: #f8fafc;")
        layout.addWidget(self.lbl_title)

        # Progress Status Line
        self.lbl_status = QLabel(f"Downloading message 0 of {self.total_count} (0%)...")
        self.lbl_status.setStyleSheet("font-size: 12px; font-weight: 600; color: #38bdf8;")
        layout.addWidget(self.lbl_status)

        # Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, self.total_count)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(18)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #070d18;
                border: 1px solid #1e293b;
                border-radius: 4px;
                text-align: center;
                color: #ffffff;
                font-size: 10px;
                font-weight: 700;
            }
            QProgressBar::chunk {
                background-color: #0284c7;
                border-radius: 3px;
            }
        """)
        layout.addWidget(self.progress_bar)

        # Article Subject Preview
        self.lbl_subject = QLabel("Preparing packet requests...")
        self.lbl_subject.setWordWrap(True)
        self.lbl_subject.setStyleSheet("font-size: 11px; color: #94a3b8; font-style: italic;")
        layout.addWidget(self.lbl_subject)

        # Room Stay Warning
        warn_layout = QHBoxLayout()
        warn_layout.setSpacing(6)
        warn_ico = QLabel("⚠️")
        warn_ico.setStyleSheet("font-size: 12px; background: transparent;")
        warn_layout.addWidget(warn_ico)

        warn_msg = QLabel("Do not leave the room while download is in progress.")
        warn_msg.setStyleSheet("font-size: 10px; color: #fbbf24; font-weight: 600;")
        warn_layout.addWidget(warn_msg, 1)
        layout.addLayout(warn_layout)

        layout.addStretch(1)

        # Action Button Row
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)

        self.btn_close = QPushButton("Background Sync")
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.setFixedHeight(28)
        self.btn_close.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #cbd5e1;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 4px 14px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #334155;
                color: #ffffff;
            }
        """)
        self.btn_close.clicked.connect(self.hide)
        btn_layout.addWidget(self.btn_close)

        layout.addLayout(btn_layout)

    def update_progress(self, current: int, total: int, subject: str = "", percent: int = 0):
        self.total_count = max(1, total)
        self.progress_bar.setMaximum(self.total_count)
        self.progress_bar.setValue(min(current, self.total_count))
        self.lbl_status.setText(f"Downloading message {current} of {self.total_count} ({percent}%)...")
        if subject:
            self.lbl_subject.setText(f"Article: \"{subject[:55]}...\"" if len(subject) > 55 else f"Article: \"{subject}\"")

    def mark_completed(self, total: int):
        self.progress_bar.setValue(total)
        self.lbl_status.setText(f"✅ Complete! {total} messages archived to SQLite.")
        self.lbl_status.setStyleSheet("font-size: 12px; font-weight: 700; color: #10b981;")
        self.lbl_subject.setText("All messages synchronized successfully.")
        self.btn_close.setText("Done")
        self.btn_close.clicked.disconnect()
        self.btn_close.clicked.connect(self.accept)

