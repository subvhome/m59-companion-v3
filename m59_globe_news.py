# -*- coding: utf-8 -*-
"""
Meridian 59 Globe News Reader & Autonomous Archiver Controller
Integrates with SQLite news archive (m59_companion.db) to display
General News and Designer News with a split-pane Master-Detail layout.
Runs the autonomous MeridianNewsArchiver background thread to detect room
changes, hook game packets, sync news catalogs, and download message bodies.
"""

import sys
import os
import time
import threading
from typing import List, Dict, Any, Optional

try:
    from PySide6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
        QSplitter, QListWidget, QListWidgetItem, QTextEdit, QFrame,
        QSizePolicy, QApplication, QProgressBar, QDialog
    )
    from PySide6.QtCore import Qt, Signal, Slot, QSize, QTimer, QThread
    from PySide6.QtGui import QFont, QColor, QIcon, QPainter, QBrush, QPen
except ImportError:
    class _DummyQt:
        PointingHandCursor = None
        Horizontal = None
        WindowContextHelpButtonHint = 0
        def __getattr__(self, name):
            return 0
    QWidget = QVBoxLayout = QHBoxLayout = QLabel = QLineEdit = QPushButton = object
    QSplitter = QListWidget = QListWidgetItem = QTextEdit = QFrame = object
    QSizePolicy = QApplication = QProgressBar = QDialog = object
    Qt = _DummyQt()
    Signal = lambda *a, **k: None
    Slot = lambda *a, **k: (lambda f: f)
    QSize = QTimer = QThread = object
    QFont = QColor = QIcon = QPainter = QBrush = QPen = object

from m59_auto_news_archiver import (
    NewsDatabase,
    MeridianNewsArchiver,
    find_meridian_game_process,
    get_db_path,
    console_log,
    NEWS_ROOM_BY_NAME,
    NEWS_ROOM_BY_RID,
    ROOM_NAME_TO_RID,
    LOCATIONS_TABLE,
    set_news_logging_enabled,
    is_news_logging_enabled
)

try:
    from m59_ui_dialogs import M59FirstTimeSyncDialog, M59GlobeDownloadProgressDialog
except Exception:
    try:
        from PySide6.QtWidgets import QDialog
    except Exception:
        QDialog = object

    class M59FirstTimeSyncDialog(QDialog):
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

            clean_ng = self.newsgroup_name.replace("_", " ")
            self.lbl_title = QLabel(f"📥 Archiving {clean_ng}")
            self.lbl_title.setStyleSheet("font-size: 15px; font-weight: 800; color: #f8fafc;")
            layout.addWidget(self.lbl_title)

            self.lbl_status = QLabel(f"Downloading message 0 of {self.total_count} (0%)...")
            self.lbl_status.setStyleSheet("font-size: 12px; font-weight: 600; color: #38bdf8;")
            layout.addWidget(self.lbl_status)

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

            self.lbl_subject = QLabel("Preparing packet requests...")
            self.lbl_subject.setWordWrap(True)
            self.lbl_subject.setStyleSheet("font-size: 11px; color: #94a3b8; font-style: italic;")
            layout.addWidget(self.lbl_subject)

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


class GlobeNewsArchiverWorker(QThread):
    """
    Autonomous background worker thread for MeridianNewsArchiver.
    Attaches Frida to Meridian 59 process, listens for room updates,
    and automatically pulls news catalogs and missing message bodies.
    """
    signal_status_updated = Signal(str, str)  # (status_text, color)
    signal_room_detected = Signal(str, str, bool)  # (room_name, globe_info, is_globe)
    signal_db_updated = Signal()  # emitted when new articles/bodies are saved
    signal_prompt_first_sync = Signal(str, int, int, int, str)  # (ng_name, nid, total_count, missing_count, room_name)
    signal_download_started = Signal(str, int, int, str)  # (ng_name, nid, total, room_name)
    signal_download_progress = Signal(str, int, int, str, int)  # (ng_name, current, total, subject, percent)
    signal_download_completed = Signal(str, int, int, int)  # (ng_name, total_downloaded, unread_count, total_unread)
    signal_unread_badge_updated = Signal(str, int, int)  # (ng_name, unread_count, total_unread)

    def __init__(self, db_path: str, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.db = NewsDatabase(self.db_path)
        self.archiver: Optional[MeridianNewsArchiver] = None
        self.is_running = True
        self.last_room_name = ""
        self.last_total_bodies = -1

    def _on_archiver_event(self, event_type: str, data: Dict[str, Any]):
        """Dispatches internal archiver events to Qt signals."""
        if event_type == "first_time_globe_detected":
            self.signal_prompt_first_sync.emit(
                data.get("newsgroup_name", "General_News"),
                data.get("newsgroup_id", 20),
                data.get("total_count", 0),
                data.get("missing_count", 0),
                data.get("room_name", "")
            )
        elif event_type == "download_started":
            self.signal_download_started.emit(
                data.get("newsgroup_name", "General_News"),
                data.get("newsgroup_id", 20),
                data.get("total", 0),
                data.get("room_name", "")
            )
        elif event_type == "download_progress":
            self.signal_download_progress.emit(
                data.get("newsgroup_name", ""),
                data.get("current", 0),
                data.get("total", 0),
                data.get("subject", ""),
                data.get("percent", 0)
            )
        elif event_type == "download_completed":
            self.signal_download_completed.emit(
                data.get("newsgroup_name", ""),
                data.get("total_downloaded", 0),
                data.get("unread_count", 0),
                data.get("total_unread", 0)
            )
            self.signal_db_updated.emit()
        elif event_type == "unread_badge_updated":
            self.signal_unread_badge_updated.emit(
                data.get("newsgroup_name", ""),
                data.get("unread_count", 0),
                data.get("total_unread", 0)
            )

    def confirm_first_time_sync(self, newsgroup_name: str):
        """User accepted first-time sync confirmation dialog."""
        if self.archiver:
            self.archiver.confirm_first_time_sync(newsgroup_name)

    def run(self):
        console_log("Starting Autonomous Globe News Archiver Background Worker...", "WORKER_START")
        
        while self.is_running:
            try:
                # 1. Check if archiver is attached
                if not self.archiver or not self.archiver.session:
                    pid, win_title, hwnd = find_meridian_game_process()
                    if pid:
                        console_log(f"Auto-Attaching News Archiver to Meridian 59 (PID: {pid})...", "WORKER_ATTACH")
                        self.archiver = MeridianNewsArchiver(pid, win_title or "")
                        self.archiver.hwnd = hwnd
                        self.archiver.register_event_callback(self._on_archiver_event)
                        attached = self.archiver.attach()
                        if attached:
                            self.signal_status_updated.emit(f"🟢 Active (PID: {pid})", "#34d399")
                            # Process initial window title room if known
                            if self.archiver.current_room_name and self.archiver.current_room_name != "Detecting...":
                                self.check_room_globe_status(self.archiver.current_room_name)
                        else:
                            self.signal_status_updated.emit("🔴 Frida Attach Failed", "#f87171")
                            self.archiver = None
                    else:
                        self.signal_status_updated.emit("🟡 Scanning for meridian.exe...", "#f59e0b")

                # 2. Check if new records arrived in SQLite database
                try:
                    total_arts, total_bodies = self.db.get_article_counts()
                    if total_bodies != self.last_total_bodies and self.last_total_bodies != -1:
                        console_log(f"Detected SQLite database update: {total_bodies}/{total_arts} bodies archived.", "WORKER_DB")
                        self.signal_db_updated.emit()
                    self.last_total_bodies = total_bodies
                except Exception:
                    pass

            except Exception as e:
                console_log(f"Archiver worker loop error: {e}", "WORKER_ERR")

            time.sleep(1.5)

    def check_room_globe_status(self, room_name: str):
        """Checks if room contains a news globe and emits UI signal."""
        if not room_name:
            return
        r_lower = room_name.lower()
        rid = ROOM_NAME_TO_RID.get(r_lower, "")
        matched = NEWS_ROOM_BY_NAME.get(r_lower)
        if not matched and rid:
            matched = NEWS_ROOM_BY_RID.get(rid.upper())

        if matched:
            globe_desc = f"{matched['name']} (NID: {matched['nid']})"
            self.signal_room_detected.emit(room_name, globe_desc, True)
        else:
            self.signal_room_detected.emit(room_name, "No Globe in Room", False)

    def set_current_room(self, room_name: str):
        """Called when room changes via companion app or window title."""
        if not room_name or room_name == self.last_room_name:
            return
        self.last_room_name = room_name
        self.check_room_globe_status(room_name)
        
        if self.archiver and self.archiver.session:
            console_log(f"Forwarding Room Change to Archiver Engine: '{room_name}'", "WORKER_ROOM")
            self.archiver.on_room_detected(room_name)

    def on_game_attached(self, pid: int, hwnd: Optional[int] = None):
        """Called directly when companion app InstanceManager attaches to game."""
        if self.archiver and self.archiver.pid == pid:
            return
        try:
            console_log(f"InstanceManager attached PID {pid}. Initializing News Archiver...", "WORKER_ATTACH")
            self.archiver = MeridianNewsArchiver(pid, "")
            self.archiver.hwnd = hwnd
            self.archiver.register_event_callback(self._on_archiver_event)
            if self.archiver.attach():
                self.signal_status_updated.emit(f"🟢 Active (PID: {pid})", "#34d399")
        except Exception as e:
            console_log(f"Error attaching archiver for PID {pid}: {e}", "WORKER_ERR")

    def on_game_detached(self):
        """Called when game process exits."""
        if self.archiver:
            try:
                self.archiver.cleanup()
            except Exception:
                pass
            self.archiver = None
        self.signal_status_updated.emit("🟡 Game Disconnected", "#f59e0b")
        self.signal_room_detected.emit("--", "Offline", False)

    def force_scan(self):
        """Triggers manual catalog scan and download on active globe."""
        if self.archiver and self.archiver.session:
            console_log(f"User requested Manual Scan on Newsgroup NID {self.archiver.newsgroup_id}...", "USER_SCAN")
            self.archiver.trigger_scan(settle_delay=0.0)
        else:
            console_log("Cannot force scan: Archiver not attached to Meridian 59 game process.", "WARN")

    def stop(self):
        self.is_running = False
        if self.archiver:
            try:
                self.archiver.cleanup()
            except Exception:
                pass


class GlobeNewsWidget(QWidget):
    """
    Globe News section widget for M59 Companion Dashboard.
    Layout:
      - Top Filter & Search Bar: All | General News | Designer News | Search | Force Scan | Refresh
      - Sub-Ribbon: Current Room Globe Detection & Engine Live Status
      - In-App Download Progress Banner: N of N progress bar and room warning
      - Splitter:
          - Left: Article headers catalog (Subject, Author, Date, Channel, Read/Unread)
          - Right: Article body reader with Post / Reply / Mail Author (disabled) and Copy
      - Bottom Status Bar: Messages in view • Read • Unread
    """
    signal_unread_badge_updated = Signal(int)
    signal_toast_requested = Signal(str, str, str)  # (title, message, icon_type)

    def __init__(self, db_path: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.db_path = db_path or get_db_path()
        self.db = NewsDatabase(self.db_path)

        self.current_channel_filter: str = "ALL"  # "ALL", "General_News", "Designers_News"
        self.search_query: str = ""
        self.all_articles: List[Dict[str, Any]] = []
        self.filtered_articles: List[Dict[str, Any]] = []
        self.current_selected_article: Optional[Dict[str, Any]] = None
        self._active_first_sync_dialog: Optional[M59FirstTimeSyncDialog] = None

        self.init_ui()
        self.load_articles_from_db()

        # Start Autonomous Archiver Thread
        self.archiver_worker = GlobeNewsArchiverWorker(self.db_path, self)
        self.archiver_worker.signal_status_updated.connect(self.on_worker_status_updated)
        self.archiver_worker.signal_room_detected.connect(self.on_worker_room_detected)
        self.archiver_worker.signal_db_updated.connect(self.load_articles_from_db)
        self.archiver_worker.signal_prompt_first_sync.connect(self.handle_prompt_first_sync)
        self.archiver_worker.signal_download_started.connect(self.handle_download_started)
        self.archiver_worker.signal_download_progress.connect(self.handle_download_progress)
        self.archiver_worker.signal_download_completed.connect(self.handle_download_completed)
        self.archiver_worker.signal_unread_badge_updated.connect(self.handle_unread_badge_updated)
        self.archiver_worker.start()

        # Auto-refresh timer to poll SQLite periodically
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(2500)
        self.refresh_timer.timeout.connect(self.poll_db_updates)
        self.refresh_timer.start()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # -------------------------------------------------------------
        # 1. TOP FILTER & CONTROL BAR
        # -------------------------------------------------------------
        top_bar = QFrame()
        top_bar.setObjectName("NewsTopFilterBar")
        top_bar.setFixedHeight(50)
        top_bar.setStyleSheet("""
            QFrame#NewsTopFilterBar {
                background-color: #0d1527;
                border-bottom: 1px solid #1e293b;
            }
        """)
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(14, 8, 14, 8)
        top_layout.setSpacing(8)

        # Filter Pills: All, General News, Designer News
        self.btn_filter_all = QPushButton("All")
        self.btn_filter_all.setCursor(Qt.PointingHandCursor)
        self.btn_filter_all.setFixedHeight(28)
        self.btn_filter_all.clicked.connect(lambda: self.set_channel_filter("ALL"))

        self.btn_filter_general = QPushButton("General News")
        self.btn_filter_general.setCursor(Qt.PointingHandCursor)
        self.btn_filter_general.setFixedHeight(28)
        self.btn_filter_general.clicked.connect(lambda: self.set_channel_filter("General_News"))

        self.btn_filter_designer = QPushButton("Designer News")
        self.btn_filter_designer.setCursor(Qt.PointingHandCursor)
        self.btn_filter_designer.setFixedHeight(28)
        self.btn_filter_designer.clicked.connect(lambda: self.set_channel_filter("Designers_News"))

        top_layout.addWidget(self.btn_filter_all)
        top_layout.addWidget(self.btn_filter_general)
        top_layout.addWidget(self.btn_filter_designer)

        top_layout.addStretch(1)

        # Search Input Field
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search author, title, content...")
        self.search_input.setFixedHeight(28)
        self.search_input.setMinimumWidth(220)
        self.search_input.setStyleSheet("""
            QLineEdit {
                background-color: #080d1a;
                color: #e2e8f0;
                border: 1px solid #1e293b;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 11px;
            }
            QLineEdit:focus {
                border: 1px solid #38bdf8;
            }
        """)
        self.search_input.textChanged.connect(self.on_search_text_changed)
        top_layout.addWidget(self.search_input)

        # Force Scan Button
        self.btn_force_scan = QPushButton("⚡ Scan Globe")
        self.btn_force_scan.setCursor(Qt.PointingHandCursor)
        self.btn_force_scan.setFixedHeight(28)
        self.btn_force_scan.setToolTip("Trigger immediate catalog fetch and message download from current news globe")
        self.btn_force_scan.setStyleSheet("""
            QPushButton {
                background-color: #0284c7;
                color: #ffffff;
                border: 1px solid #38bdf8;
                border-radius: 4px;
                padding: 4px 12px;
                font-size: 11px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #0369a1;
            }
        """)
        self.btn_force_scan.clicked.connect(self.handle_force_scan)
        top_layout.addWidget(self.btn_force_scan)

        # Reload / Refresh button
        self.btn_refresh = QPushButton("🔄 Refresh")
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_refresh.setFixedHeight(28)
        self.btn_refresh.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #cbd5e1;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #334155;
                color: #f8fafc;
            }
        """)
        self.btn_refresh.clicked.connect(self.load_articles_from_db)
        top_layout.addWidget(self.btn_refresh)

        main_layout.addWidget(top_bar)

        # -------------------------------------------------------------
        # 1.5 SUB-RIBBON: GLOBE DETECTION & ENGINE STATUS
        # -------------------------------------------------------------
        ribbon_bar = QFrame()
        ribbon_bar.setFixedHeight(28)
        ribbon_bar.setStyleSheet("background-color: #080e1a; border-bottom: 1px solid #1e293b;")
        ribbon_layout = QHBoxLayout(ribbon_bar)
        ribbon_layout.setContentsMargins(14, 0, 14, 0)
        ribbon_layout.setSpacing(12)

        self.lbl_globe_status = QLabel("📍 Location: Detecting room...")
        self.lbl_globe_status.setStyleSheet("font-size: 10px; font-weight: 700; color: #94a3b8;")
        ribbon_layout.addWidget(self.lbl_globe_status)

        ribbon_layout.addStretch()

        self.lbl_engine_status = QLabel("🟡 Scanning for meridian.exe...")
        self.lbl_engine_status.setStyleSheet("font-size: 10px; font-weight: 700; color: #f59e0b;")
        ribbon_layout.addWidget(self.lbl_engine_status)

        main_layout.addWidget(ribbon_bar)

        # -------------------------------------------------------------
        # 1.8 IN-APP DOWNLOAD PROGRESS BANNER (N of N Progress Bar)
        # -------------------------------------------------------------
        self.download_banner = QFrame()
        self.download_banner.setObjectName("GlobeDownloadBanner")
        self.download_banner.setFixedHeight(36)
        self.download_banner.setStyleSheet("""
            QFrame#GlobeDownloadBanner {
                background-color: #0c1a30;
                border-bottom: 1px solid #0284c7;
            }
        """)
        self.download_banner.hide()
        banner_layout = QHBoxLayout(self.download_banner)
        banner_layout.setContentsMargins(14, 0, 14, 0)
        banner_layout.setSpacing(10)

        self.lbl_banner_status = QLabel("📥 Archiving Globe: 0/0 (0%)...")
        self.lbl_banner_status.setStyleSheet("font-size: 11px; font-weight: 700; color: #38bdf8;")
        banner_layout.addWidget(self.lbl_banner_status)

        self.banner_progress = QProgressBar()
        self.banner_progress.setRange(0, 100)
        self.banner_progress.setValue(0)
        self.banner_progress.setFixedHeight(14)
        self.banner_progress.setFixedWidth(200)
        self.banner_progress.setTextVisible(True)
        self.banner_progress.setStyleSheet("""
            QProgressBar {
                background-color: #070e1b;
                border: 1px solid #1e293b;
                border-radius: 3px;
                text-align: center;
                color: #ffffff;
                font-size: 9px;
                font-weight: 700;
            }
            QProgressBar::chunk {
                background-color: #0284c7;
                border-radius: 2px;
            }
        """)
        banner_layout.addWidget(self.banner_progress)

        self.lbl_banner_warn = QLabel("⚠️ Stay in room during sync")
        self.lbl_banner_warn.setStyleSheet("font-size: 10px; color: #fbbf24; font-weight: 600;")
        banner_layout.addWidget(self.lbl_banner_warn)

        banner_layout.addStretch()

        self.btn_banner_dismiss = QPushButton("✕")
        self.btn_banner_dismiss.setFixedSize(20, 20)
        self.btn_banner_dismiss.setCursor(Qt.PointingHandCursor)
        self.btn_banner_dismiss.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #94a3b8;
                border: none;
                font-size: 11px;
                font-weight: 700;
            }
            QPushButton:hover {
                color: #ffffff;
            }
        """)
        self.btn_banner_dismiss.clicked.connect(self.download_banner.hide)
        banner_layout.addWidget(self.btn_banner_dismiss)

        main_layout.addWidget(self.download_banner)

        # -------------------------------------------------------------
        # 2. DUAL PANE SPLITTER (Header on left, Body on right)
        # -------------------------------------------------------------
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #1e293b;
                width: 1px;
            }
        """)

        # LEFT PANE: Article Header List
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        # Left Sub-header bar
        list_header = QFrame()
        list_header.setFixedHeight(30)
        list_header.setStyleSheet("background-color: #0a101d; border-bottom: 1px solid #1e293b;")
        lh_layout = QHBoxLayout(list_header)
        lh_layout.setContentsMargins(12, 0, 12, 0)
        lbl_list_title = QLabel("Articles Catalog")
        lbl_list_title.setStyleSheet("font-size: 11px; font-weight: 700; color: #94a3b8;")
        lh_layout.addWidget(lbl_list_title)
        lh_layout.addStretch()

        self.btn_mark_all_read = QPushButton("Mark All Read")
        self.btn_mark_all_read.setCursor(Qt.PointingHandCursor)
        self.btn_mark_all_read.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: #38bdf8;
                font-size: 10px;
                font-weight: 600;
            }
            QPushButton:hover {
                text-decoration: underline;
                color: #7dd3fc;
            }
        """)
        self.btn_mark_all_read.clicked.connect(self.handle_mark_all_read)
        lh_layout.addWidget(self.btn_mark_all_read)

        left_layout.addWidget(list_header)

        self.article_list_widget = QListWidget()
        self.article_list_widget.setObjectName("NewsArticleList")
        self.article_list_widget.setFrameShape(QFrame.NoFrame)
        self.article_list_widget.setStyleSheet("""
            QListWidget#NewsArticleList {
                background-color: #0c1322;
                border: none;
                outline: none;
            }
            QListWidget#NewsArticleList::item {
                border-bottom: 1px solid #172033;
                padding: 8px 12px;
            }
            QListWidget#NewsArticleList::item:hover {
                background-color: #131d33;
            }
            QListWidget#NewsArticleList::item:selected {
                background-color: #0c2340;
                border-left: 3px solid #38bdf8;
            }
        """)
        self.article_list_widget.currentRowChanged.connect(self.on_article_selected)
        left_layout.addWidget(self.article_list_widget, 1)

        self.splitter.addWidget(left_panel)

        # RIGHT PANE: Article Detail & Body Reader
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # Article Header Card in Reader
        self.reader_header_card = QFrame()
        self.reader_header_card.setObjectName("ReaderHeaderCard")
        self.reader_header_card.setStyleSheet("""
            QFrame#ReaderHeaderCard {
                background-color: #0d1424;
                border-bottom: 1px solid #1e293b;
            }
        """)
        rh_layout = QVBoxLayout(self.reader_header_card)
        rh_layout.setContentsMargins(18, 14, 18, 14)
        rh_layout.setSpacing(10)

        # Channel Badge & Date & Actions Row
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        self.lbl_channel_badge = QLabel("General News")
        self.lbl_channel_badge.setStyleSheet("""
            background-color: #082f49;
            color: #7dd3fc;
            border: 1px solid #0369a1;
            border-radius: 4px;
            padding: 2px 8px;
            font-size: 10px;
            font-weight: 700;
        """)
        row1.addWidget(self.lbl_channel_badge)

        self.lbl_post_date = QLabel("Date: --")
        self.lbl_post_date.setStyleSheet("color: #94a3b8; font-size: 11px; font-family: monospace;")
        row1.addWidget(self.lbl_post_date)

        row1.addStretch(1)

        # Placeholder Action Buttons: Post, Reply, Mail Author (DISABLED)
        self.btn_post = QPushButton("✏️ Post")
        self.btn_post.setEnabled(False)
        self.btn_post.setToolTip("Post new message (Placeholder - Coming Soon)")
        self.btn_post.setStyleSheet(self._disabled_btn_style())
        row1.addWidget(self.btn_post)

        self.btn_reply = QPushButton("↩️ Reply")
        self.btn_reply.setEnabled(False)
        self.btn_reply.setToolTip("Reply to message (Placeholder - Coming Soon)")
        self.btn_reply.setStyleSheet(self._disabled_btn_style())
        row1.addWidget(self.btn_reply)

        self.btn_mail_author = QPushButton("✉️ Mail Author")
        self.btn_mail_author.setEnabled(False)
        self.btn_mail_author.setToolTip("Send in-game mail to author (Placeholder - Coming Soon)")
        self.btn_mail_author.setStyleSheet(self._disabled_btn_style())
        row1.addWidget(self.btn_mail_author)

        # Copy Body Button (Active)
        self.btn_copy = QPushButton("📋 Copy")
        self.btn_copy.setCursor(Qt.PointingHandCursor)
        self.btn_copy.setToolTip("Copy article body to clipboard")
        self.btn_copy.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #e2e8f0;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #334155;
                color: #ffffff;
            }
        """)
        self.btn_copy.clicked.connect(self.handle_copy_body)
        row1.addWidget(self.btn_copy)

        rh_layout.addLayout(row1)

        # Subject Title
        self.lbl_subject = QLabel("Select an article to view")
        self.lbl_subject.setWordWrap(True)
        self.lbl_subject.setStyleSheet("font-size: 16px; font-weight: 800; color: #f8fafc; line-height: 1.3;")
        rh_layout.addWidget(self.lbl_subject)

        # Author Metadata Card
        self.lbl_author_info = QLabel("Author: --")
        self.lbl_author_info.setStyleSheet("font-size: 11px; color: #cbd5e1; font-weight: 600;")
        rh_layout.addWidget(self.lbl_author_info)

        right_layout.addWidget(self.reader_header_card)

        # Body Text Edit (Monospace Parchment style)
        self.body_text_edit = QTextEdit()
        self.body_text_edit.setReadOnly(True)
        self.body_text_edit.setFrameShape(QFrame.NoFrame)
        self.body_text_edit.setStyleSheet("""
            QTextEdit {
                background-color: #070b14;
                color: #cbd5e1;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
                line-height: 1.6;
                padding: 18px 24px;
                border: none;
            }
        """)
        right_layout.addWidget(self.body_text_edit, 1)

        self.splitter.addWidget(right_panel)

        # Set initial splitter proportions: 35% left, 65% right
        self.splitter.setSizes([340, 660])
        main_layout.addWidget(self.splitter, 1)

        # -------------------------------------------------------------
        # 3. FOOTER STATUS BAR (Messages in view • Read • Unread)
        # -------------------------------------------------------------
        status_bar = QFrame()
        status_bar.setObjectName("NewsFooterStatusBar")
        status_bar.setFixedHeight(32)
        status_bar.setStyleSheet("""
            QFrame#NewsFooterStatusBar {
                background-color: #0d1424;
                border-top: 1px solid #1e293b;
            }
        """)
        sb_layout = QHBoxLayout(status_bar)
        sb_layout.setContentsMargins(14, 0, 14, 0)
        sb_layout.setSpacing(8)

        self.lbl_status_counts = QLabel("0 Messages in view  •  0 Read  •  0 Unread")
        self.lbl_status_counts.setStyleSheet("font-size: 11px; font-weight: 600; color: #94a3b8;")
        sb_layout.addWidget(self.lbl_status_counts)

        sb_layout.addStretch(1)

        self.lbl_active_filter_note = QLabel("Showing All Boards")
        self.lbl_active_filter_note.setStyleSheet("font-size: 10px; color: #64748b;")
        sb_layout.addWidget(self.lbl_active_filter_note)

        main_layout.addWidget(status_bar)

        self._update_filter_button_styles()

    def _disabled_btn_style(self) -> str:
        return """
            QPushButton {
                background-color: #1e293b;
                color: #64748b;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 500;
            }
        """

    def _update_filter_button_styles(self):
        active_style = """
            QPushButton {
                background-color: #0284c7;
                color: #ffffff;
                border: 1px solid #38bdf8;
                border-radius: 4px;
                padding: 4px 14px;
                font-size: 11px;
                font-weight: 700;
            }
        """
        inactive_style = """
            QPushButton {
                background-color: #1e293b;
                color: #94a3b8;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 4px 14px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #334155;
                color: #f8fafc;
            }
        """
        self.btn_filter_all.setStyleSheet(active_style if self.current_channel_filter == "ALL" else inactive_style)
        self.btn_filter_general.setStyleSheet(active_style if self.current_channel_filter == "General_News" else inactive_style)
        self.btn_filter_designer.setStyleSheet(active_style if self.current_channel_filter == "Designers_News" else inactive_style)

    def set_channel_filter(self, channel: str):
        self.current_channel_filter = channel
        self._update_filter_button_styles()
        
        if channel == "ALL":
            self.lbl_active_filter_note.setText("Showing All Boards")
        elif channel == "General_News":
            self.lbl_active_filter_note.setText("Filtered: General News")
        else:
            self.lbl_active_filter_note.setText("Filtered: Designer News")

        self.apply_filter()

    def on_search_text_changed(self, text: str):
        self.search_query = text.strip().lower()
        self.apply_filter()

    def load_articles_from_db(self):
        """Loads articles from SQLite database and updates UI."""
        try:
            records = self.db.get_all_articles_for_reader()
        except Exception:
            records = []

        self.all_articles = records
        self.apply_filter()
        self.update_badge_counts()

    def update_badge_counts(self):
        """Updates unread count labels on filter buttons and emits unread badge signal."""
        try:
            tot_unread = self.db.get_unread_count()
            gen_unread = self.db.get_unread_count("General_News")
            des_unread = self.db.get_unread_count("Designers_News")
        except Exception:
            tot_unread, gen_unread, des_unread = 0, 0, 0

        self.btn_filter_all.setText(f"All ({tot_unread})" if tot_unread > 0 else "All")
        self.btn_filter_general.setText(f"General News ({gen_unread})" if gen_unread > 0 else "General News")
        self.btn_filter_designer.setText(f"Designer News ({des_unread})" if des_unread > 0 else "Designer News")

        self.signal_unread_badge_updated.emit(tot_unread)

        # Notify parent window if present
        try:
            parent_win = self.window()
            if hasattr(parent_win, "update_globe_news_nav_badge"):
                parent_win.update_globe_news_nav_badge(tot_unread)
        except Exception:
            pass

    def poll_db_updates(self):
        """Periodically checks if total article count changed in SQLite."""
        try:
            total_arts, total_bodies = self.db.get_article_counts()
            if len(self.all_articles) != total_arts:
                self.load_articles_from_db()
        except Exception:
            pass

    def apply_filter(self):
        """Applies channel filter and search query to all_articles."""
        filtered = []
        for art in self.all_articles:
            if self.current_channel_filter != "ALL" and art["newsgroup_name"] != self.current_channel_filter:
                continue

            if self.search_query:
                sq = self.search_query
                matches = (
                    sq in art.get("subject", "").lower() or
                    sq in art.get("author", "").lower() or
                    sq in art.get("body", "").lower()
                )
                if not matches:
                    continue

            filtered.append(art)

        self.filtered_articles = filtered
        self.render_article_list()

    def render_article_list(self):
        """Populates left QListWidget with filtered article headers."""
        # Preserve selection index if possible
        prev_row = self.article_list_widget.currentRow()

        self.article_list_widget.blockSignals(True)
        self.article_list_widget.clear()

        read_count = sum(1 for a in self.filtered_articles if a.get("is_read"))
        unread_count = len(self.filtered_articles) - read_count

        self.lbl_status_counts.setText(
            f"<b>{len(self.filtered_articles)}</b> Messages in view  •  "
            f"<span style='color:#34d399;'><b>{read_count}</b> Read</span>  •  "
            f"<span style='color:{'#38bdf8' if unread_count > 0 else '#94a3b8'};'><b>{unread_count}</b> Unread</span>"
        )

        for idx, art in enumerate(self.filtered_articles):
            item = QListWidgetItem()
            item.setSizeHint(QSize(300, 68))

            is_designer = art["newsgroup_name"] == "Designers_News"
            is_read = art.get("is_read", False)
            date_str = art.get("post_date", "").split(" ")[0] if art.get("post_date") else ""

            # Card Widget
            card = QFrame()
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(6, 6, 6, 6)
            card_layout.setSpacing(3)

            # Top row: Channel badge & Date
            row1 = QHBoxLayout()
            row1.setContentsMargins(0, 0, 0, 0)

            channel_lbl = QLabel("Designer News" if is_designer else "General News")
            channel_lbl.setStyleSheet(f"""
                font-size: 9px;
                font-weight: 700;
                padding: 1px 6px;
                border-radius: 3px;
                background-color: {'#451a03' if is_designer else '#082f49'};
                color: {'#fde68a' if is_designer else '#7dd3fc'};
                border: 1px solid {'#92400e' if is_designer else '#0284c7'};
            """)
            row1.addWidget(channel_lbl)

            if not is_read:
                unread_badge = QLabel("● Unread")
                unread_badge.setStyleSheet("color: #38bdf8; font-size: 9px; font-weight: 800; margin-left: 4px;")
                row1.addWidget(unread_badge)

            row1.addStretch()

            date_lbl = QLabel(date_str)
            date_lbl.setStyleSheet("color: #64748b; font-size: 9px; font-family: monospace;")
            row1.addWidget(date_lbl)

            card_layout.addLayout(row1)

            # Subject title
            subj_lbl = QLabel(art.get("subject", "No Subject"))
            subj_lbl.setWordWrap(False)
            subj_lbl.setStyleSheet(f"""
                font-size: 11px;
                font-weight: {'700' if not is_read else '500'};
                color: {'#f8fafc' if not is_read else '#cbd5e1'};
            """)
            card_layout.addWidget(subj_lbl)

            # Author line
            author_lbl = QLabel(f"👤 {art.get('author', 'Unknown')}")
            author_lbl.setStyleSheet("color: #94a3b8; font-size: 10px;")
            card_layout.addWidget(author_lbl)

            self.article_list_widget.addItem(item)
            self.article_list_widget.setItemWidget(item, card)

        self.article_list_widget.blockSignals(False)

        # Maintain or select first article
        if self.filtered_articles:
            target_row = prev_row if (0 <= prev_row < len(self.filtered_articles)) else 0
            self.article_list_widget.setCurrentRow(target_row)
            self.display_article(self.filtered_articles[target_row])
        else:
            self.clear_display()

    def on_article_selected(self, row: int):
        if 0 <= row < len(self.filtered_articles):
            art = self.filtered_articles[row]
            # Mark read
            if not art.get("is_read"):
                art["is_read"] = True
                try:
                    self.db.mark_article_read(art["newsgroup_name"], art["article_id"])
                except Exception:
                    pass
                # Update status counts
                read_count = sum(1 for a in self.filtered_articles if a.get("is_read"))
                unread_count = len(self.filtered_articles) - read_count
                self.lbl_status_counts.setText(
                    f"<b>{len(self.filtered_articles)}</b> Messages in view  •  "
                    f"<span style='color:#34d399;'><b>{read_count}</b> Read</span>  •  "
                    f"<span style='color:{'#38bdf8' if unread_count > 0 else '#94a3b8'};'><b>{unread_count}</b> Unread</span>"
                )

            self.display_article(art)

    def display_article(self, art: Dict[str, Any]):
        self.current_selected_article = art
        is_designer = art["newsgroup_name"] == "Designers_News"

        self.lbl_channel_badge.setText("Designer News" if is_designer else "General News")
        self.lbl_channel_badge.setStyleSheet(f"""
            background-color: {'#451a03' if is_designer else '#082f49'};
            color: {'#fde68a' if is_designer else '#7dd3fc'};
            border: 1px solid {'#92400e' if is_designer else '#0284c7'};
            border-radius: 4px;
            padding: 2px 8px;
            font-size: 10px;
            font-weight: 700;
        """)

        post_date = art.get("post_date") or "Unknown Date"
        self.lbl_post_date.setText(f"Posted on {post_date}")
        self.lbl_subject.setText(art.get("subject", "No Subject"))
        self.lbl_author_info.setText(f"👤 Author: {art.get('author', 'Unknown')}")

        body_text = art.get("body", "")
        self.body_text_edit.setPlainText(body_text if body_text else "[Empty message body]")

    def clear_display(self):
        self.current_selected_article = None
        self.lbl_channel_badge.setText("Globe News")
        self.lbl_post_date.setText("Date: --")
        self.lbl_subject.setText("No articles found matching criteria")
        self.lbl_author_info.setText("Author: --")
        self.body_text_edit.setPlainText("")

    def handle_copy_body(self):
        if self.current_selected_article:
            clipboard = QApplication.clipboard()
            clipboard.setText(self.current_selected_article.get("body", ""))
            self.btn_copy.setText("✓ Copied")
            QTimer.singleShot(2000, lambda: self.btn_copy.setText("📋 Copy"))

    def handle_mark_all_read(self):
        try:
            self.db.mark_all_read(
                self.current_channel_filter if self.current_channel_filter != "ALL" else None
            )
        except Exception:
            pass

        for art in self.filtered_articles:
            art["is_read"] = True

        self.render_article_list()

    def handle_force_scan(self):
        """User triggered manual scan button."""
        self.btn_force_scan.setEnabled(False)
        self.btn_force_scan.setText("⏳ Scanning...")
        self.archiver_worker.force_scan()
        QTimer.singleShot(3000, lambda: (
            self.btn_force_scan.setEnabled(True),
            self.btn_force_scan.setText("⚡ Scan Globe")
        ))

    # -------------------------------------------------------------
    # SLOTS & EXTERNAL COMPANION APP INTEGRATION
    # -------------------------------------------------------------

    @Slot(str, str)
    def on_worker_status_updated(self, status_text: str, color: str):
        self.lbl_engine_status.setText(status_text)
        self.lbl_engine_status.setStyleSheet(f"font-size: 10px; font-weight: 700; color: {color};")

    @Slot(str, str, bool)
    def on_worker_room_detected(self, room_name: str, globe_desc: str, is_globe: bool):
        if is_globe:
            self.lbl_globe_status.setText(f"🟢 Active Newsroom: {room_name} ({globe_desc})")
            self.lbl_globe_status.setStyleSheet("font-size: 10px; font-weight: 700; color: #38bdf8;")
        else:
            self.lbl_globe_status.setText(f"📍 Location: {room_name} (No News Globe)")
            self.lbl_globe_status.setStyleSheet("font-size: 10px; font-weight: 700; color: #94a3b8;")

    def on_room_changed(self, room_name: str):
        """Forward room update from main companion GPS / title tracker."""
        if hasattr(self, 'archiver_worker'):
            self.archiver_worker.set_current_room(room_name)

    def on_game_connected(self, pid: int, hwnd: Optional[int] = None):
        """Forward process attachment from InstanceManager."""
        if hasattr(self, 'archiver_worker'):
            self.archiver_worker.on_game_attached(pid, hwnd)

    def on_game_disconnected(self):
        """Forward process disconnect."""
        if hasattr(self, 'archiver_worker'):
            self.archiver_worker.on_game_detached()

    @Slot(str, int, int, int, str)
    def handle_prompt_first_sync(self, newsgroup_name: str, nid: int, total_count: int, missing_count: int, room_name: str):
        """Shows first-time sync confirmation dialog if not already open."""
        count_to_sync = missing_count if missing_count > 0 else total_count
        if count_to_sync <= 0:
            return

        if self._active_first_sync_dialog and self._active_first_sync_dialog.isVisible():
            return

        self._active_first_sync_dialog = M59FirstTimeSyncDialog(newsgroup_name, count_to_sync, room_name, self)
        if self._active_first_sync_dialog.exec():
            console_log(f"User accepted First-Time Sync for {newsgroup_name} ({count_to_sync} messages).", "UI_SYNC_ACCEPT")
            self.archiver_worker.confirm_first_time_sync(newsgroup_name)
            self.handle_download_started(newsgroup_name, nid, count_to_sync, room_name)
        else:
            console_log(f"User dismissed First-Time Sync for {newsgroup_name}.", "UI_SYNC_DISMISS")
        self._active_first_sync_dialog = None

    @Slot(str, int, int, str)
    def handle_download_started(self, newsgroup_name: str, nid: int, total: int, room_name: str):
        clean_ng = newsgroup_name.replace("_", " ")
        self.lbl_banner_status.setText(f"📥 Archiving {clean_ng}: 0/{total} (0%)...")
        self.lbl_banner_status.setStyleSheet("font-size: 11px; font-weight: 700; color: #38bdf8;")
        self.banner_progress.setRange(0, max(1, total))
        self.banner_progress.setValue(0)
        self.download_banner.show()

    @Slot(str, int, int, str, int)
    def handle_download_progress(self, newsgroup_name: str, current: int, total: int, subject: str, percent: int):
        clean_ng = newsgroup_name.replace("_", " ")
        self.lbl_banner_status.setText(f"📥 Archiving {clean_ng}: {current}/{total} ({percent}%)...")
        self.banner_progress.setMaximum(max(1, total))
        self.banner_progress.setValue(current)
        if not self.download_banner.isVisible():
            self.download_banner.show()

    @Slot(str, int, int, int)
    def handle_download_completed(self, newsgroup_name: str, total_downloaded: int, unread_count: int, total_unread: int):
        clean_ng = newsgroup_name.replace("_", " ")
        self.lbl_banner_status.setText(f"✅ Synchronized {clean_ng} ({total_downloaded} messages archived)")
        self.lbl_banner_status.setStyleSheet("font-size: 11px; font-weight: 700; color: #34d399;")
        self.banner_progress.setValue(self.banner_progress.maximum())
        QTimer.singleShot(4000, self.download_banner.hide)
        self.load_articles_from_db()
        self.update_badge_counts()
        if total_downloaded > 0:
            self.signal_toast_requested.emit("Globe News", f"Synchronized {clean_ng} ({total_downloaded} articles archived)", "news")

    @Slot(str, int, int)
    def handle_unread_badge_updated(self, newsgroup_name: str, unread_count: int, total_unread: int):
        self.update_badge_counts()

