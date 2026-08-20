"""Dark (OLED-leaning) theme — semantic tokens + QSS.

Accent discipline: blue = interactive elements only; green/amber/red are
semantic state colors (ok/warn/error); everything else stays neutral.
"""

ACCENT = "#4da3ff"
ACCENT_DIM = "#2f6db8"
BG = "#0f1115"
BG_PANEL = "#15181e"
BG_CARD = "#1b1f27"
BG_INSET = "#11141a"
BORDER = "#2b313d"
FG = "#e8eaed"
FG_DIM = "#9aa0a6"
OK = "#35c26a"
WARN = "#f5a623"
ERR = "#f0554d"

QSS = f"""
* {{ font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Inter', sans-serif; font-size: 10.5pt; }}
QMainWindow, QDialog, QWidget#root {{ background: {BG}; }}
QWidget#leftRail {{ background: {BG_PANEL}; border-right: 1px solid #090a0d; }}
QWidget#card {{ background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 12px; }}
QLabel {{ color: {FG}; background: transparent; }}
QLabel#dim {{ color: {FG_DIM}; }}
QLabel#title {{ font-size: 15pt; font-weight: 650; }}
QLabel#big {{ font-size: 11.5pt; font-weight: 600; }}
QLabel#empty {{ color: {FG_DIM}; font-size: 11pt; }}

QLabel#pill {{
    color: {FG_DIM}; background: {BG_INSET}; border: 1px solid {BORDER};
    border-radius: 11px; padding: 3px 12px; font-weight: 600; font-size: 9.5pt;
}}
QLabel#pill[state="connected"] {{ color: {OK}; border-color: {OK}; }}
QLabel#pill[state="connecting"] {{ color: {WARN}; border-color: {WARN}; }}
QLabel#pill[state="transferring"] {{ color: {ACCENT}; border-color: {ACCENT}; }}
QLabel#pill[state="error"] {{ color: {ERR}; border-color: {ERR}; }}

QPushButton {{
    background: {BG_CARD}; color: {FG}; border: 1px solid {BORDER};
    border-radius: 8px; padding: 8px 16px;
}}
QPushButton:hover {{ border-color: {ACCENT_DIM}; background: #202531; }}
QPushButton:pressed {{ background: {BG_INSET}; }}
QPushButton:focus {{ border: 1px solid {ACCENT}; outline: none; }}
QPushButton:disabled {{ color: #565c66; border-color: #20242c; background: {BG_PANEL}; }}
QPushButton#primary {{
    background: {ACCENT_DIM}; border: 1px solid {ACCENT}; font-weight: 650;
    padding: 11px 22px; font-size: 11.5pt;
}}
QPushButton#primary:hover {{ background: {ACCENT}; color: #0c1118; }}
QPushButton#primary:disabled {{ background: #1c2733; border-color: #27384a; color: #5c6a7a; }}
QPushButton#danger {{ border-color: {ERR}; color: {ERR}; }}
QPushButton#danger:hover {{ background: #2a1715; }}

QProgressBar {{
    background: {BG_INSET}; border: none; border-radius: 5px; text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QProgressBar#storage::chunk {{ background: {OK}; }}
QProgressBar#storageWarn::chunk {{ background: {WARN}; }}
QProgressBar#storageFull::chunk {{ background: {ERR}; }}

QTabWidget::pane {{ border: none; background: {BG}; }}
QTabBar::tab {{
    background: transparent; color: {FG_DIM}; padding: 9px 18px; border: none;
    border-bottom: 2px solid transparent; font-weight: 600;
}}
QTabBar::tab:selected {{ color: {FG}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover:!selected {{ color: {FG}; }}

QListWidget {{ background: transparent; border: none; color: {FG}; outline: none; }}
QListWidget::item {{ padding: 10px 12px; border-radius: 8px; margin: 2px 6px; }}
QListWidget::item:selected {{ background: {BG_CARD}; }}
QListWidget::item:hover:!selected {{ background: #191d25; }}

QTableWidget {{
    background: {BG_PANEL}; border: none; color: {FG}; gridline-color: #232833;
    selection-background-color: {BG_CARD}; selection-color: {FG};
}}
QHeaderView::section {{
    background: {BG_PANEL}; color: {FG_DIM}; border: none; padding: 7px 6px; font-weight: 600;
}}
QTableWidget QProgressBar {{ margin: 6px 4px; }}

QLineEdit, QComboBox {{
    background: {BG_PANEL}; color: {FG}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 7px 9px;
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QCheckBox {{ color: {FG}; spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {BORDER};
    border-radius: 4px; background: {BG_INSET}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QToolTip {{ background: {BG_CARD}; color: {FG}; border: 1px solid {BORDER}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""
