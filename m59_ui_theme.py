# -*- coding: utf-8 -*-
"""
M59 UI Theme & Styling Module
Contains the modern dark fluid stylesheet (FLUID_WEB_QSS) and UI color tokens.
"""

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

/* Tooltips - Universal Styled Tooltips for Mouse Over */
QToolTip {
    background-color: #020617;
    color: #38bdf8;
    border: 1px solid #0284c7;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 11px;
    font-weight: 700;
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

QListWidget, QListView {
    background-color: #030712;
    color: #f8fafc;
    border: 1px solid #1e293b;
    border-radius: 6px;
}

QListWidget#NavList {
    background-color: transparent;
    border: none;
    font-size: 13px;
    font-weight: 600;
}

QListWidget::item, QListView::item {
    padding: 0px;
    margin: 0px;
    color: #cbd5e1;
    border-radius: 4px;
    border: 1px solid transparent;
}

QListWidget#NavList::item {
    padding: 6px 10px;
    margin-bottom: 2px;
}

QListWidget::item:hover, QListView::item:hover {
    background-color: #1e293b;
    color: #38bdf8;
}

QListWidget::item:selected, QListView::item:selected {
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
QPushButton {
    background-color: #1e293b;
    color: #f8fafc;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 600;
}

QPushButton:hover {
    background-color: #334155;
    color: #38bdf8;
    border-color: #38bdf8;
}

QPushButton:pressed {
    background-color: #0f172a;
    color: #38bdf8;
}

QPushButton:disabled {
    background-color: #0f172a;
    color: #475569;
    border-color: #1e293b;
}

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
    color: #ffffff;
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
    color: #f8fafc;
}

QToolButton {
    background-color: transparent;
    color: #f8fafc;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 4px;
}

QToolButton:hover {
    background-color: #1e293b;
    color: #38bdf8;
    border-color: #334155;
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

QLineEdit:hover {
    border: 1px solid #475569;
}

QLineEdit:focus {
    border: 1px solid #38bdf8;
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

QComboBox:hover {
    border: 1px solid #38bdf8;
    color: #f8fafc;
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

/* Tables & Data Grids */
QTableWidget, QTableView {
    background-color: #030712;
    color: #f8fafc;
    gridline-color: #1e293b;
    border: 1px solid #1e293b;
    border-radius: 6px;
}

QTableWidget::item, QTableView::item {
    padding: 4px 8px;
    color: #f8fafc;
    background-color: transparent;
}

QTableWidget::item:hover, QTableView::item:hover {
    background-color: #1e293b;
    color: #38bdf8;
}

QTableWidget::item:selected, QTableView::item:selected {
    background-color: #334155;
    color: #ffffff;
}

QHeaderView::section {
    background-color: #0f172a;
    color: #94a3b8;
    font-weight: 700;
    font-size: 11px;
    padding: 6px 8px;
    border: 1px solid #1e293b;
}

QHeaderView::section:hover {
    background-color: #1e293b;
    color: #38bdf8;
}

/* Tree Views */
QTreeWidget, QTreeView {
    background-color: #030712;
    color: #f8fafc;
    border: 1px solid #1e293b;
    border-radius: 6px;
}

QTreeWidget::item, QTreeView::item {
    padding: 4px 8px;
    color: #f8fafc;
}

QTreeWidget::item:hover, QTreeView::item:hover {
    background-color: #1e293b;
    color: #38bdf8;
}

QTreeWidget::item:selected, QTreeView::item:selected {
    background-color: #2563eb;
    color: #ffffff;
}

/* Menus */
QMenu {
    background-color: #0f172a;
    color: #f8fafc;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 4px;
}

QMenu::item {
    padding: 6px 20px 6px 12px;
    border-radius: 4px;
    color: #f8fafc;
}

QMenu::item:selected, QMenu::item:hover {
    background-color: #2563eb;
    color: #ffffff;
}

/* Tab Bar */
QTabBar::tab {
    background-color: #0f172a;
    color: #94a3b8;
    border: 1px solid #1e293b;
    padding: 6px 14px;
    font-weight: 600;
    font-size: 12px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}

QTabBar::tab:hover {
    background-color: #1e293b;
    color: #38bdf8;
}

QTabBar::tab:selected {
    background-color: #1e293b;
    color: #f8fafc;
    border-bottom: 2px solid #38bdf8;
}

/* Checkboxes & Radio Buttons */
QCheckBox:hover, QRadioButton:hover {
    color: #38bdf8;
}

QCheckBox::indicator:hover, QRadioButton::indicator:hover {
    border-color: #38bdf8;
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

