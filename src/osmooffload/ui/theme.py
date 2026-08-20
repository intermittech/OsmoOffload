"""'Slate & Ember' design language — fresh graphite neutrals, warm data colors.

Color roles:
- Chrome is neutral cool graphite; the COLOR lives in the data: battery
  gradient (red->amber->green) and the copper/sand storage shades.
- Amber survives only in small doses: active tab, focus, selection borders,
  progress chunks, checkboxes. Never as large surfaces.
- The primary action is a light neutral button (fresh, quiet, high contrast)
  at natural width — no full-width color slabs.
- Green/red stay reserved for semantic states (connected/error, full storage).

Motion tokens: ease-out, interruptible, ~220 ms, skipped when hidden.
"""

# neutrals (cool graphite)
BG = "#141619"
BG_PANEL = "#1a1d21"
BG_CARD = "#212429"
BG_INSET = "#0f1113"
BORDER = "#343a42"
FG = "#e9ebee"
FG_DIM = "#9ba3ad"

# small-dose accent (amber, ties the chrome to the warm data colors)
ACCENT = "#e0a23e"
ACCENT_HOVER = "#efb658"
ACCENT_DIM = "#7d5c26"
ON_ACCENT = "#16120a"

# primary action (light neutral)
PRIMARY = "#e8ebef"
PRIMARY_HOVER = "#ffffff"
ON_PRIMARY = "#191c20"

# storage family — two shades of the same copper/sand hue
STORE_INTERNAL = "#c07830"
STORE_SD = "#e3ab55"

# semantic
OK = "#3fbd74"
WARN = "#e0a23e"
ERR = "#ef5a4c"

# motion tokens (ms)
DUR_FAST = 140
DUR = 220

QSS = f"""
* {{ font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Inter', sans-serif; font-size: 10.5pt; }}
QMainWindow, QDialog, QWidget#root {{ background: {BG}; }}
QWidget#leftRail {{ background: {BG_PANEL}; border-right: 1px solid #0a0806; }}
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
QLabel#pill[state="transferring"] {{ color: {ACCENT_HOVER}; border-color: {ACCENT}; }}
QLabel#pill[state="error"] {{ color: {ERR}; border-color: {ERR}; }}

QPushButton {{
    background: {BG_CARD}; color: {FG}; border: 1px solid {BORDER};
    border-radius: 8px; padding: 8px 16px;
}}
QPushButton:hover {{ border-color: {ACCENT_DIM}; background: #26211a; }}
QPushButton:pressed {{ background: {BG_INSET}; }}
QPushButton:focus {{ border: 1px solid {ACCENT}; outline: none; }}
QPushButton:disabled {{ color: #6a6152; border-color: #241f17; background: {BG_PANEL}; }}
QPushButton#primary {{
    background: {PRIMARY}; color: {ON_PRIMARY}; border: 1px solid {PRIMARY};
    font-weight: 650; padding: 9px 20px; font-size: 11pt;
}}
QPushButton#primary:hover {{ background: {PRIMARY_HOVER}; border-color: {PRIMARY_HOVER}; }}
QPushButton#primary:pressed {{ background: #c7ccd3; border-color: #c7ccd3; }}
QPushButton#primary:disabled {{ background: #2b2f35; border-color: #343a42; color: #6d747d; }}
QPushButton#danger {{ border-color: {ERR}; color: {ERR}; }}
QPushButton#danger:hover {{ background: #2c1713; }}

QProgressBar {{
    background: {BG_INSET}; border: none; border-radius: 5px; text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}

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
QListWidget::item:hover:!selected {{ background: #1c1813; }}

QListWidget#mediaGrid {{ background: {BG_PANEL}; padding: 8px; }}
QListWidget#mediaGrid::item {{
    padding: 6px; margin: 6px; border-radius: 10px; border: 1px solid transparent;
    color: {FG_DIM};
}}
QListWidget#mediaGrid::item:selected {{ background: {BG_CARD}; border-color: {ACCENT}; color: {FG}; }}
QListWidget#mediaGrid::item:hover:!selected {{ background: {BG_CARD}; }}

QTableWidget {{
    background: {BG_PANEL}; border: none; color: {FG}; gridline-color: #262019;
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
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT_HOVER}; }}
QToolTip {{ background: {BG_CARD}; color: {FG}; border: 1px solid {BORDER}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""
