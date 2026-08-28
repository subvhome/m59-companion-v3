#!/usr/bin/env python3
"""
Meridian 59 - BGF Sprite & Animation Viewer (Standalone Tool)
============================================================
A comprehensive tool for inspecting, animating, and analyzing Meridian 59
.BGF (Bitmap Graphics File) assets to determine frame groupings, poses,
rotational angles, action timings, and hotspot attachments.

Features:
- Live animation playback with adjustable FPS (1-60 FPS) and presets.
- 4 Animation Modes:
    1. Action / Poses (cycle through poses at a fixed angle)
    2. 360° Turntable (rotate through all 6 angles for a fixed pose)
    3. Custom Frame Range (loop specific frames, e.g. 6-11 for attack/walk)
    4. Sequential (step through all raw bitmap frames 0..N-1)
- Loop Modes: Continuous Loop, Ping-Pong (Yo-yo), Play Once.
- Interactive scrubbers: Global Frame slider, Pose slider, Angle slider (0°-300°).
- Interactive Filmstrip thumbnail gallery with live playhead tracking.
- Hotspot & anchor crosshair inspection overlay.
- Onion-skinning (ghosting previous frame) for motion analysis.
- Zoom levels (1x, 2x, 3x, 4x, 6x, 8x, Fit to window) with nearest-neighbor crisp scaling.
- Background modes: Dark, Transparency Checkerboard, Black, White, Game Stone.
- PNG and GIF export tools.
- Auto-detects Steam & Non-Steam Meridian 59 installation resource directories.
- Quick Bestiary selector loaded from settings/moblist.csv.
"""

import sys
import os
import struct
import zlib
import getpass
import json

# Ensure PIL / Pillow is available
try:
    from PIL import Image, ImageDraw, ImageSequence
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# Ensure PySide6 or PyQt5 is available
try:
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QPushButton, QSlider, QSpinBox, QComboBox, QFileDialog,
        QScrollArea, QFrame, QSplitter, QCheckBox, QGroupBox, QGridLayout,
        QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QToolTip,
        QSizePolicy
    )
    from PySide6.QtCore import Qt, QTimer, QSize, QPoint, QRect
    from PySide6.QtGui import (
        QPixmap, QImage, QColor, QPainter, QPen, QBrush, QFont,
        QKeySequence, QShortcut, QIcon
    )
    HAS_PYSIDE6 = True
except ImportError:
    try:
        from PyQt5.QtWidgets import (
            QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
            QLabel, QPushButton, QSlider, QSpinBox, QComboBox, QFileDialog,
            QScrollArea, QFrame, QSplitter, QCheckBox, QGroupBox, QGridLayout,
            QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QToolTip,
            QSizePolicy
        )
        from PyQt5.QtCore import Qt, QTimer, QSize, QPoint, QRect
        from PyQt5.QtGui import (
            QPixmap, QImage, QColor, QPainter, QPen, QBrush, QFont,
            QKeySequence, QShortcut, QIcon
        )
        HAS_PYSIDE6 = True
    except ImportError:
        HAS_PYSIDE6 = False
        class QLabel: pass
        class QMainWindow: pass
        class QWidget: pass
        class QObject: pass

def resource_path(relative_path):
    """Get absolute path to resource, checking data/, settings/, or base folder."""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    
    direct = os.path.join(base_path, relative_path)
    if os.path.exists(direct):
        return direct
    fname = os.path.basename(relative_path)
    for folder in ["data", "settings", ""]:
        p = os.path.join(base_path, folder, fname) if folder else os.path.join(base_path, fname)
        if os.path.exists(p):
            return p
    return direct

def pil_to_qpixmap(pil_img):
    """Converts a PIL RGBA Image to a PySide6 QPixmap."""
    if not pil_img:
        return QPixmap()
    try:
        rgba = pil_img.convert("RGBA")
        data = rgba.tobytes("raw", "RGBA")
        qimg = QImage(data, rgba.width, rgba.height, QImage.Format_RGBA8888)
        return QPixmap.fromImage(qimg)
    except Exception as e:
        print(f"[BGF-VIEWER] Error converting PIL to QPixmap: {e}")
        return QPixmap()


class StandaloneBGFParser:
    """Standalone robust BGF loader with detailed metadata extraction."""
    def __init__(self, resource_dir=None):
        self.resource_dir = resource_dir
        self.palette = self._load_palette()

    def _load_palette(self):
        palette = []
        pal_path = resource_path("blakston.pal")
        if os.path.exists(pal_path):
            try:
                with open(pal_path, "r") as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) >= 3:
                            palette.extend([int(parts[0]), int(parts[1]), int(parts[2])])
            except Exception as e:
                print(f"[BGF-VIEWER] Error reading blakston.pal: {e}")
        
        if not palette:
            for i in range(256):
                palette.extend([i, i, i])

        if len(palette) < 768:
            palette.extend([0] * (768 - len(palette)))
        elif len(palette) > 768:
            palette = palette[:768]
        return palette

    def parse_bgf(self, filepath):
        """
        Parses a .bgf file and returns a structured dictionary:
        {
            'header': {'version': int, 'name': str, 'num_bitmaps': int, 'num_groups': int, 'max_indices': int, 'shrink': int},
            'frames': [
                {
                    'index': int,
                    'width': int,
                    'height': int,
                    'x_off': int,
                    'y_off': int,
                    'hotspots': {id: (hx, hy)},
                    'pil_image': PIL.Image,
                    'qpixmap': QPixmap
                }
            ]
        }
        """
        if not os.path.exists(filepath):
            return None

        try:
            with open(filepath, "rb") as f:
                magic = f.read(4)
                if magic != b"BGF\x11":
                    print(f"[BGF-VIEWER] Invalid BGF magic in {filepath}: {magic}")
                    return None

                version = struct.unpack("<I", f.read(4))[0]
                name = f.read(32).decode("ascii", "ignore").strip("\x00")
                num_bitmaps = struct.unpack("<I", f.read(4))[0]
                num_groups = struct.unpack("<I", f.read(4))[0]
                max_indices = struct.unpack("<I", f.read(4))[0]
                shrink = struct.unpack("<I", f.read(4))[0]

                header = {
                    "version": version,
                    "name": name,
                    "num_bitmaps": num_bitmaps,
                    "num_groups": num_groups,
                    "max_indices": max_indices,
                    "shrink": shrink,
                    "filepath": filepath,
                    "filename": os.path.basename(filepath)
                }

                frames = []
                for i in range(num_bitmaps):
                    width, height = struct.unpack("<II", f.read(8))
                    x_off, y_off = struct.unpack("<ii", f.read(8))

                    num_hotspots = struct.unpack("B", f.read(1))[0]
                    hotspots = {}
                    for _ in range(num_hotspots):
                        hn = struct.unpack("b", f.read(1))[0]
                        hx, hy = struct.unpack("<ii", f.read(8))
                        hotspots[hn] = (hx, hy)

                    is_comp = struct.unpack("B", f.read(1))[0]
                    if is_comp == 1:
                        comp_len = struct.unpack("<I", f.read(4))[0]
                        comp_data = f.read(comp_len)
                        try:
                            data = zlib.decompress(comp_data)
                        except Exception:
                            data = b"\x00" * (width * height)
                    else:
                        _ = struct.unpack("<I", f.read(4))[0]
                        data = f.read(width * height)

                    img = Image.new("P", (width, height))
                    img.putpalette(self.palette)
                    img.frombytes(data[:width * height])

                    rgba_img = img.convert("RGBA")
                    datas = rgba_img.getdata()
                    trans_color = tuple(self.palette[254 * 3:254 * 3 + 3])

                    new_data = []
                    for item in datas:
                        if item[0] == trans_color[0] and item[1] == trans_color[1] and item[2] == trans_color[2]:
                            new_data.append((0, 0, 0, 0))
                        else:
                            new_data.append(item)
                    rgba_img.putdata(new_data)

                    qpix = pil_to_qpixmap(rgba_img)

                    frames.append({
                        "index": i,
                        "width": width,
                        "height": height,
                        "x_off": x_off,
                        "y_off": y_off,
                        "hotspots": hotspots,
                        "pil_image": rgba_img,
                        "qpixmap": qpix
                    })

                return {
                    "header": header,
                    "frames": frames
                }
        except Exception as e:
            print(f"[BGF-VIEWER] Failed parsing BGF {filepath}: {e}")
            return None


class BGFCanvas(QLabel):
    """Custom interactive viewport widget for rendering sprites with zoom & overlays."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(320, 320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.current_frame = None
        self.prev_frame = None
        self.zoom_factor = 2.0  # 1.0, 2.0, 3.0, 4.0, 6.0, 8.0 or 0 (Fit)
        self.bg_mode = "Dark (#222)"
        self.show_hotspots = True
        self.show_onion = False
        self.show_origin = True

    def set_frame(self, frame_data, prev_frame_data=None):
        self.current_frame = frame_data
        self.prev_frame = prev_frame_data
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)

        cw = self.width()
        ch = self.height()

        # 1. Draw Background
        if self.bg_mode == "Dark (#222)":
            painter.fillRect(0, 0, cw, ch, QColor(34, 34, 34))
        elif self.bg_mode == "Black (#000)":
            painter.fillRect(0, 0, cw, ch, QColor(10, 10, 10))
        elif self.bg_mode == "White (#FFF)":
            painter.fillRect(0, 0, cw, ch, QColor(245, 245, 245))
        elif self.bg_mode == "Stone / Slate":
            painter.fillRect(0, 0, cw, ch, QColor(45, 55, 72))
        elif self.bg_mode == "Checkerboard":
            tile_size = 16
            c1 = QColor(40, 40, 40)
            c2 = QColor(60, 60, 60)
            for y in range(0, ch, tile_size):
                for x in range(0, cw, tile_size):
                    painter.fillRect(x, y, tile_size, tile_size, c1 if ((x // tile_size) + (y // tile_size)) % 2 == 0 else c2)

        if not self.current_frame:
            painter.setPen(QColor(150, 150, 150))
            painter.setFont(QFont("Segoe UI", 12))
            painter.drawText(self.rect(), Qt.AlignCenter, "No BGF sprite loaded\nOpen a .BGF file or select a monster from the list.")
            return

        pix = self.current_frame.get("qpixmap")
        if not pix or pix.isNull():
            return

        orig_w = pix.width()
        orig_h = pix.height()

        # Calculate Zoom & Scale
        if self.zoom_factor == 0:  # Fit to Window
            scale = min((cw - 40) / max(1, orig_w), (ch - 40) / max(1, orig_h))
            scale = max(0.5, scale)
        else:
            scale = self.zoom_factor

        target_w = int(orig_w * scale)
        target_h = int(orig_h * scale)

        dest_x = (cw - target_w) // 2
        dest_y = (ch - target_h) // 2

        # 2. Draw Origin / Grid Axes if enabled
        if self.show_origin:
            painter.setPen(QPen(QColor(80, 80, 80, 150), 1, Qt.DashLine))
            painter.drawLine(0, ch // 2, cw, ch // 2)
            painter.drawLine(cw // 2, 0, cw // 2, ch)

        # 3. Draw Onion Skin (Previous frame with 35% opacity)
        if self.show_onion and self.prev_frame and self.prev_frame != self.current_frame:
            prev_pix = self.prev_frame.get("qpixmap")
            if prev_pix and not prev_pix.isNull():
                painter.setOpacity(0.35)
                p_tw = int(prev_pix.width() * scale)
                p_th = int(prev_pix.height() * scale)
                p_dx = (cw - p_tw) // 2
                p_dy = (ch - p_th) // 2
                painter.drawPixmap(p_dx, p_dy, p_tw, p_th, prev_pix)
                painter.setOpacity(1.0)

        # 4. Draw Main Sprite Pixmap (Nearest Neighbor / Crisp pixel art)
        painter.drawPixmap(dest_x, dest_y, target_w, target_h, pix)

        # 5. Draw Hotspot Overlays
        if self.show_hotspots:
            hotspots = self.current_frame.get("hotspots", {})
            hotspot_names = {
                1: "Head (1)",
                11: "Eyes (11)",
                12: "Mouth (12)",
                13: "Hair (13)",
                14: "Nose (14)",
                21: "R-Arm (21)",
                22: "Weapon (22)",
                31: "L-Arm (31)",
                41: "Legs (41)"
            }

            for hn, (hx, hy) in hotspots.items():
                if hn < 0:  # Skip negative duplicate keys
                    continue
                # Hotspot coordinates relative to sprite top-left
                hs_screen_x = dest_x + int(hx * scale)
                hs_screen_y = dest_y + int(hy * scale)

                # Draw crosshair marker
                painter.setPen(QPen(QColor(255, 80, 80), 2))
                painter.drawLine(hs_screen_x - 6, hs_screen_y, hs_screen_x + 6, hs_screen_y)
                painter.drawLine(hs_screen_x, hs_screen_y - 6, hs_screen_x, hs_screen_y + 6)
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(QPoint(hs_screen_x, hs_screen_y), 4, 4)

                # Hotspot label tag
                label = hotspot_names.get(hn, f"HS {hn}")
                painter.setPen(QColor(255, 220, 100))
                painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
                painter.drawText(hs_screen_x + 8, hs_screen_y + 4, f"{label} ({hx},{hy})")


class BGFViewerApp(QMainWindow):
    def __init__(self, initial_filepath=None):
        super().__init__()
        self.setWindowTitle("Meridian 59 - BGF Sprite & Animation Explorer")
        self.resize(1280, 840)
        self.setMinimumSize(960, 600)

        # Core State
        self.parser = StandaloneBGFParser()
        self.current_bgf_data = None
        self.detected_resource_dir = self._detect_m59_dir()
        self.mob_mapping = self._load_mob_mapping()

        # Animation State
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._on_anim_tick)
        self.is_playing = False
        self.fps = 8
        self.anim_mode = "Action / Poses"  # Action, Turntable, Range, Sequential
        self.loop_mode = "Continuous Loop"  # Continuous Loop, Ping-Pong, Play Once
        self.anim_direction = 1  # 1 = forward, -1 = backward (for ping-pong)

        # Active Playback Indices
        self.current_frame_idx = 0
        self.prev_frame_idx = 0
        self.active_pose = 0
        self.active_angle = 0
        self.range_start = 0
        self.range_end = 0

        self._init_ui()

        # Load initial file or prompt
        if initial_filepath and os.path.exists(initial_filepath):
            self.load_bgf_file(initial_filepath)
        elif self.mob_mapping:
            # Load default monster (e.g., Avar or Ant) if available
            for def_mob in ["avar", "ant", "skeleton", "orc", "cow", "zombie"]:
                if def_mob in self.mob_mapping:
                    bgf_name = self.mob_mapping[def_mob]
                    found_path = self._find_bgf_path(bgf_name)
                    if found_path:
                        self.load_bgf_file(found_path)
                        break

    def _detect_m59_dir(self):
        """Attempts to locate Meridian 59 installation directory."""
        candidates = []
        try:
            local_app_data = os.environ.get("LOCALAPPDATA", f"C:\\Users\\{getpass.getuser()}\\AppData\\Local")
            candidates.extend([
                os.path.join(local_app_data, "Meridian 59", "resource"),
                os.path.join(local_app_data, "Meridian 59", "resource", "graphics"),
                "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Meridian 59\\resource",
                "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Meridian 59\\resource\\graphics",
                os.path.join(os.getcwd(), "resource"),
                os.path.join(os.getcwd(), "graphics")
            ])
        except Exception:
            pass

        for c in candidates:
            if os.path.exists(c):
                return c
        return os.getcwd()

    def _load_mob_mapping(self):
        """Loads moblist.csv mapping (name -> bgf)."""
        mapping = {}
        csv_path = resource_path("settings/moblist.csv")
        if os.path.exists(csv_path):
            try:
                with open(csv_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        parts = line.split(",")
                        if len(parts) >= 2:
                            clean_name = parts[0].strip().lower()
                            bgf_file = parts[1].strip()
                            mapping[clean_name] = bgf_file
            except Exception as e:
                print(f"[BGF-VIEWER] Error reading moblist.csv: {e}")
        return mapping

    def _find_bgf_path(self, bgf_name):
        """Searches for a .bgf file across resource and working dirs."""
        search_dirs = [
            self.detected_resource_dir,
            os.path.join(self.detected_resource_dir, "graphics") if self.detected_resource_dir else None,
            os.path.join(self.detected_resource_dir, "rooms") if self.detected_resource_dir else None,
            os.path.join(os.getcwd(), "resource"),
            os.path.join(os.getcwd(), "graphics"),
            os.getcwd()
        ]
        for d in search_dirs:
            if d and os.path.exists(d):
                cand = os.path.join(d, bgf_name)
                if os.path.exists(cand):
                    return cand
        return None

    def _init_ui(self):
        # Master Widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # 1. Top Control Bar (File opening, Bestiary dropdown, Search)
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        btn_open = QPushButton("📁 Open BGF...")
        btn_open.setFixedHeight(32)
        btn_open.clicked.connect(self._on_open_file_dialog)
        top_bar.addWidget(btn_open)

        btn_browse_dir = QPushButton("📂 Set Resource Dir...")
        btn_browse_dir.setFixedHeight(32)
        btn_browse_dir.clicked.connect(self._on_set_resource_dir)
        top_bar.addWidget(btn_browse_dir)

        # Bestiary Selector
        top_bar.addWidget(QLabel("Bestiary:"))
        self.combo_mobs = QComboBox()
        self.combo_mobs.setFixedHeight(32)
        self.combo_mobs.setMinimumWidth(180)
        self.combo_mobs.addItem("-- Select Monster Preset --", None)
        for mob_name, bgf_file in sorted(self.mob_mapping.items()):
            self.combo_mobs.addItem(f"{mob_name.title()} ({bgf_file})", bgf_file)
        self.combo_mobs.currentIndexChanged.connect(self._on_mob_selected)
        top_bar.addWidget(self.combo_mobs)

        top_bar.addStretch()

        # Viewport Zoom & Background dropdowns
        top_bar.addWidget(QLabel("Zoom:"))
        self.combo_zoom = QComboBox()
        self.combo_zoom.setFixedHeight(32)
        for z_name, z_val in [("1x (100%)", 1.0), ("2x (200%)", 2.0), ("3x (300%)", 3.0), ("4x (400%)", 4.0), ("6x (600%)", 6.0), ("8x (800%)", 8.0), ("Fit View", 0)]:
            self.combo_zoom.addItem(z_name, z_val)
        self.combo_zoom.setCurrentIndex(1)  # Default 2x
        self.combo_zoom.currentIndexChanged.connect(self._on_zoom_changed)
        top_bar.addWidget(self.combo_zoom)

        top_bar.addWidget(QLabel("Canvas:"))
        self.combo_bg = QComboBox()
        self.combo_bg.setFixedHeight(32)
        self.combo_bg.addItems(["Dark (#222)", "Checkerboard", "Black (#000)", "White (#FFF)", "Stone / Slate"])
        self.combo_bg.currentIndexChanged.connect(self._on_bg_changed)
        top_bar.addWidget(self.combo_bg)

        main_layout.addLayout(top_bar)

        # 2. Main Middle Body (Left: Canvas + Scrubbing; Right: Frame Inspector & Animations)
        splitter = QSplitter(Qt.Horizontal)

        # === LEFT PANEL: VIEWPORT & FILMSTRIP ===
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        self.canvas = BGFCanvas()
        left_layout.addWidget(self.canvas, stretch=1)

        # Canvas Overlays Checkboxes
        overlays_bar = QHBoxLayout()
        self.chk_hotspots = QCheckBox("Show Hotspots Crosshairs")
        self.chk_hotspots.setChecked(True)
        self.chk_hotspots.toggled.connect(lambda v: setattr(self.canvas, "show_hotspots", v) or self.canvas.update())
        overlays_bar.addWidget(self.chk_hotspots)

        self.chk_onion = QCheckBox("Onion Skinning (Ghosting)")
        self.chk_onion.setChecked(False)
        self.chk_onion.toggled.connect(lambda v: setattr(self.canvas, "show_onion", v) or self.canvas.update())
        overlays_bar.addWidget(self.chk_onion)

        self.chk_origin = QCheckBox("Center Crosshairs")
        self.chk_origin.setChecked(True)
        self.chk_origin.toggled.connect(lambda v: setattr(self.canvas, "show_origin", v) or self.canvas.update())
        overlays_bar.addWidget(self.chk_origin)
        overlays_bar.addStretch()

        left_layout.addLayout(overlays_bar)

        # Filmstrip / Thumbnails Bar
        film_group = QGroupBox("🎞️ Frame Filmstrip Gallery (Click to inspect)")
        film_layout = QVBoxLayout(film_group)
        film_layout.setContentsMargins(4, 4, 4, 4)

        self.filmstrip_list = QListWidget()
        self.filmstrip_list.setFixedHeight(110)
        self.filmstrip_list.setViewMode(QListWidget.IconMode)
        self.filmstrip_list.setIconSize(QSize(64, 64))
        self.filmstrip_list.setResizeMode(QListWidget.Adjust)
        self.filmstrip_list.setMovement(QListWidget.Static)
        self.filmstrip_list.setSpacing(6)
        self.filmstrip_list.currentRowChanged.connect(self._on_filmstrip_clicked)
        film_layout.addWidget(self.filmstrip_list)

        left_layout.addWidget(film_group)
        splitter.addWidget(left_widget)

        # === RIGHT PANEL: ANIMATION SUITE & METADATA ===
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setMinimumWidth(360)
        right_scroll.setMaximumWidth(450)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(6, 6, 6, 6)
        right_layout.setSpacing(10)

        # --- Group A: Playback & Animation Engine ---
        anim_group = QGroupBox("▶️ Animation Controls")
        anim_vbox = QVBoxLayout(anim_group)
        anim_vbox.setSpacing(8)

        # Mode Selector
        mode_hbox = QHBoxLayout()
        mode_hbox.addWidget(QLabel("Anim Mode:"))
        self.combo_anim_mode = QComboBox()
        self.combo_anim_mode.addItems([
            "Action / Poses (Cycle Poses at Angle)",
            "360° Turntable (Rotate Angles at Pose)",
            "Custom Frame Range (Loop A -> B)",
            "Sequential (All Frames 0..N)"
        ])
        self.combo_anim_mode.currentIndexChanged.connect(self._on_anim_mode_changed)
        mode_hbox.addWidget(self.combo_anim_mode, stretch=1)
        anim_vbox.addLayout(mode_hbox)

        # Play / Pause / Step Controls
        btn_row = QHBoxLayout()
        self.btn_play = QPushButton("▶ PLAY (Space)")
        self.btn_play.setFixedHeight(34)
        self.btn_play.setStyleSheet("font-weight: bold; background-color: #2b6cb0; color: white;")
        self.btn_play.clicked.connect(self.toggle_play)
        btn_row.addWidget(self.btn_play, stretch=2)

        self.btn_prev = QPushButton("⏮ Prev")
        self.btn_prev.setFixedHeight(34)
        self.btn_prev.clicked.connect(self.step_prev)
        btn_row.addWidget(self.btn_prev, stretch=1)

        self.btn_next = QPushButton("Next ⏭")
        self.btn_next.setFixedHeight(34)
        self.btn_next.clicked.connect(self.step_next)
        btn_row.addWidget(self.btn_next, stretch=1)
        anim_vbox.addLayout(btn_row)

        # Speed / FPS Control
        fps_hbox = QHBoxLayout()
        fps_hbox.addWidget(QLabel("Speed (FPS):"))
        self.slider_fps = QSlider(Qt.Horizontal)
        self.slider_fps.setRange(1, 40)
        self.slider_fps.setValue(self.fps)
        self.slider_fps.valueChanged.connect(self._on_fps_slider_changed)
        fps_hbox.addWidget(self.slider_fps, stretch=1)

        self.lbl_fps = QLabel(f"{self.fps} FPS")
        self.lbl_fps.setFixedWidth(50)
        fps_hbox.addWidget(self.lbl_fps)
        anim_vbox.addLayout(fps_hbox)

        # FPS Presets
        presets_hbox = QHBoxLayout()
        presets_hbox.addWidget(QLabel("Presets:"))
        for p_val in [4, 8, 12, 16, 24]:
            btn_p = QPushButton(f"{p_val}")
            btn_p.setFixedWidth(36)
            btn_p.clicked.connect(lambda _, v=p_val: self.slider_fps.setValue(v))
            presets_hbox.addWidget(btn_p)
        presets_hbox.addStretch()
        anim_vbox.addLayout(presets_hbox)

        # Loop Mode
        loop_hbox = QHBoxLayout()
        loop_hbox.addWidget(QLabel("Loop:"))
        self.combo_loop = QComboBox()
        self.combo_loop.addItems(["Continuous Loop", "Ping-Pong (Yo-yo)", "Play Once"])
        self.combo_loop.currentIndexChanged.connect(lambda: setattr(self, "loop_mode", self.combo_loop.currentText()))
        loop_hbox.addWidget(self.combo_loop, stretch=1)
        anim_vbox.addLayout(loop_hbox)

        # Custom Frame Range Row
        range_hbox = QHBoxLayout()
        range_hbox.addWidget(QLabel("Range:"))
        self.spin_range_start = QSpinBox()
        self.spin_range_start.setRange(0, 999)
        self.spin_range_start.valueChanged.connect(self._on_range_changed)
        range_hbox.addWidget(self.spin_range_start)

        range_hbox.addWidget(QLabel("to"))
        self.spin_range_end = QSpinBox()
        self.spin_range_end.setRange(0, 999)
        self.spin_range_end.valueChanged.connect(self._on_range_changed)
        range_hbox.addWidget(self.spin_range_end)

        btn_set_range = QPushButton("Set Range")
        btn_set_range.clicked.connect(self._apply_custom_range)
        range_hbox.addWidget(btn_set_range)
        anim_vbox.addLayout(range_hbox)

        right_layout.addWidget(anim_group)

        # --- Group B: Pose & Angle Scrubbers ---
        scrub_group = QGroupBox("🎛️ Frame & Pose Scrubbers")
        scrub_vbox = QVBoxLayout(scrub_group)
        scrub_vbox.setSpacing(6)

        # Layout Scheme Selector
        scheme_hbox = QHBoxLayout()
        scheme_hbox.addWidget(QLabel("Layout Scheme:"))
        self.combo_layout_scheme = QComboBox()
        self.combo_layout_scheme.addItems([
            "Auto-Detect (Engine Pattern)",
            "Direction-Major (Spider / Groups=6)",
            "Angle-Major (Avar / Stride=6)",
            "Sequential / Non-Directional"
        ])
        self.combo_layout_scheme.currentIndexChanged.connect(self._on_layout_scheme_changed)
        scheme_hbox.addWidget(self.combo_layout_scheme, stretch=1)
        scrub_vbox.addLayout(scheme_hbox)

        # Master Frame Slider
        scrub_vbox.addWidget(QLabel("Global Frame Index:"))
        f_hbox = QHBoxLayout()
        self.slider_frame = QSlider(Qt.Horizontal)
        self.slider_frame.setRange(0, 0)
        self.slider_frame.valueChanged.connect(self._on_global_frame_slider_changed)
        f_hbox.addWidget(self.slider_frame, stretch=1)
        self.lbl_frame_idx = QLabel("0 / 0")
        self.lbl_frame_idx.setFixedWidth(60)
        f_hbox.addWidget(self.lbl_frame_idx)
        scrub_vbox.addLayout(f_hbox)

        # Pose Slider
        scrub_vbox.addWidget(QLabel("Pose (Action State):"))
        p_hbox = QHBoxLayout()
        self.slider_pose = QSlider(Qt.Horizontal)
        self.slider_pose.setRange(0, 0)
        self.slider_pose.valueChanged.connect(self._on_pose_slider_changed)
        p_hbox.addWidget(self.slider_pose, stretch=1)
        self.lbl_pose_idx = QLabel("Pose 0")
        self.lbl_pose_idx.setFixedWidth(60)
        p_hbox.addWidget(self.lbl_pose_idx)
        scrub_vbox.addLayout(p_hbox)

        # Angle Slider
        scrub_vbox.addWidget(QLabel("Angle (Facing Direction):"))
        a_hbox = QHBoxLayout()
        self.slider_angle = QSlider(Qt.Horizontal)
        self.slider_angle.setRange(0, 5)
        self.slider_angle.valueChanged.connect(self._on_angle_slider_changed)
        a_hbox.addWidget(self.slider_angle, stretch=1)
        self.lbl_angle_idx = QLabel("0° (0/5)")
        self.lbl_angle_idx.setFixedWidth(70)
        a_hbox.addWidget(self.lbl_angle_idx)
        scrub_vbox.addLayout(a_hbox)

        right_layout.addWidget(scrub_group)

        # --- Group C: Frame & BGF Metadata ---
        meta_group = QGroupBox("ℹ️ Frame & BGF Info")
        meta_vbox = QVBoxLayout(meta_group)
        meta_vbox.setSpacing(4)

        self.lbl_meta_file = QLabel("<b>File:</b> None")
        meta_vbox.addWidget(self.lbl_meta_file)

        self.lbl_meta_dims = QLabel("<b>Dimensions:</b> 0 x 0 px")
        meta_vbox.addWidget(self.lbl_meta_dims)

        self.lbl_meta_offsets = QLabel("<b>Offsets:</b> X: 0, Y: 0")
        meta_vbox.addWidget(self.lbl_meta_offsets)

        self.lbl_meta_counts = QLabel("<b>Total Frames:</b> 0 | <b>Bitmaps:</b> 0")
        meta_vbox.addWidget(self.lbl_meta_counts)

        self.lbl_meta_hotspots = QLabel("<b>Hotspots:</b> None")
        self.lbl_meta_hotspots.setWordWrap(True)
        meta_vbox.addWidget(self.lbl_meta_hotspots)

        btn_copy_meta = QPushButton("📋 Copy Frame Info to Clipboard")
        btn_copy_meta.clicked.connect(self._copy_frame_info)
        meta_vbox.addWidget(btn_copy_meta)

        right_layout.addWidget(meta_group)

        # --- Group D: Export Actions ---
        export_group = QGroupBox("💾 Export Assets")
        export_vbox = QVBoxLayout(export_group)

        btn_exp_png = QPushButton("Export Current Frame (PNG)...")
        btn_exp_png.clicked.connect(self._export_current_frame_png)
        export_vbox.addWidget(btn_exp_png)

        btn_exp_seq = QPushButton("Export All Frames (PNG Sequence)...")
        btn_exp_seq.clicked.connect(self._export_all_frames_png)
        export_vbox.addWidget(btn_exp_seq)

        btn_exp_gif = QPushButton("Export Active Animation (GIF)...")
        btn_exp_gif.clicked.connect(self._export_animation_gif)
        export_vbox.addWidget(btn_exp_gif)

        right_layout.addWidget(export_group)
        right_layout.addStretch()

        right_scroll.setWidget(right_widget)
        splitter.addWidget(right_scroll)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        main_layout.addWidget(splitter, stretch=1)

        # 3. Status Bar
        self.lbl_status = QLabel("Ready. Select or open a BGF asset to begin.")
        self.statusBar().addWidget(self.lbl_status)

        # 4. Keyboard Shortcuts
        QShortcut(QKeySequence(Qt.Key_Space), self, self.toggle_play)
        QShortcut(QKeySequence(Qt.Key_Left), self, self.step_prev)
        QShortcut(QKeySequence(Qt.Key_Right), self, self.step_next)
        QShortcut(QKeySequence(Qt.Key_Up), self, lambda: self.slider_pose.setValue(self.slider_pose.value() + 1))
        QShortcut(QKeySequence(Qt.Key_Down), self, lambda: self.slider_pose.setValue(self.slider_pose.value() - 1))

        self._apply_dark_theme()

    def _apply_dark_theme(self):
        """Modern Dark UI stylesheet."""
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #1a202c;
                color: #e2e8f0;
                font-family: 'Segoe UI', sans-serif;
                font-size: 13px;
            }
            QGroupBox {
                border: 1px solid #2d3748;
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 14px;
                font-weight: bold;
                color: #63b3ed;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QPushButton {
                background-color: #2d3748;
                border: 1px solid #4a5568;
                border-radius: 4px;
                padding: 4px 10px;
                color: #edf2f7;
            }
            QPushButton:hover {
                background-color: #4a5568;
                border-color: #718096;
            }
            QPushButton:pressed {
                background-color: #1a365d;
            }
            QComboBox, QSpinBox, QLineEdit {
                background-color: #2d3748;
                border: 1px solid #4a5568;
                border-radius: 4px;
                padding: 4px 8px;
                color: #edf2f7;
            }
            QListWidget {
                background-color: #171923;
                border: 1px solid #2d3748;
                border-radius: 6px;
            }
            QListWidget::item:selected {
                background-color: #2b6cb0;
                border-radius: 4px;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #2d3748;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #3182ce;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #90cdf4;
                border: 1px solid #2b6cb0;
                width: 14px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 7px;
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
        """)

    # =========================================================================
    # File Loading & Parsing
    # =========================================================================

    def _on_open_file_dialog(self):
        start_dir = self.detected_resource_dir or os.getcwd()
        path, _ = QFileDialog.getOpenFileName(self, "Open Meridian 59 BGF Sprite", start_dir, "BGF Graphics (*.bgf);;All Files (*.*)")
        if path:
            self.load_bgf_file(path)

    def _on_set_resource_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select Meridian 59 Resource Directory", self.detected_resource_dir or os.getcwd())
        if d:
            self.detected_resource_dir = d
            self.lbl_status.setText(f"Resource directory set: {d}")

    def _on_mob_selected(self, index):
        bgf_file = self.combo_mobs.currentData()
        if not bgf_file:
            return
        path = self._find_bgf_path(bgf_file)
        if path and os.path.exists(path):
            self.load_bgf_file(path)
        else:
            QMessageBox.warning(self, "BGF Not Found", f"Could not find '{bgf_file}' in resource directory:\n{self.detected_resource_dir}\n\nPlease click 'Set Resource Dir...' to point to your Meridian 59 resource folder.")

    def _get_layout_scheme(self):
        """Returns 'direction_major', 'angle_major', or 'sequential'."""
        if not self.current_bgf_data:
            return "sequential"
        choice = self.combo_layout_scheme.currentIndex()
        if choice == 1:
            return "direction_major"
        elif choice == 2:
            return "angle_major"
        elif choice == 3:
            return "sequential"

        # Auto-Detect
        num_frames = len(self.current_bgf_data["frames"])
        if num_frames < 6:
            return "sequential"
        num_groups = self.current_bgf_data.get("header", {}).get("num_groups", 1)
        if num_groups == 6 or (num_frames in (12, 18, 24) and num_groups > 1):
            return "direction_major"
        return "angle_major"

    def _calc_frame_index(self, pose, angle):
        """Calculates flat frame index based on active layout scheme."""
        if not self.current_bgf_data:
            return 0
        num_frames = len(self.current_bgf_data["frames"])
        if num_frames <= 0:
            return 0
        scheme = self._get_layout_scheme()
        if scheme == "direction_major":
            poses_per_angle = max(1, num_frames // 6)
            clamped_pose = max(0, min(pose, poses_per_angle - 1))
            clamped_angle = max(0, min(angle, 5))
            return min((clamped_angle * poses_per_angle) + clamped_pose, num_frames - 1)
        elif scheme == "angle_major":
            max_pose = max(0, (num_frames // 6) - 1)
            clamped_pose = max(0, min(pose, max_pose))
            clamped_angle = max(0, min(angle, 5))
            return min((clamped_pose * 6) + clamped_angle, num_frames - 1)
        else:
            return max(0, min(max(pose, angle), num_frames - 1))

    def _calc_pose_angle(self, index):
        """Calculates (pose, angle) from flat frame index based on active layout scheme."""
        if not self.current_bgf_data:
            return 0, 0
        num_frames = len(self.current_bgf_data["frames"])
        if num_frames <= 0:
            return 0, 0
        clamped_idx = max(0, min(index, num_frames - 1))
        scheme = self._get_layout_scheme()
        if scheme == "direction_major":
            poses_per_angle = max(1, num_frames // 6)
            angle = clamped_idx // poses_per_angle
            pose = clamped_idx % poses_per_angle
            return pose, angle
        elif scheme == "angle_major":
            pose = clamped_idx // 6
            angle = clamped_idx % 6
            return pose, angle
        else:
            return 0, clamped_idx

    def _update_slider_ranges(self):
        """Updates pose & angle slider limits based on active layout scheme."""
        if not self.current_bgf_data:
            return
        num_frames = len(self.current_bgf_data["frames"])
        scheme = self._get_layout_scheme()

        self.slider_pose.blockSignals(True)
        self.slider_angle.blockSignals(True)

        if scheme == "direction_major":
            poses_per_angle = max(1, num_frames // 6)
            max_pose = max(0, poses_per_angle - 1)
            self.slider_pose.setEnabled(max_pose > 0)
            self.slider_pose.setRange(0, max_pose)
            self.slider_angle.setEnabled(True)
            self.slider_angle.setRange(0, 5)
        elif scheme == "angle_major":
            max_pose = max(0, (num_frames // 6) - 1)
            self.slider_pose.setEnabled(max_pose > 0)
            self.slider_pose.setRange(0, max_pose)
            self.slider_angle.setEnabled(True)
            self.slider_angle.setRange(0, 5)
        else:
            self.slider_pose.setEnabled(False)
            self.slider_pose.setRange(0, 0)
            self.slider_angle.setEnabled(num_frames > 1)
            self.slider_angle.setRange(0, max(0, num_frames - 1))

        self.slider_pose.blockSignals(False)
        self.slider_angle.blockSignals(False)

    def _on_layout_scheme_changed(self, idx):
        self._update_slider_ranges()
        if self.current_bgf_data:
            self._display_frame(self.current_frame_idx)

    def load_bgf_file(self, filepath):
        """Loads and parses a .bgf file, populating filmstrip, sliders, and controls."""
        self.stop_play()

        data = self.parser.parse_bgf(filepath)
        if not data or not data.get("frames"):
            QMessageBox.critical(self, "Load Error", f"Failed to parse BGF file:\n{filepath}\nEnsure it is a valid Meridian 59 BGF v17 asset.")
            return

        self.current_bgf_data = data
        frames = data["frames"]
        num_frames = len(frames)

        # Update Frame Sliders Range
        self.slider_frame.blockSignals(True)
        self.slider_frame.setRange(0, num_frames - 1)
        self.slider_frame.setValue(0)
        self.slider_frame.blockSignals(False)

        # Calculate Poses & Angles ranges
        self._update_slider_ranges()
        self.slider_pose.blockSignals(True)
        self.slider_pose.setValue(0)
        self.slider_pose.blockSignals(False)
        self.slider_angle.blockSignals(True)
        self.slider_angle.setValue(0)
        self.slider_angle.blockSignals(False)

        # Update Range spinboxes
        self.spin_range_start.setRange(0, num_frames - 1)
        self.spin_range_start.setValue(0)
        self.spin_range_end.setRange(0, num_frames - 1)
        self.spin_range_end.setValue(min(5, num_frames - 1) if num_frames >= 6 else num_frames - 1)
        self.range_start = 0
        self.range_end = self.spin_range_end.value()

        # Populate Filmstrip Gallery
        self.filmstrip_list.blockSignals(True)
        self.filmstrip_list.clear()
        for f in frames:
            idx = f["index"]
            pix = f["qpixmap"]
            item = QListWidgetItem()
            item.setText(f"#{idx}\n{f['width']}x{f['height']}")
            item.setTextAlignment(Qt.AlignCenter)
            if pix and not pix.isNull():
                thumb = pix.scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                item.setIcon(QIcon(thumb))
            self.filmstrip_list.addItem(item)
        self.filmstrip_list.blockSignals(False)
        self.filmstrip_list.setCurrentRow(0)

        # Set initial frame
        self.current_frame_idx = 0
        self.prev_frame_idx = 0
        self._display_frame(0)

        # Update File Header metadata
        hdr = data["header"]
        scheme = self._get_layout_scheme()
        scheme_label = "Direction-Major (6 dirs)" if scheme == "direction_major" else ("Angle-Major (6 angles/pose)" if scheme == "angle_major" else "Sequential")
        self.lbl_meta_file.setText(f"<b>File:</b> {hdr['filename']} (v{hdr['version']})")
        self.lbl_meta_counts.setText(f"<b>Frames:</b> {num_frames} | <b>Groups:</b> {hdr['num_groups']} | <b>Bitmaps:</b> {hdr['num_bitmaps']}<br><b>Sequence Scheme:</b> {scheme_label}")
        self.lbl_status.setText(f"Loaded '{hdr['filename']}' ({num_frames} frames, {scheme_label}). Ready to analyze and animate.")

    # =========================================================================
    # Animation Playback Engine
    # =========================================================================

    def toggle_play(self):
        if not self.current_bgf_data:
            return
        if self.is_playing:
            self.stop_play()
        else:
            self.start_play()

    def start_play(self):
        if not self.current_bgf_data:
            return
        self.is_playing = True
        self.btn_play.setText("⏸ PAUSE (Space)")
        self.btn_play.setStyleSheet("font-weight: bold; background-color: #c53030; color: white;")
        interval_ms = max(10, int(1000 / max(1, self.fps)))
        self.anim_timer.start(interval_ms)

    def stop_play(self):
        self.is_playing = False
        self.anim_timer.stop()
        self.btn_play.setText("▶ PLAY (Space)")
        self.btn_play.setStyleSheet("font-weight: bold; background-color: #2b6cb0; color: white;")

    def step_next(self):
        self.stop_play()
        self._advance_frame(step=1)

    def step_prev(self):
        self.stop_play()
        self._advance_frame(step=-1)

    def _on_fps_slider_changed(self, val):
        self.fps = val
        self.lbl_fps.setText(f"{val} FPS")
        if self.is_playing:
            interval_ms = max(10, int(1000 / max(1, self.fps)))
            self.anim_timer.setInterval(interval_ms)

    def _on_anim_mode_changed(self, idx):
        self.anim_mode = self.combo_anim_mode.currentText()

    def _on_range_changed(self):
        self.range_start = min(self.spin_range_start.value(), self.spin_range_end.value())
        self.range_end = max(self.spin_range_start.value(), self.spin_range_end.value())

    def _apply_custom_range(self):
        self.combo_anim_mode.setCurrentIndex(2)  # Custom Frame Range
        self.current_frame_idx = self.range_start
        self._display_frame(self.current_frame_idx)
        self.start_play()

    def _on_anim_tick(self):
        self._advance_frame(step=self.anim_direction)

    def _advance_frame(self, step=1):
        """Advances playback according to the active animation mode."""
        if not self.current_bgf_data:
            return

        frames = self.current_bgf_data["frames"]
        num_frames = len(frames)
        if num_frames == 0:
            return

        mode = self.combo_anim_mode.currentText()
        scheme = self._get_layout_scheme()

        # MODE 1: Action / Poses (Cycle Poses at selected fixed Angle)
        if "Action / Poses" in mode:
            if scheme in ("direction_major", "angle_major") and num_frames >= 6:
                if scheme == "direction_major":
                    poses_per_angle = max(1, num_frames // 6)
                    max_pose = max(0, poses_per_angle - 1)
                else:
                    max_pose = max(0, (num_frames // 6) - 1)

                next_pose = self.active_pose + step
                if next_pose > max_pose:
                    if self.loop_mode == "Ping-Pong (Yo-yo)":
                        self.anim_direction = -1
                        next_pose = max(0, max_pose - 1)
                    elif self.loop_mode == "Play Once":
                        self.stop_play()
                        return
                    else:
                        next_pose = 0
                elif next_pose < 0:
                    if self.loop_mode == "Ping-Pong (Yo-yo)":
                        self.anim_direction = 1
                        next_pose = min(max_pose, 1)
                    else:
                        next_pose = max_pose

                self.active_pose = max(0, min(max_pose, next_pose))
                target_frame = self._calc_frame_index(self.active_pose, self.active_angle)
            else:
                target_frame = (self.current_frame_idx + step) % num_frames

        # MODE 2: 360° Turntable (Rotate Angles at selected fixed Pose)
        elif "Turntable" in mode:
            if scheme in ("direction_major", "angle_major") and num_frames >= 6:
                next_angle = (self.active_angle + step) % 6
                self.active_angle = next_angle
                target_frame = self._calc_frame_index(self.active_pose, self.active_angle)
            else:
                target_frame = (self.current_frame_idx + step) % num_frames

        # MODE 3: Custom Frame Range
        elif "Custom Frame Range" in mode:
            r_min = min(self.range_start, self.range_end)
            r_max = max(self.range_start, self.range_end)
            next_idx = self.current_frame_idx + step
            if next_idx > r_max:
                if self.loop_mode == "Ping-Pong (Yo-yo)":
                    self.anim_direction = -1
                    next_idx = max(r_min, r_max - 1)
                elif self.loop_mode == "Play Once":
                    self.stop_play()
                    return
                else:
                    next_idx = r_min
            elif next_idx < r_min:
                if self.loop_mode == "Ping-Pong (Yo-yo)":
                    self.anim_direction = 1
                    next_idx = min(r_max, r_min + 1)
                else:
                    next_idx = r_max
            target_frame = next_idx

        # MODE 4: Sequential (All frames 0..N-1)
        else:
            next_idx = self.current_frame_idx + step
            if next_idx >= num_frames:
                if self.loop_mode == "Ping-Pong (Yo-yo)":
                    self.anim_direction = -1
                    next_idx = max(0, num_frames - 2)
                elif self.loop_mode == "Play Once":
                    self.stop_play()
                    return
                else:
                    next_idx = 0
            elif next_idx < 0:
                if self.loop_mode == "Ping-Pong (Yo-yo)":
                    self.anim_direction = 1
                    next_idx = min(num_frames - 1, 1)
                else:
                    next_idx = num_frames - 1
            target_frame = next_idx

        target_frame = max(0, min(num_frames - 1, target_frame))
        self._display_frame(target_frame)

    # =========================================================================
    # Frame Rendering & Display Sync
    # =========================================================================

    def _display_frame(self, index):
        if not self.current_bgf_data:
            return
        frames = self.current_bgf_data["frames"]
        num_frames = len(frames)
        if index < 0 or index >= num_frames:
            return

        self.prev_frame_idx = self.current_frame_idx
        self.current_frame_idx = index
        frame = frames[index]
        prev_frame = frames[self.prev_frame_idx]

        # Calculate pose & angle for this index according to scheme
        self.active_pose, self.active_angle = self._calc_pose_angle(index)
        scheme = self._get_layout_scheme()

        # Render on Canvas
        self.canvas.set_frame(frame, prev_frame)

        # Sync Sliders without triggering infinite loops
        self.slider_frame.blockSignals(True)
        self.slider_frame.setValue(index)
        self.slider_frame.blockSignals(False)
        self.lbl_frame_idx.setText(f"{index} / {num_frames-1}")

        self.slider_pose.blockSignals(True)
        self.slider_pose.setValue(self.active_pose)
        self.slider_pose.blockSignals(False)
        self.lbl_pose_idx.setText(f"Pose {self.active_pose}")

        self.slider_angle.blockSignals(True)
        self.slider_angle.setValue(self.active_angle)
        self.slider_angle.blockSignals(False)
        if scheme in ("direction_major", "angle_major") and num_frames >= 6:
            self.lbl_angle_idx.setText(f"{self.active_angle*60}° ({self.active_angle}/5)")
        else:
            self.lbl_angle_idx.setText(f"Idx {self.active_angle}")

        # Sync Filmstrip
        self.filmstrip_list.blockSignals(True)
        self.filmstrip_list.setCurrentRow(index)
        self.filmstrip_list.blockSignals(False)

        # Update Detailed Metadata Text
        self.lbl_meta_dims.setText(f"<b>Dimensions:</b> {frame['width']} × {frame['height']} px")
        self.lbl_meta_offsets.setText(f"<b>Offsets:</b> X: {frame['x_off']}, Y: {frame['y_off']}")

        hotspots = frame.get("hotspots", {})
        if hotspots:
            hs_strs = []
            for hn, (hx, hy) in sorted(hotspots.items()):
                if hn >= 0:
                    hs_strs.append(f"#{hn}: ({hx}, {hy})")
            self.lbl_meta_hotspots.setText(f"<b>Hotspots:</b> {', '.join(hs_strs)}")
        else:
            self.lbl_meta_hotspots.setText("<b>Hotspots:</b> None")

    def _on_global_frame_slider_changed(self, val):
        self.stop_play()
        self._display_frame(val)

    def _on_pose_slider_changed(self, val):
        self.stop_play()
        self.active_pose = val
        target = self._calc_frame_index(self.active_pose, self.active_angle)
        self._display_frame(target)

    def _on_angle_slider_changed(self, val):
        self.stop_play()
        self.active_angle = val
        target = self._calc_frame_index(self.active_pose, self.active_angle)
        self._display_frame(target)

    def _on_filmstrip_clicked(self, row):
        if row >= 0:
            self.stop_play()
            self._display_frame(row)

    def _on_zoom_changed(self):
        z_val = self.combo_zoom.currentData()
        self.canvas.zoom_factor = z_val
        self.canvas.update()

    def _on_bg_changed(self):
        self.canvas.bg_mode = self.combo_bg.currentText()
        self.canvas.update()

    # =========================================================================
    # Exporting & Clipboard
    # =========================================================================

    def _copy_frame_info(self):
        if not self.current_bgf_data:
            return
        frame = self.current_bgf_data["frames"][self.current_frame_idx]
        hdr = self.current_bgf_data["header"]
        info = (
            f"Asset: {hdr['filename']}\n"
            f"Frame: {frame['index'] + 1}/{len(self.current_bgf_data['frames'])} (0-based: #{frame['index']})\n"
            f"Pose: {self.active_pose} | Angle: {self.active_angle * 60}° (#{self.active_angle})\n"
            f"Size: {frame['width']}x{frame['height']} px | Offsets: ({frame['x_off']}, {frame['y_off']})\n"
            f"Hotspots: {frame.get('hotspots', {})}"
        )
        QApplication.clipboard().setText(info)
        self.lbl_status.setText(f"Copied Frame #{frame['index']} metadata to clipboard.")

    def _export_current_frame_png(self):
        if not self.current_bgf_data:
            return
        frame = self.current_bgf_data["frames"][self.current_frame_idx]
        hdr = self.current_bgf_data["header"]
        def_name = f"{os.path.splitext(hdr['filename'])[0]}_frame_{frame['index']:03d}.png"
        path, _ = QFileDialog.getSaveFileName(self, "Export Frame PNG", def_name, "PNG Image (*.png)")
        if path:
            frame["pil_image"].save(path, "PNG")
            self.lbl_status.setText(f"Exported frame PNG: {path}")

    def _export_all_frames_png(self):
        if not self.current_bgf_data:
            return
        hdr = self.current_bgf_data["header"]
        d = QFileDialog.getExistingDirectory(self, "Select Export Folder for PNG Sequence", os.getcwd())
        if d:
            base_name = os.path.splitext(hdr["filename"])[0]
            for f in self.current_bgf_data["frames"]:
                out_name = f"{base_name}_frame_{f['index']:03d}_pose{f['index']//6}_ang{(f['index']%6)*60}deg.png"
                out_path = os.path.join(d, out_name)
                f["pil_image"].save(out_path, "PNG")
            QMessageBox.information(self, "Export Complete", f"Exported {len(self.current_bgf_data['frames'])} PNG frames to:\n{d}")

    def _export_animation_gif(self):
        if not self.current_bgf_data:
            return
        hdr = self.current_bgf_data["header"]
        def_name = f"{os.path.splitext(hdr['filename'])[0]}_anim.gif"
        path, _ = QFileDialog.getSaveFileName(self, "Export Animation GIF", def_name, "GIF Image (*.gif)")
        if path:
            frames = self.current_bgf_data["frames"]
            num_frames = len(frames)
            gif_images = []

            mode = self.combo_anim_mode.currentText()
            if "Action / Poses" in mode and num_frames >= 6:
                max_pose = max(0, (num_frames // 6) - 1)
                for p in range(max_pose + 1):
                    idx = (p * 6) + self.active_angle
                    if idx < num_frames:
                        gif_images.append(frames[idx]["pil_image"])
            elif "Turntable" in mode and num_frames >= 6:
                for a in range(6):
                    idx = (self.active_pose * 6) + a
                    if idx < num_frames:
                        gif_images.append(frames[idx]["pil_image"])
            elif "Custom Frame Range" in mode:
                for i in range(self.range_start, self.range_end + 1):
                    if i < num_frames:
                        gif_images.append(frames[i]["pil_image"])
            else:
                for f in frames:
                    gif_images.append(f["pil_image"])

            if gif_images:
                duration_ms = max(20, int(1000 / max(1, self.fps)))
                gif_images[0].save(
                    path,
                    save_all=True,
                    append_images=gif_images[1:],
                    duration=duration_ms,
                    loop=0,
                    disposal=2
                )
                self.lbl_status.setText(f"Exported GIF animation: {path}")


def main():
    if not HAS_PYSIDE6:
        print("=" * 60)
        print("ERROR: PySide6 is required to run the BGF Viewer GUI.")
        print("Please install it in your Python environment via:")
        print("    pip install PySide6 Pillow")
        print("=" * 60)
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Support passing a .bgf file via command line argument: python m59_bgf_viewer.py avar.bgf
    initial_file = sys.argv[1] if len(sys.argv) > 1 else None
    viewer = BGFViewerApp(initial_file)
    viewer.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
