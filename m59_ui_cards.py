# -*- coding: utf-8 -*-
"""
M59 Card & Chart Layout Module
Provides:
- GridReorderContainer: Drag-and-drop grid container with layout reordering
- ReorderableCard: Collapsible, detachable, and reorderable dashboard cards
- ReorderableSubCard: Embedded sub-card components
- ReagentTrendChartWidget: Custom QPainter historical reagent depletion chart
- PKGraphChartWidget: Real-time PK ratio / combat outcome bar chart
"""

import sys
import os
import time
import math

try:
    from PySide6.QtWidgets import (
        QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
        QFrame, QSizePolicy, QMenu, QToolTip, QScrollArea
    )
    from PySide6.QtCore import Qt, QTimer, Signal, QObject, QPoint, QRect, QEvent, QSize, QMimeData
    from PySide6.QtGui import (
        QFont, QIcon, QColor, QPixmap, QImage, QPainter, QPen, QBrush,
        QLinearGradient, QRadialGradient, QCursor, QDrag
    )
except ImportError:
    class _DummyQt:
        PointingHandCursor = None
        Horizontal = None
        WindowContextHelpButtonHint = 0
        def __getattr__(self, name):
            return 0
    QApplication = QWidget = QVBoxLayout = QHBoxLayout = QGridLayout = QLabel = QPushButton = object
    QFrame = QSizePolicy = QMenu = QToolTip = QScrollArea = object
    Qt = _DummyQt()
    QTimer = QObject = QPoint = QRect = QEvent = QSize = QMimeData = object
    Signal = lambda *a, **k: None
    QFont = QIcon = QColor = QPixmap = QImage = QPainter = QPen = QBrush = object
    QLinearGradient = QRadialGradient = QCursor = QDrag = object

from m59_utils import resource_path

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
# PK Combat Analytics & Target Intelligence Chart Widget and Dialog
# ----------------------------------------------------------------------
class PKGraphChartWidget(QWidget):
    """
    Custom QPainter dark-mode chart widget for visualizing Player Kills (PKs).
    Supports 3 modes:
    - 'Hourly Distribution': 24 bars (00:00 to 23:00) showing PK count by hour of day
    - 'Day of Week': 7 bars (Mon to Sun) showing PK count by day
    - 'Top PK Targets': Horizontal/Vertical bars for top target player victims
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.history_records = []
        self.target_filter = "All PK Targets"
        self.timeframe_filter = "All Time"
        self.chart_mode = "Hourly Distribution"
        self.hover_idx = -1
        self.setMouseTracking(True)

    def set_data(self, history_records, target_filter="All PK Targets", timeframe_filter="All Time", chart_mode="Hourly Distribution"):
        self.history_records = history_records or []
        self.target_filter = target_filter or "All PK Targets"
        self.timeframe_filter = timeframe_filter or "All Time"
        self.chart_mode = chart_mode or "Hourly Distribution"
        self.hover_idx = -1
        self.update()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, 'globalPosition') else event.pos()
        w = self.width()
        m_left = 50
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

    def _filter_records(self):
        from datetime import datetime, timedelta
        filtered = []
        now = datetime.now()
        tf = self.timeframe_filter
        tf_cutoff = None
        if tf == "Today":
            tf_cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif tf == "Last 7 Days":
            tf_cutoff = now - timedelta(days=7)
        elif tf == "Last 30 Days":
            tf_cutoff = now - timedelta(days=30)

        t_filter = (self.target_filter or "All PK Targets").strip().lower()

        for rec in self.history_records:
            if not isinstance(rec, dict):
                continue
            victim = rec.get("victim", "").strip()
            if t_filter not in ["all pk targets", "all targets", "all", ""] and victim.lower() != t_filter:
                continue

            ts_str = rec.get("timestamp", "")
            if tf_cutoff and ts_str:
                try:
                    d_part = ts_str.split(" ")[0]
                    rec_dt = datetime.strptime(d_part, "%Y-%m-%d")
                    if rec_dt < tf_cutoff:
                        continue
                except Exception:
                    pass
            filtered.append(rec)
        return filtered

    def _get_data_series(self):
        records = self._filter_records()
        series = []

        if self.chart_mode == "Hourly Distribution":
            counts = [0] * 24
            for r in records:
                try:
                    h = int(r.get("hour", 0))
                    if 0 <= h < 24:
                        counts[h] += 1
                except Exception:
                    pass
            for h in range(24):
                lbl = f"{h:02d}" if h % 2 == 0 else ""
                series.append((lbl, counts[h], f"{h:02d}:00 - {h:02d}:59 ({counts[h]} PK Kills)"))

        elif self.chart_mode == "Day of Week":
            days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            short_days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            counts = {d: 0 for d in days}
            for r in records:
                dow = r.get("day_of_week", "")
                if dow in counts:
                    counts[dow] += 1
                else:
                    ts_str = r.get("timestamp", "")
                    if ts_str:
                        try:
                            from datetime import datetime
                            d_obj = datetime.strptime(ts_str.split(" ")[0], "%Y-%m-%d")
                            day_name = d_obj.strftime("%A")
                            if day_name in counts:
                                counts[day_name] += 1
                        except Exception:
                            pass
            for idx, d_full in enumerate(days):
                series.append((short_days[idx], counts[d_full], f"{d_full}: {counts[d_full]} PKs"))

        elif self.chart_mode == "Top PK Targets":
            t_map = {}
            for r in records:
                vic = r.get("victim", "Unknown").title()
                t_map[vic] = t_map.get(vic, 0) + 1
            sorted_t = sorted(t_map.items(), key=lambda x: x[1], reverse=True)[:10]
            if not sorted_t:
                sorted_t = [("No Targets", 0)]
            for vic, cnt in sorted_t:
                disp = vic if len(vic) <= 10 else vic[:8] + ".."
                series.append((disp, cnt, f"{vic}: {cnt} PK Victories"))

        return series

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        w = self.width()
        h = self.height()

        bg_brush = QBrush(QColor("#030712"))
        border_pen = QPen(QColor("#1e293b"), 1)
        painter.setBrush(bg_brush)
        painter.setPen(border_pen)
        painter.drawRoundedRect(0, 0, w, h, 6, 6)

        m_left = 50
        m_right = 20
        m_top = 28
        m_bottom = 32

        plot_w = w - m_left - m_right
        plot_h = h - m_top - m_bottom

        if plot_w <= 20 or plot_h <= 20:
            return

        series = self._get_data_series()
        total_kills = sum(s[1] for s in series)
        if not series or total_kills == 0:
            painter.setPen(QPen(QColor("#64748b")))
            painter.setFont(QFont("Segoe UI", 10, QFont.Bold))
            painter.drawText(0, 0, w, h, Qt.AlignCenter, "No Player Kills Recorded in this Timeframe")
            return

        max_v = max([s[1] for s in series] + [5])

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

        col_top = QColor("#c084fc")
        col_bot = QColor("#581c87")

        num_bars = len(series)
        group_w = plot_w / num_bars
        bar_w = max(3, int(group_w * 0.65))

        val_font = QFont("Segoe UI", 8, QFont.Bold)
        label_font = QFont("Segoe UI", 8, QFont.Bold)

        peak_v = max([s[1] for s in series])

        for i, (lbl, val, detail) in enumerate(series):
            cx = int(m_left + i * group_w + group_w / 2)
            bx = cx - bar_w // 2

            bar_h = int((val / max_v) * plot_h) if max_v > 0 else 0
            by = int(m_top + plot_h - bar_h)

            is_peak = (val == peak_v and peak_v > 0)

            if bar_h > 0:
                grad = QLinearGradient(bx, by, bx, by + bar_h)
                if i == self.hover_idx:
                    grad.setColorAt(0, QColor("#e879f9"))
                    grad.setColorAt(1, QColor("#a855f7"))
                elif is_peak:
                    grad.setColorAt(0, QColor("#fbbf24"))
                    grad.setColorAt(1, QColor("#b45309"))
                else:
                    grad.setColorAt(0, col_top)
                    grad.setColorAt(1, col_bot)

                painter.setBrush(QBrush(grad))
                painter.setPen(QPen(QColor("#38bdf8") if i == self.hover_idx else (QColor("#fbbf24") if is_peak else col_top), 1))
                painter.drawRoundedRect(bx, by, bar_w, bar_h, 2, 2)

                if bar_h > 12 or i == self.hover_idx or is_peak:
                    painter.setPen(QPen(QColor("#ffffff") if is_peak else QColor("#e2e8f0")))
                    painter.setFont(val_font)
                    painter.drawText(cx - 20, max(m_top - 18, by - 16), 40, 14, Qt.AlignCenter, f"{val}")
            else:
                painter.setBrush(QBrush(QColor("#1e293b")))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(cx - 1, int(m_top + plot_h - 2), 3, 3)

            if lbl:
                painter.setPen(QPen(QColor("#38bdf8") if i == self.hover_idx else QColor("#94a3b8")))
                painter.setFont(label_font)
                painter.drawText(cx - 20, int(h - m_bottom + 6), 40, 18, Qt.AlignCenter, lbl)

            if i == self.hover_idx:
                painter.setPen(QPen(QColor("#38bdf8"), 1, Qt.DotLine))
                painter.drawLine(cx, m_top, cx, h - m_bottom)

                painter.setBrush(QBrush(QColor("#0f172a")))
                painter.setPen(QPen(QColor("#38bdf8"), 1))
                painter.drawRoundedRect(w - 220, 4, 210, 20, 4, 4)
                painter.setPen(QPen(QColor("#38bdf8")))
                painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
                painter.drawText(w - 220, 4, 210, 20, Qt.AlignCenter, detail)


