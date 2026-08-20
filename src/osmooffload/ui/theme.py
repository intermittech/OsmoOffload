"""Dark theme (QSS) — clean, flat, accent-driven."""

ACCENT = "#4da3ff"
ACCENT_DIM = "#2d6cb3"
BG = "#16181d"
BG_PANEL = "#1e2128"
BG_CARD = "#252932"
FG = "#e8eaed"
FG_DIM = "#9aa0a6"
OK = "#34c759"
WARN = "#ffb340"
ERR = "#ff5f57"

QSS = f"""
* {{ font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif; font-size: 10.5pt; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#leftRail {{ background: {BG_PANEL}; border-right: 1px solid #0d0e11; }}
QWidget#card {{ background: {BG_CARD}; border-radius: 10px; }}
QLabel {{ color: {FG}; background: transparent; }}
QLabel#dim {{ color: {FG_DIM}; }}
QLabel#title {{ font-size: 14pt; font-weight: 600; }}
QLabel#big {{ font-size: 12pt; font-weight: 600; }}
QPushButton {{
    background: {BG_CARD}; color: {FG}; border: 1px solid #333a46;
    border-radius: 8px; padding: 7px 16px;
}}
QPushButton:hover {{ border-color: {ACCENT_DIM}; }}
QPushButton:disabled {{ color: {FG_DIM}; }}
QPushButton#primary {{
    background: {ACCENT_DIM}; border: 1px solid {ACCENT}; font-weight: 600;
    padding: 10px 22px; font-size: 11.5pt;
}}
QPushButton#primary:hover {{ background: {ACCENT}; color: #10151c; }}
QProgressBar {{
    background: #14161a; border: none; border-radius: 5px; height: 10px; text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QProgressBar#storage::chunk {{ background: {OK}; }}
QProgressBar#storageWarn::chunk {{ background: {WARN}; }}
QProgressBar#storageFull::chunk {{ background: {ERR}; }}
QTabWidget::pane {{ border: none; background: {BG}; }}
QTabBar::tab {{
    background: transparent; color: {FG_DIM}; padding: 8px 18px; border: none;
    border-bottom: 2px solid transparent; font-weight: 600;
}}
QTabBar::tab:selected {{ color: {FG}; border-bottom: 2px solid {ACCENT}; }}
QListWidget {{
    background: transparent; border: none; color: {FG}; outline: none;
}}
QListWidget::item {{ padding: 10px 12px; border-radius: 8px; margin: 2px 6px; }}
QListWidget::item:selected {{ background: {BG_CARD}; }}
QTableWidget {{
    background: {BG_PANEL}; border: none; color: {FG}; gridline-color: #2a2e37;
    selection-background-color: {BG_CARD};
}}
QHeaderView::section {{
    background: {BG_PANEL}; color: {FG_DIM}; border: none; padding: 6px; font-weight: 600;
}}
QLineEdit, QComboBox {{
    background: {BG_PANEL}; color: {FG}; border: 1px solid #333a46; border-radius: 6px;
    padding: 6px 8px;
}}
QCheckBox {{ color: {FG}; spacing: 8px; }}
QToolTip {{ background: {BG_CARD}; color: {FG}; border: 1px solid #333a46; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: #333a46; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""
