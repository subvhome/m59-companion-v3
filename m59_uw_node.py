import math
from collections import deque
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, Signal
from PySide6.QtGui import QPainter, QPen, QBrush, QColor, QFont

# ----------------------------------------------------------------------
# BRAZIER DATA
# ----------------------------------------------------------------------
BRAZIERS = [
    {
        "id": 0,
        "num": 1,
        "name": "Tos",
        "pos_name": "North-West",
        "short_pos": "NW",
        "toggles": [0, 2, 3],
        "angle_deg": -126.0,
    },
    {
        "id": 1,
        "num": 2,
        "name": "Cornoth",
        "pos_name": "North-East",
        "short_pos": "NE",
        "toggles": [1, 3, 4],
        "angle_deg": -54.0,
    },
    {
        "id": 2,
        "num": 3,
        "name": "Barloque",
        "pos_name": "South-East",
        "short_pos": "SE",
        "toggles": [2, 4, 0],
        "angle_deg": 18.0,
    },
    {
        "id": 3,
        "num": 4,
        "name": "Marion",
        "pos_name": "South",
        "short_pos": "S",
        "toggles": [3, 0, 1],
        "angle_deg": 90.0,
    },
    {
        "id": 4,
        "num": 5,
        "name": "Jasper",
        "pos_name": "South-West",
        "short_pos": "SW",
        "toggles": [4, 1, 2],
        "angle_deg": 162.0,
    },
]


def solve_uw_node(state):
    """
    Finds the shortest sequence of braziers to activate to turn all braziers ON.
    Rule: In-game, only OFF braziers can be activated. Activating an OFF brazier
    toggles itself and its two non-adjacent star braziers.
    """
    target = (True, True, True, True, True)
    state_tuple = tuple(state)
    if state_tuple == target:
        return []

    queue = deque([(state_tuple, [])])
    visited = {state_tuple}

    while queue:
        curr_state, path = queue.popleft()
        for idx in range(5):
            # Can only activate OFF (False) braziers
            if not curr_state[idx]:
                nxt = list(curr_state)
                for t in BRAZIERS[idx]["toggles"]:
                    nxt[t] = not nxt[t]
                nxt_tuple = tuple(nxt)

                new_path = path + [idx]
                if nxt_tuple == target:
                    return new_path
                if nxt_tuple not in visited:
                    visited.add(nxt_tuple)
                    queue.append((nxt_tuple, new_path))
    return None


# ----------------------------------------------------------------------
# PENTAGRAM STAR CANVAS
# ----------------------------------------------------------------------
class UWPentagramCanvas(QWidget):
    """
    Displays the pentagram star with 5 braziers (ON / OFF).
    Clicking a brazier toggles it ON/OFF to mirror the room.
    When solved, displays the central Node / Portal.
    """
    brazier_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(340, 340)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.states = [False, True, True, True, True]
        self.solution_steps = []
        self.node_active = False

        self.pulse_phase = 0.0
        self.anim_timer = QTimer(self)
        self.anim_timer.setInterval(50)
        self.anim_timer.timeout.connect(self._on_anim_tick)
        self.anim_timer.start()

    def set_state(self, states, solution_steps=None):
        self.states = list(states)
        self.solution_steps = solution_steps or []
        self.node_active = all(self.states)
        self.update()

    def _on_anim_tick(self):
        self.pulse_phase += 0.08
        if self.pulse_phase > 2 * math.pi:
            self.pulse_phase -= 2 * math.pi
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        w = self.width()
        h = self.height()
        center = QPointF(w / 2.0, h / 2.0)
        radius = min(w, h) * 0.38

        # Background
        painter.fillRect(QRectF(0, 0, w, h), QColor("#030712"))

        # Outer Pentagram Ring
        ring_pen = QPen(QColor("#1e293b"), 2, Qt.SolidLine)
        painter.setPen(ring_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(center, radius, radius)
        painter.drawEllipse(center, radius * 0.38, radius * 0.38)

        # Brazier Coordinates
        node_coords = []
        for b in BRAZIERS:
            rad = math.radians(b["angle_deg"])
            x = center.x() + radius * math.cos(rad)
            y = center.y() + radius * math.sin(rad)
            node_coords.append(QPointF(x, y))

        # Star Lines
        star_seq = [0, 2, 4, 1, 3, 0]
        star_pen = QPen(QColor("#334155"), 1.5, Qt.DashLine)
        painter.setPen(star_pen)
        for i in range(len(star_seq) - 1):
            p1 = node_coords[star_seq[i]]
            p2 = node_coords[star_seq[i + 1]]
            painter.drawLine(p1, p2)

        # Center Node / Portal
        if self.node_active:
            portal_rad = 34 + 4 * math.sin(self.pulse_phase * 3)
            grad_color = QColor("#818cf8")
            grad_color.setAlpha(90)
            painter.setBrush(QBrush(grad_color))
            painter.setPen(QPen(QColor("#a855f7"), 2, Qt.SolidLine))
            painter.drawEllipse(center, portal_rad + 6, portal_rad + 6)

            painter.setBrush(QBrush(QColor("#4c1d95")))
            painter.setPen(QPen(QColor("#c084fc"), 2, Qt.SolidLine))
            painter.drawEllipse(center, portal_rad, portal_rad)

            font = QFont("Segoe UI", 9, QFont.Bold)
            painter.setFont(font)
            painter.setPen(QColor("#f472b6"))
            painter.drawText(QRectF(center.x() - 40, center.y() - 14, 80, 16), Qt.AlignCenter, "MANA NODE")
            font_sub = QFont("Segoe UI", 8, QFont.Bold)
            painter.setFont(font_sub)
            painter.setPen(QColor("#e9d5ff"))
            painter.drawText(QRectF(center.x() - 40, center.y() + 2, 80, 14), Qt.AlignCenter, "PORTAL OPEN")
        else:
            painter.setBrush(QBrush(QColor("#0f172a")))
            painter.setPen(QPen(QColor("#334155"), 1.5, Qt.SolidLine))
            painter.drawEllipse(center, 26, 26)

            font = QFont("Segoe UI", 8, QFont.Bold)
            painter.setFont(font)
            painter.setPen(QColor("#64748b"))
            painter.drawText(QRectF(center.x() - 30, center.y() - 8, 60, 16), Qt.AlignCenter, "SKULL")

        # Render 5 Brazier Nodes
        node_radius = 28.0
        for i, b in enumerate(BRAZIERS):
            pt = node_coords[i]
            is_on = self.states[i]

            # Find if this brazier is in solution steps
            step_orders = [idx + 1 for idx, b_idx in enumerate(self.solution_steps) if b_idx == i]

            if is_on:
                bg_color = QColor("#854d0e")
                border_color = QColor("#eab308")
                status_text = "ON"
                status_color = QColor("#fef08a")
            else:
                bg_color = QColor("#0f172a")
                border_color = QColor("#f59e0b") if step_orders else QColor("#334155")
                status_text = "OFF"
                status_color = QColor("#94a3b8")

            # Draw Brazier Circle
            painter.setBrush(QBrush(bg_color))
            painter.setPen(QPen(border_color, 2, Qt.SolidLine))
            painter.drawEllipse(pt, node_radius, node_radius)

            # ON / OFF Label
            font_status = QFont("Segoe UI", 11, QFont.Bold)
            painter.setFont(font_status)
            painter.setPen(status_color)
            painter.drawText(QRectF(pt.x() - 20, pt.y() - 14, 40, 28), Qt.AlignCenter, status_text)

            # Brazier Name Banner
            label_x = pt.x() + (34 if pt.x() >= center.x() else -114)
            label_y = pt.y() - 14
            label_rect = QRectF(label_x, label_y, 80, 28)

            painter.setBrush(QBrush(QColor(15, 23, 42, 220)))
            painter.setPen(QPen(QColor("#334155"), 1, Qt.SolidLine))
            painter.drawRoundedRect(label_rect, 4, 4)

            font_name = QFont("Segoe UI", 8, QFont.Bold)
            painter.setFont(font_name)
            painter.setPen(QColor("#f8fafc"))
            painter.drawText(QRectF(label_x, label_y + 2, 80, 13), Qt.AlignCenter, f"{b['name']}")

            font_pos = QFont("Segoe UI", 7)
            painter.setFont(font_pos)
            painter.setPen(QColor("#94a3b8"))
            painter.drawText(QRectF(label_x, label_y + 14, 80, 11), Qt.AlignCenter, b['short_pos'])

            # Order Badge if part of solution
            if step_orders:
                steps_str = ", ".join(f"#{s}" for s in step_orders)
                badge_rect = QRectF(pt.x() - 26, pt.y() - node_radius - 16, 52, 16)

                painter.setBrush(QBrush(QColor("#b45309")))
                painter.setPen(QPen(QColor("#fde047"), 1, Qt.SolidLine))
                painter.drawRoundedRect(badge_rect, 4, 4)

                font_badge = QFont("Segoe UI", 8, QFont.Bold)
                painter.setFont(font_badge)
                painter.setPen(QColor("#ffffff"))
                painter.drawText(badge_rect, Qt.AlignCenter, f"CLICK {steps_str}")

    def mousePressEvent(self, event):
        w = self.width()
        h = self.height()
        center = QPointF(w / 2.0, h / 2.0)
        radius = min(w, h) * 0.38

        click_pt = event.position()
        for i, b in enumerate(BRAZIERS):
            rad = math.radians(b["angle_deg"])
            x = center.x() + radius * math.cos(rad)
            y = center.y() + radius * math.sin(rad)

            dx = click_pt.x() - x
            dy = click_pt.y() - y
            if (dx * dx + dy * dy) <= (32 * 32):
                self.brazier_clicked.emit(i)
                break


# ----------------------------------------------------------------------
# MAIN UW NODE SOLVER WIDGET
# ----------------------------------------------------------------------
class UWNodeSolverWidget(QWidget):
    """
    Clean Underworld Mana Node Resolver.
    Layout contains only the pentagram star with braziers and the ordered list
    of braziers to change to solve the puzzle and reveal the node.
    """
    def __init__(self, parent=None):
        super().__init__(parent)

        # Initial state: 1 OFF (Tos)
        self.brazier_states = [False, True, True, True, True]
        self.solution_steps = []

        self._init_ui()
        self.recalculate_solution()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # Header Title & Instructions
        hdr_layout = QVBoxLayout()
        hdr_layout.setSpacing(2)

        title = QLabel("UNDERWORLD MANA NODE RESOLVER")
        title.setStyleSheet("font-size: 14px; font-weight: 900; color: #a855f7; letter-spacing: 0.5px;")
        sub = QLabel("Click braziers on the star below to set room state (ON / OFF)")
        sub.setStyleSheet("font-size: 11px; color: #94a3b8;")

        hdr_layout.addWidget(title)
        hdr_layout.addWidget(sub)
        main_layout.addLayout(hdr_layout)

        # Main Content Layout: Star Canvas + Solution List
        content_layout = QHBoxLayout()
        content_layout.setSpacing(16)

        # Pentagram Canvas
        self.canvas = UWPentagramCanvas()
        self.canvas.brazier_clicked.connect(self.toggle_brazier_state)
        content_layout.addWidget(self.canvas, 2)

        # Solution Panel
        sol_panel = QFrame()
        sol_panel.setStyleSheet("background-color: #0f172a; border: 1px solid #1e293b; border-radius: 8px;")
        sol_layout = QVBoxLayout(sol_panel)
        sol_layout.setContentsMargins(16, 16, 16, 16)
        sol_layout.setSpacing(12)

        sol_title = QLabel("SOLUTION (CLICK IN THIS ORDER):")
        sol_title.setStyleSheet("font-size: 11px; font-weight: 900; color: #eab308; letter-spacing: 0.5px;")
        sol_layout.addWidget(sol_title)

        self.sol_list_lbl = QLabel()
        self.sol_list_lbl.setStyleSheet("font-size: 12px; color: #f8fafc; line-height: 1.6;")
        self.sol_list_lbl.setWordWrap(True)
        sol_layout.addWidget(self.sol_list_lbl)

        sol_layout.addStretch()

        content_layout.addWidget(sol_panel, 1)

        main_layout.addLayout(content_layout, 1)

    def toggle_brazier_state(self, idx):
        self.brazier_states[idx] = not self.brazier_states[idx]
        self.recalculate_solution()

    def recalculate_solution(self):
        self.solution_steps = solve_uw_node(self.brazier_states) or []
        self.update_ui_displays()

    def update_ui_displays(self):
        # Update Canvas
        self.canvas.set_state(self.brazier_states, self.solution_steps)

        # Update Solution List
        is_solved = all(self.brazier_states)
        if is_solved:
            self.sol_list_lbl.setText(
                "<span style='color: #4ade80; font-weight: bold; font-size: 13px;'>"
                "✔ All braziers are ON!<br>The Mana Node Portal is active at the central skull.</span>"
            )
        elif self.solution_steps:
            html = ["<ol style='margin-left: -15px; padding-left: 15px;'>"]
            for step_num, b_idx in enumerate(self.solution_steps, 1):
                b = BRAZIERS[b_idx]
                html.append(
                    f"<li style='margin-bottom: 8px;'>"
                    f"<b style='color: #fde047;'>Click {b['name']}</b> "
                    f"<span style='color: #94a3b8;'>({b['pos_name']})</span>"
                    f"</li>"
                )
            html.append("</ol>")
            self.sol_list_lbl.setText("".join(html))
        else:
            self.sol_list_lbl.setText(
                "<span style='color: #f87171;'>No solution found for this configuration.</span>"
            )
