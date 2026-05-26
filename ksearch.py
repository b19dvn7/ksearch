#!/usr/bin/env python3
"""ksearch — fast keyword search GUI.

Backends : ripgrep (content) + fdfind (filenames).
Features : dark theme, tree-grouped results, batched UI inserts, search history,
           settings dialog, fast/deep/ultra scope, partial-match scoring,
           click-to-open file / folder, low-priority subprocesses.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

from PyQt5.QtCore import QProcess, Qt, QTimer, QVariant
from PyQt5.QtGui import QColor, QFont, QPalette, QIcon, QKeySequence
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QStyle,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

# ---------------------------------------------------------------------------
# Constants & paths
# ---------------------------------------------------------------------------

HOME = str(Path.home())
CONFIG_DIR = Path(HOME) / ".config" / "ksearch"
CONFIG_FILE = CONFIG_DIR / "config.json"
HISTORY_FILE = CONFIG_DIR / "history.json"
LEARNING_FILE = CONFIG_DIR / "learning.json"
MAX_HISTORY = 50

PRIORITY_DIRS = [
    os.getcwd(),
    HOME,
    f"{HOME}/Projects",
    f"{HOME}/Documents",
    f"{HOME}/Desktop",
    f"{HOME}/Downloads",
    f"{HOME}/.config",
]

DEFAULT_EXCLUDES = [
    "/proc/**", "/sys/**", "/dev/**", "/run/**", "/snap/**",
    "/var/cache/**", "/var/lib/flatpak/**", "/var/lib/docker/**",
    "**/.cache/**", "**/.local/share/Trash/**", "**/.npm/**",
    "**/node_modules/**", "**/.git/**", "**/.svn/**", "**/.hg/**",
    "**/.venv/**", "**/venv/**", "**/__pycache__/**", "**/.tox/**",
    "**/build/**", "**/dist/**", "**/.next/**", "**/target/**",
    "**/.mypy_cache/**", "**/.pytest_cache/**", "**/.ruff_cache/**",
]

DEFAULT_FILE_TYPES = ""  # empty = no whitelist, search all

STOPWORDS = {"a", "an", "the", "of", "to", "in", "on", "at", "is", "it", "and", "or"}
MIN_QUERY_LEN = 2
MIN_WORD_LEN = 2

# Smart-filter tuning
PLACEHOLDER_TOKENS = (
    "your_", "your-", "yourkey", "xxxxxxxx", "<example", "placeholder",
    "changeme", "change-me", "change_me", "<insert", "<your", "<api",
    "here>", "tobeset", "to_be_set", "your-api", "your_api",
    "fill_in", "fill-in", "fillme", "fill_me",
)
ENVVAR_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}=?$")  # SOME_KEY or SOME_KEY=
ENV_FILE_PATTERNS = (
    "/.env", "/.envrc", "/.bashrc", "/.zshrc", "/.profile",
    "/.config/", ".conf", ".toml", ".yaml", ".yml", ".ini", ".cfg",
)
NOISE_FILE_PATTERNS = (
    ".md", ".rst", ".txt", "readme", ".example", ".sample", ".template",
    "/docs/", "/doc/",
)

DEFAULT_CONFIG = {
    # search behavior
    "row_cap": 2000,
    "max_filesize_mb": 5,
    "excludes": DEFAULT_EXCLUDES,
    "file_types_whitelist": DEFAULT_FILE_TYPES,
    "use_nice": True,
    "use_ionice": True,
    "editor_cmd": "",          # blank = auto-detect
    "flush_ms": 150,
    "group_by_folder": False,
    "show_preview": True,
    # scoring
    "use_score": True,             # master toggle: show score col + heuristic
    "score_default_sort": "score", # default sort: score | count | file | preview
    "score_real_value": 5,
    "score_placeholder": -1,
    "score_env_file": 3,
    "score_doc_file": -3,
    # adaptive learning (clicks teach the scorer)
    "use_learning": True,
    "learning_weight": 2,          # multiplier for click-based bonus
    # appearance
    "theme": "dark",           # "dark" | "light"
    "font_family": "monospace",
    "font_size": 9,
    "value_color": "#7ed957",
    "tmpl_color": "#f0a050",
    "show_history_panel": True,
    "show_plan_label": True,
    "row_alt_color": True,
    # default toggle state on startup
    "default_mode": "fast",
    "default_match": "exact",
    "default_smart": "tag",
    "default_strict": True,
    "default_content": True,
    "default_names": True,
}


# ---------------------------------------------------------------------------
# Config / history persistence
# ---------------------------------------------------------------------------

def ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_config():
    ensure_config_dir()
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text()))
        except Exception:
            pass
    return cfg


def save_config(cfg):
    ensure_config_dir()
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def load_history():
    ensure_config_dir()
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            return []
    return []


def save_history(history):
    ensure_config_dir()
    HISTORY_FILE.write_text(json.dumps(history[-MAX_HISTORY:], indent=2))


# -- learning store (adaptive scoring) --

def load_learning():
    ensure_config_dir()
    if LEARNING_FILE.exists():
        try:
            data = json.loads(LEARNING_FILE.read_text())
            data.setdefault("paths", {})
            data.setdefault("dirs", {})
            data.setdefault("exts", {})
            data.setdefault("queries", {})
            data.setdefault("total_clicks", 0)
            return data
        except Exception:
            pass
    return {"paths": {}, "dirs": {}, "exts": {}, "queries": {}, "total_clicks": 0}


def save_learning(L):
    ensure_config_dir()
    LEARNING_FILE.write_text(json.dumps(L, indent=2))


def record_click(L, path, query=""):
    """Bump path + parent dir + extension + last-query counters."""
    L["total_clicks"] = L.get("total_clicks", 0) + 1
    L["paths"][path] = L["paths"].get(path, 0) + 3
    parent = os.path.dirname(path)
    if parent:
        L["dirs"][parent] = L["dirs"].get(parent, 0) + 1
    ext = os.path.splitext(path)[1].lower()
    if ext:
        L["exts"][ext] = L["exts"].get(ext, 0) + 1
    if query:
        L["queries"][query] = L["queries"].get(query, 0) + 1


def _harvest_recently_used():
    """XDG recently-used.xbel: every GUI-opened file."""
    f = Path(HOME) / ".local" / "share" / "recently-used.xbel"
    if not f.exists():
        return []
    try:
        import xml.etree.ElementTree as ET
        from urllib.parse import unquote
        out = []
        tree = ET.parse(str(f))
        for elem in tree.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "bookmark":
                href = elem.get("href", "")
                if href.startswith("file://"):
                    p = unquote(href[7:])
                    if os.path.exists(p):
                        out.append(p)
        return out
    except Exception:
        return []


def _harvest_shell_history():
    """Extract absolute paths from bash/zsh history files."""
    paths = set()
    candidates = [
        Path(HOME) / ".bash_history",
        Path(HOME) / ".zsh_history",
    ]
    path_re = re.compile(r"(?:^|\s)(/[A-Za-z0-9._\-/+@]+)")
    for f in candidates:
        if not f.exists():
            continue
        try:
            data = f.read_text(errors="ignore")
            for m in path_re.findall(data):
                if os.path.exists(m):
                    paths.add(m)
        except Exception:
            continue
    return list(paths)


def _harvest_vim_mru():
    """Parse ~/.viminfo for most-recently-used file list."""
    paths = set()
    f = Path(HOME) / ".viminfo"
    if not f.exists():
        return []
    try:
        data = f.read_text(errors="ignore")
        for line in data.splitlines():
            if line.startswith("> "):
                p = line[2:].strip()
                if os.path.isabs(p) and os.path.exists(p):
                    paths.add(p)
    except Exception:
        pass
    return list(paths)


def bootstrap_learning_from_system(L, sources):
    """Populate L from selected system sources. Mutates L in place. Returns summary dict."""
    summary = {}

    def bump(p, weight=1):
        L["paths"][p] = L["paths"].get(p, 0) + weight
        d = os.path.dirname(p)
        if d:
            L["dirs"][d] = L["dirs"].get(d, 0) + weight
        ext = os.path.splitext(p)[1].lower()
        if ext:
            L["exts"][ext] = L["exts"].get(ext, 0) + weight

    if "recent" in sources:
        ps = _harvest_recently_used()
        for p in ps:
            bump(p, weight=2)        # GUI-opened = stronger signal
        summary["recently_used.xbel"] = len(ps)

    if "shell" in sources:
        ps = _harvest_shell_history()
        for p in ps:
            bump(p, weight=1)
        summary["shell_history"] = len(ps)

    if "vim" in sources:
        ps = _harvest_vim_mru()
        for p in ps:
            bump(p, weight=2)        # actively edited
        summary["viminfo"] = len(ps)

    return summary


def learning_bonus(path, L):
    """Return numeric score adjustment based on past clicks.
    Uses log scale so heavily-clicked items don't dominate.
    """
    import math
    if not L:
        return 0
    bonus = 0
    p_hits = L.get("paths", {}).get(path, 0)
    if p_hits:
        bonus += math.log2(1 + p_hits) * 2
    parent = os.path.dirname(path)
    d_hits = L.get("dirs", {}).get(parent, 0)
    if d_hits:
        bonus += math.log2(1 + d_hits)
    ext = os.path.splitext(path)[1].lower()
    e_hits = L.get("exts", {}).get(ext, 0) if ext else 0
    if e_hits:
        bonus += math.log2(1 + e_hits) * 0.5
    return round(bonus, 2)


# ---------------------------------------------------------------------------
# Binary lookup
# ---------------------------------------------------------------------------

def shortpath(p):
    """Compact path: replace $HOME with ~."""
    try:
        if p.startswith(HOME + "/"):
            return "~" + p[len(HOME):]
        if p == HOME:
            return "~"
    except Exception:
        pass
    return p


def find_bin(*names):
    for n in names:
        p = shutil.which(n)
        if p:
            if n == "fd" and p == "/usr/bin/fd":
                continue  # broken 2020 binary on this box
            return p
    return None


RG = find_bin("rg")
FD = find_bin("fdfind", "fd")
NICE = find_bin("nice")
IONICE = find_bin("ionice")


class SortItem(QTreeWidgetItem):
    """QTreeWidgetItem that sorts numerically on score/count columns."""
    def __lt__(self, other):
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        a = self.data(col, Qt.UserRole)
        b = other.data(col, Qt.UserRole)
        if a is not None and b is not None:
            try:
                return float(a) < float(b)
            except (TypeError, ValueError):
                pass
        return self.text(col) < other.text(col)


def detect_editor():
    """Pick a text editor for file open. Prefer GUI editors w/ line-jump."""
    for cmd in ("code", "gedit", "gnome-text-editor", "kate", "subl"):
        p = shutil.which(cmd)
        if p:
            return p
    return shutil.which("xdg-open")


# ---------------------------------------------------------------------------
# Query expansion (partial-match support)
# ---------------------------------------------------------------------------

def expand_query(text, match_mode):
    text = text.strip()
    if match_mode == "exact":
        return text, [text], False
    raw = re.split(r"\s+", text)
    words = [w for w in raw if len(w) >= MIN_WORD_LEN and w.lower() not in STOPWORDS]
    if not words:
        return text, [text], False
    escaped = "|".join(re.escape(w) for w in words)
    return f"(?i)({escaped})", words, True


def is_envvar_query(text):
    """Return True if query looks like an env var name (FOO_BAR or FOO_BAR=)."""
    t = text.strip()
    if not t:
        return False
    # tolerate trailing = sign
    t = t.rstrip("=")
    return bool(ENVVAR_RE.match(t + ("=" if not t.endswith("=") else "")))


def build_smart_regex(text):
    """Regex: NAME\\s*=\\s*<any non-whitespace>. Lets placeholder filter do the rest."""
    name = text.strip().rstrip("=")
    return rf"\b{re.escape(name)}\s*=\s*\S"


def hit_is_placeholder(preview):
    low = preview.lower()
    # explicit token check
    for tok in PLACEHOLDER_TOKENS:
        if tok in low:
            return True
    # NAME=<empty or quoted empty>
    if re.search(r"=\s*(['\"])\s*\1\s*$", preview):
        return True
    # NAME=${...} unresolved reference
    if re.search(r"=\s*\$\{?", preview):
        return True
    # NAME=    (trailing whitespace only)
    if re.search(r"=\s*$", preview):
        return True
    # NAME=<placeholder>   any angle-bracket value
    if re.search(r"=\s*<[^>]*>", preview):
        return True
    return False


def file_relevance_bonus(path):
    """+ for likely real config files, - for docs/templates."""
    low = path.lower()
    score = 0
    for pat in ENV_FILE_PATTERNS:
        if pat in low:
            score += 3
            break
    for pat in NOISE_FILE_PATTERNS:
        if pat in low:
            score -= 3
            break
    return score


# ---------------------------------------------------------------------------
# rg / fd command builders
# ---------------------------------------------------------------------------

def wrap_low_priority(argv, cfg):
    """Prepend nice/ionice to keep the box responsive."""
    out = []
    if cfg.get("use_ionice") and IONICE:
        out += [IONICE, "-c", "3"]
    if cfg.get("use_nice") and NICE:
        out += [NICE, "-n", "10"]
    return out + list(argv)


def build_rg_args(regex, mode, root, cfg):
    args = [
        RG,
        "--line-number",
        "--no-heading",
        "--color", "never",
        "--with-filename",
        "--max-columns", "300",
        "--max-filesize", f"{cfg['max_filesize_mb']}M",
        "--threads", str(max(2, (os.cpu_count() or 4) // 2)),
    ]
    if mode == "fast":
        args += ["--smart-case"]
    else:
        args += ["--no-ignore", "--hidden", "--smart-case"]

    for ex in cfg.get("excludes", []):
        args += ["-g", f"!{ex}"]

    types = [t.strip().lstrip(".") for t in (cfg.get("file_types_whitelist") or "").split(",") if t.strip()]
    for t in types:
        args += ["-g", f"*.{t}"]

    args += ["-e", regex, root]
    return wrap_low_priority(args, cfg)


def build_fd_args(regex, mode, root, cfg):
    args = [FD, "--color", "never"]
    if mode != "fast":
        args += ["--no-ignore", "--hidden"]
    for ex in cfg.get("excludes", []):
        # fd uses --exclude with glob fragments; we strip leading /**
        token = ex.lstrip("/").rstrip("/")
        token = re.sub(r"^\*\*/", "", token).rstrip("/*")
        if token:
            args += ["--exclude", token]
    args += [regex, root]
    return wrap_low_priority(args, cfg)


# ---------------------------------------------------------------------------
# Dark theme
# ---------------------------------------------------------------------------

def apply_theme(app, cfg):
    """Apply dark or light theme + font from cfg."""
    app.setStyle("Fusion")
    pal = QPalette()
    if cfg.get("theme", "dark") == "light":
        bg = QColor(245, 245, 247)
        base = QColor(255, 255, 255)
        alt = QColor(238, 238, 240)
        text = QColor(30, 30, 30)
        sub = QColor(110, 110, 115)
        accent = QColor(48, 110, 200)
        pal.setColor(QPalette.HighlightedText, Qt.white)
        sheet = """
            QToolTip { color: #222; background: #ffffe1; border: 1px solid #b0b0a0; }
            QHeaderView::section { background: #e0e0e2; color: #222; padding: 4px; border: 0; border-right: 1px solid #ccc; }
            QTreeView::item:hover { background: #d0e0ff; }
            QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {
                background: white; color: #222; border: 1px solid #bbb; padding: 4px;
            }
            QPushButton { background: #e8e8ea; color: #222; border: 1px solid #bbb; padding: 5px 12px; }
            QPushButton:hover { background: #d8d8da; }
            QPushButton:disabled { background: #efefef; color: #aaa; border-color: #d0d0d0; }
            QStatusBar { background: #ececef; color: #555; }
            QListWidget { background: white; color: #222; border: 1px solid #bbb; }
            QListWidget::item:selected { background: #306ec8; color: white; }
        """
    else:
        bg = QColor(30, 30, 32)
        base = QColor(22, 22, 24)
        alt = QColor(38, 38, 42)
        text = QColor(220, 220, 220)
        sub = QColor(170, 170, 175)
        accent = QColor(94, 156, 235)
        pal.setColor(QPalette.HighlightedText, Qt.black)
        sheet = """
            QToolTip { color: #ddd; background: #303034; border: 1px solid #555; }
            QHeaderView::section { background: #2a2a2e; color: #ddd; padding: 4px; border: 0; border-right: 1px solid #444; }
            QTreeView::item:hover { background: #2c3a52; }
            QTreeView::branch { background: #16181c; }
            QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {
                background: #1c1c20; color: #ddd; border: 1px solid #444; padding: 4px;
            }
            QPushButton {
                background: #353539; color: #eee; border: 1px solid #555; padding: 5px 12px;
            }
            QPushButton:hover { background: #45454a; }
            QPushButton:disabled { background: #2a2a2c; color: #777; border-color: #3a3a3c; }
            QStatusBar { background: #232327; color: #c0c0c5; }
            QSplitter::handle { background: #2a2a2e; }
            QListWidget { background: #1c1c20; color: #ddd; border: 1px solid #333; }
            QListWidget::item:selected { background: #5e9ceb; color: black; }
        """

    pal.setColor(QPalette.Window, bg)
    pal.setColor(QPalette.WindowText, text)
    pal.setColor(QPalette.Base, base)
    pal.setColor(QPalette.AlternateBase, alt)
    pal.setColor(QPalette.Text, text)
    pal.setColor(QPalette.ToolTipBase, alt)
    pal.setColor(QPalette.ToolTipText, text)
    pal.setColor(QPalette.Button, alt)
    pal.setColor(QPalette.ButtonText, text)
    pal.setColor(QPalette.BrightText, QColor(255, 80, 80))
    pal.setColor(QPalette.Link, accent)
    pal.setColor(QPalette.Highlight, accent)
    pal.setColor(QPalette.PlaceholderText, sub)
    app.setPalette(pal)
    app.setStyleSheet(sheet)

    fam = cfg.get("font_family") or "monospace"
    size = int(cfg.get("font_size") or 9)
    app.setFont(QFont(fam, size))


# legacy alias kept so anything still referring to it works
def apply_dark_theme(app):
    apply_theme(app, {"theme": "dark"})


# ---------------------------------------------------------------------------
# Settings dialog
# ---------------------------------------------------------------------------

class ColorButton(QPushButton):
    """Small colored swatch that opens QColorDialog when clicked."""
    def __init__(self, color_hex, parent=None):
        super().__init__(parent)
        self.setFixedWidth(80)
        self.set_color(color_hex)
        self.clicked.connect(self._pick)

    def set_color(self, color_hex):
        self._hex = color_hex
        self.setText(color_hex)
        self.setStyleSheet(f"background:{color_hex}; color:#000; border:1px solid #555;")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._hex), self, "Pick color")
        if c.isValid():
            self.set_color(c.name())

    def color_hex(self):
        return self._hex


class SettingsDialog(QDialog):
    """Tabbed settings: Search · Behavior · Appearance · Defaults · Scoring."""

    def __init__(self, parent, cfg):
        super().__init__(parent)
        self.setWindowTitle("ksearch — Settings")
        self.resize(720, 580)
        self.cfg = dict(cfg)
        self._build()

    def _build(self):
        tabs = QTabWidget()
        tabs.addTab(self._tab_search(), "Search")
        tabs.addTab(self._tab_behavior(), "Behavior")
        tabs.addTab(self._tab_appearance(), "Appearance")
        tabs.addTab(self._tab_defaults(), "Defaults")
        tabs.addTab(self._tab_scoring(), "Scoring")
        tabs.addTab(self._tab_learning(), "Learning")

        btns = QDialogButtonBox(
            QDialogButtonBox.RestoreDefaults | QDialogButtonBox.Cancel | QDialogButtonBox.Save
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        btns.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self._restore_all)

        v = QVBoxLayout(self)
        v.addWidget(tabs, 1)
        v.addWidget(btns)

    # ---- Search tab ----
    def _tab_search(self):
        w = QWidget()
        form = QFormLayout(w)

        self.row_cap = QSpinBox(); self.row_cap.setRange(100, 100000); self.row_cap.setSingleStep(100)
        self.row_cap.setValue(self.cfg["row_cap"])
        form.addRow("Max results (rows)", self.row_cap)

        self.filesize = QSpinBox(); self.filesize.setRange(1, 500); self.filesize.setSuffix(" MB")
        self.filesize.setValue(self.cfg["max_filesize_mb"])
        form.addRow("Skip files larger than", self.filesize)

        self.file_types = QLineEdit(self.cfg.get("file_types_whitelist", ""))
        self.file_types.setPlaceholderText("blank = all (e.g. py,md,txt,json)")
        form.addRow("File type whitelist", self.file_types)

        form.addRow(QLabel("Excluded paths (rg glob, one per line):"))
        self.excludes = QPlainTextEdit("\n".join(self.cfg.get("excludes", [])))
        self.excludes.setFont(QFont("monospace", 9))
        form.addRow(self.excludes)
        return w

    # ---- Behavior tab ----
    def _tab_behavior(self):
        w = QWidget()
        form = QFormLayout(w)

        self.flush = QSpinBox(); self.flush.setRange(50, 2000); self.flush.setSuffix(" ms")
        self.flush.setValue(self.cfg["flush_ms"])
        form.addRow("UI refresh interval", self.flush)

        self.use_nice = QCheckBox("Low CPU priority (nice -n 10)")
        self.use_nice.setChecked(self.cfg["use_nice"])
        form.addRow(self.use_nice)

        self.use_ionice = QCheckBox("Low IO priority (ionice -c 3)")
        self.use_ionice.setChecked(self.cfg["use_ionice"])
        form.addRow(self.use_ionice)

        self.group_by_folder = QCheckBox("Group results by parent folder (future)")
        self.group_by_folder.setChecked(self.cfg["group_by_folder"])
        form.addRow(self.group_by_folder)

        self.show_preview = QCheckBox("Show line preview children in tree")
        self.show_preview.setChecked(self.cfg["show_preview"])
        form.addRow(self.show_preview)

        self.editor_cmd = QLineEdit(self.cfg.get("editor_cmd", ""))
        self.editor_cmd.setPlaceholderText("blank = auto-detect")
        form.addRow("Editor command", self.editor_cmd)
        return w

    # ---- Appearance tab ----
    def _tab_appearance(self):
        w = QWidget()
        form = QFormLayout(w)

        self.theme = QComboBox()
        self.theme.addItems(["dark", "light"])
        self.theme.setCurrentText(self.cfg.get("theme", "dark"))
        form.addRow("Theme", self.theme)

        self.font_family = QFontComboBox()
        ff = self.cfg.get("font_family", "monospace")
        self.font_family.setCurrentFont(QFont(ff))
        form.addRow("Font", self.font_family)

        self.font_size = QSpinBox(); self.font_size.setRange(7, 24); self.font_size.setSuffix(" pt")
        self.font_size.setValue(int(self.cfg.get("font_size", 9)))
        form.addRow("Font size", self.font_size)

        self.value_color = ColorButton(self.cfg.get("value_color", "#7ed957"))
        form.addRow("Value (real key) color", self.value_color)
        self.tmpl_color = ColorButton(self.cfg.get("tmpl_color", "#f0a050"))
        form.addRow("Template (placeholder) color", self.tmpl_color)

        self.show_history_panel = QCheckBox("Show History panel")
        self.show_history_panel.setChecked(self.cfg.get("show_history_panel", True))
        form.addRow(self.show_history_panel)

        self.show_plan_label = QCheckBox("Show Plan line under controls")
        self.show_plan_label.setChecked(self.cfg.get("show_plan_label", True))
        form.addRow(self.show_plan_label)

        self.row_alt_color = QCheckBox("Alternate row coloring")
        self.row_alt_color.setChecked(self.cfg.get("row_alt_color", True))
        form.addRow(self.row_alt_color)
        return w

    # ---- Defaults tab ----
    def _tab_defaults(self):
        w = QWidget()
        form = QFormLayout(w)

        self.default_mode = QComboBox(); self.default_mode.addItems(["fast", "deep", "ultra"])
        self.default_mode.setCurrentText(self.cfg.get("default_mode", "fast"))
        form.addRow("Default Mode", self.default_mode)

        self.default_match = QComboBox(); self.default_match.addItems(["exact", "any", "all"])
        self.default_match.setCurrentText(self.cfg.get("default_match", "exact"))
        form.addRow("Default Match", self.default_match)

        self.default_smart = QComboBox(); self.default_smart.addItems(["off", "tag", "values", "templates"])
        self.default_smart.setCurrentText(self.cfg.get("default_smart", "tag"))
        form.addRow("Default Smart", self.default_smart)

        self.default_strict = QCheckBox("Strict value ON by default (env-var queries)")
        self.default_strict.setChecked(self.cfg.get("default_strict", True))
        form.addRow(self.default_strict)

        self.default_content = QCheckBox("Search Content (rg) by default")
        self.default_content.setChecked(self.cfg.get("default_content", True))
        form.addRow(self.default_content)

        self.default_names = QCheckBox("Search Filenames (fd) by default")
        self.default_names.setChecked(self.cfg.get("default_names", True))
        form.addRow(self.default_names)
        return w

    # ---- Scoring tab ----
    def _tab_scoring(self):
        w = QWidget()
        form = QFormLayout(w)

        info = QLabel(
            "You are the judge. Score is a hint based on the rules below.\n"
            "Turn it off if you prefer to sort by Count / File yourself."
        )
        info.setStyleSheet("color: #88c0ff;")
        info.setWordWrap(True)
        form.addRow(info)

        self.use_score = QCheckBox("Use score column + heuristic ranking")
        self.use_score.setChecked(self.cfg.get("use_score", True))
        form.addRow(self.use_score)

        self.score_default_sort = QComboBox()
        self.score_default_sort.addItems(["score", "count", "file", "preview"])
        self.score_default_sort.setCurrentText(self.cfg.get("score_default_sort", "score"))
        form.addRow("Default sort column", self.score_default_sort)

        self.score_real = QSpinBox(); self.score_real.setRange(-10, 20)
        self.score_real.setValue(int(self.cfg.get("score_real_value", 5)))
        form.addRow("+ per real-value hit", self.score_real)

        self.score_ph = QSpinBox(); self.score_ph.setRange(-20, 10)
        self.score_ph.setValue(int(self.cfg.get("score_placeholder", -1)))
        form.addRow("+ per placeholder hit", self.score_ph)

        self.score_env = QSpinBox(); self.score_env.setRange(-10, 20)
        self.score_env.setValue(int(self.cfg.get("score_env_file", 3)))
        form.addRow("+ if file looks like env/.toml/.conf", self.score_env)

        self.score_doc = QSpinBox(); self.score_doc.setRange(-20, 10)
        self.score_doc.setValue(int(self.cfg.get("score_doc_file", -3)))
        form.addRow("+ if file is doc (.md/.rst/README)", self.score_doc)
        return w

    # ---- Learning tab ----
    def _tab_learning(self):
        w = QWidget()
        v = QVBoxLayout(w)

        info = QLabel(
            "Adaptive scoring learns from your clicks. Files/folders/extensions\n"
            "you open get a score bonus next time you search.\n"
            "Stored locally in ~/.config/ksearch/learning.json (no network)."
        )
        info.setStyleSheet("color: #88c0ff;")
        info.setWordWrap(True)
        v.addWidget(info)

        form = QFormLayout()
        self.use_learning = QCheckBox("Enable adaptive scoring from clicks")
        self.use_learning.setChecked(self.cfg.get("use_learning", True))
        form.addRow(self.use_learning)

        self.learning_weight = QSpinBox(); self.learning_weight.setRange(0, 10)
        self.learning_weight.setValue(int(self.cfg.get("learning_weight", 2)))
        self.learning_weight.setToolTip("0 = ignored. Higher = clicks matter more.")
        form.addRow("Learning weight", self.learning_weight)
        v.addLayout(form)

        # stats summary
        L = load_learning()
        v.addWidget(QLabel(
            f"Total clicks recorded: {L.get('total_clicks', 0)}\n"
            f"Distinct paths: {len(L.get('paths', {}))}    "
            f"Distinct dirs: {len(L.get('dirs', {}))}    "
            f"Distinct exts: {len(L.get('exts', {}))}"
        ))

        top_paths_box = QPlainTextEdit()
        top_paths_box.setReadOnly(True)
        top_paths_box.setFont(QFont("monospace", 9))
        top = sorted(L.get("paths", {}).items(), key=lambda kv: -kv[1])[:15]
        top_paths_box.setPlainText(
            "Top-clicked paths:\n" + "\n".join(f"  {c:4d}  {p}" for p, c in top)
            if top else "Top-clicked paths:  (none yet — click some results!)"
        )
        v.addWidget(top_paths_box, 1)

        # bootstrap controls
        boot_label = QLabel(
            "Bootstrap from system (one-time import to give scoring a head start):"
        )
        boot_label.setStyleSheet("color: #aaa; margin-top: 6px;")
        v.addWidget(boot_label)

        boot_row = QHBoxLayout()
        self.boot_recent = QCheckBox("GUI files (recently-used.xbel)")
        self.boot_recent.setChecked(True)
        self.boot_recent.setToolTip("XDG file: tracks files opened by Files/editors/viewers.")
        self.boot_shell = QCheckBox("Shell history (.bash_history/.zsh_history)")
        self.boot_shell.setChecked(True)
        self.boot_shell.setToolTip("Extracts absolute paths mentioned in shell commands.")
        self.boot_vim = QCheckBox("Vim MRU (.viminfo)")
        self.boot_vim.setChecked(True)
        self.boot_vim.setToolTip("Files recently edited in vim.")
        boot_row.addWidget(self.boot_recent)
        boot_row.addWidget(self.boot_shell)
        boot_row.addWidget(self.boot_vim)
        v.addLayout(boot_row)

        btns_row = QHBoxLayout()
        boot_btn = QPushButton("Bootstrap now")
        boot_btn.setToolTip("Scan selected sources, import paths into learning store.")
        boot_btn.clicked.connect(self._bootstrap_now)
        btns_row.addWidget(boot_btn)
        clear_btn = QPushButton("Clear all learning data")
        clear_btn.clicked.connect(self._clear_learning)
        btns_row.addWidget(clear_btn)
        btns_row.addStretch(1)
        v.addLayout(btns_row)
        return w

    def _bootstrap_now(self):
        sources = []
        if self.boot_recent.isChecked(): sources.append("recent")
        if self.boot_shell.isChecked():  sources.append("shell")
        if self.boot_vim.isChecked():    sources.append("vim")
        if not sources:
            QMessageBox.information(self, "Bootstrap", "Pick at least one source.")
            return
        L = load_learning()
        before = {"paths": len(L["paths"]), "dirs": len(L["dirs"]), "exts": len(L["exts"])}
        summary = bootstrap_learning_from_system(L, sources)
        save_learning(L)
        after = {"paths": len(L["paths"]), "dirs": len(L["dirs"]), "exts": len(L["exts"])}
        QMessageBox.information(
            self,
            "Bootstrap complete",
            "Imported counts (raw):\n  "
            + "\n  ".join(f"{k}: {v}" for k, v in summary.items())
            + f"\n\nStore: paths {before['paths']}→{after['paths']}, "
              f"dirs {before['dirs']}→{after['dirs']}, "
              f"exts {before['exts']}→{after['exts']}"
        )

    def _clear_learning(self):
        if QMessageBox.question(self, "Clear learning",
                "Erase all click history used for adaptive scoring?") == QMessageBox.Yes:
            save_learning({"paths": {}, "dirs": {}, "exts": {}, "queries": {}, "total_clicks": 0})
            QMessageBox.information(self, "Cleared", "Learning data reset.")

    # ---- restore + result ----
    def _restore_all(self):
        for k, v in DEFAULT_CONFIG.items():
            self.cfg[k] = v
        # rebuild dialog body so widgets reflect defaults
        # cheapest: close + reopen
        QMessageBox.information(self, "Defaults restored",
            "Defaults loaded. Click Save to commit, or Cancel to discard.")
        self.cfg["excludes"] = list(DEFAULT_EXCLUDES)
        # easiest visual refresh: re-create widgets via direct set
        self.row_cap.setValue(DEFAULT_CONFIG["row_cap"])
        self.filesize.setValue(DEFAULT_CONFIG["max_filesize_mb"])
        self.flush.setValue(DEFAULT_CONFIG["flush_ms"])
        self.use_nice.setChecked(DEFAULT_CONFIG["use_nice"])
        self.use_ionice.setChecked(DEFAULT_CONFIG["use_ionice"])
        self.group_by_folder.setChecked(DEFAULT_CONFIG["group_by_folder"])
        self.show_preview.setChecked(DEFAULT_CONFIG["show_preview"])
        self.editor_cmd.setText("")
        self.file_types.setText(DEFAULT_CONFIG["file_types_whitelist"])
        self.excludes.setPlainText("\n".join(DEFAULT_EXCLUDES))
        self.theme.setCurrentText(DEFAULT_CONFIG["theme"])
        self.font_family.setCurrentFont(QFont(DEFAULT_CONFIG["font_family"]))
        self.font_size.setValue(DEFAULT_CONFIG["font_size"])
        self.value_color.set_color(DEFAULT_CONFIG["value_color"])
        self.tmpl_color.set_color(DEFAULT_CONFIG["tmpl_color"])
        self.show_history_panel.setChecked(DEFAULT_CONFIG["show_history_panel"])
        self.show_plan_label.setChecked(DEFAULT_CONFIG["show_plan_label"])
        self.row_alt_color.setChecked(DEFAULT_CONFIG["row_alt_color"])
        self.default_mode.setCurrentText(DEFAULT_CONFIG["default_mode"])
        self.default_match.setCurrentText(DEFAULT_CONFIG["default_match"])
        self.default_smart.setCurrentText(DEFAULT_CONFIG["default_smart"])
        self.default_strict.setChecked(DEFAULT_CONFIG["default_strict"])
        self.default_content.setChecked(DEFAULT_CONFIG["default_content"])
        self.default_names.setChecked(DEFAULT_CONFIG["default_names"])
        self.use_score.setChecked(DEFAULT_CONFIG["use_score"])
        self.score_default_sort.setCurrentText(DEFAULT_CONFIG["score_default_sort"])
        self.score_real.setValue(DEFAULT_CONFIG["score_real_value"])
        self.score_ph.setValue(DEFAULT_CONFIG["score_placeholder"])
        self.score_env.setValue(DEFAULT_CONFIG["score_env_file"])
        self.score_doc.setValue(DEFAULT_CONFIG["score_doc_file"])
        self.use_learning.setChecked(DEFAULT_CONFIG["use_learning"])
        self.learning_weight.setValue(DEFAULT_CONFIG["learning_weight"])

    def result(self):
        ex = [ln.strip() for ln in self.excludes.toPlainText().splitlines() if ln.strip()]
        return {
            "row_cap": self.row_cap.value(),
            "max_filesize_mb": self.filesize.value(),
            "flush_ms": self.flush.value(),
            "use_nice": self.use_nice.isChecked(),
            "use_ionice": self.use_ionice.isChecked(),
            "group_by_folder": self.group_by_folder.isChecked(),
            "show_preview": self.show_preview.isChecked(),
            "editor_cmd": self.editor_cmd.text().strip(),
            "file_types_whitelist": self.file_types.text().strip(),
            "excludes": ex,
            "theme": self.theme.currentText(),
            "font_family": self.font_family.currentFont().family(),
            "font_size": self.font_size.value(),
            "value_color": self.value_color.color_hex(),
            "tmpl_color": self.tmpl_color.color_hex(),
            "show_history_panel": self.show_history_panel.isChecked(),
            "show_plan_label": self.show_plan_label.isChecked(),
            "row_alt_color": self.row_alt_color.isChecked(),
            "default_mode": self.default_mode.currentText(),
            "default_match": self.default_match.currentText(),
            "default_smart": self.default_smart.currentText(),
            "default_strict": self.default_strict.isChecked(),
            "default_content": self.default_content.isChecked(),
            "default_names": self.default_names.isChecked(),
            "use_score": self.use_score.isChecked(),
            "score_default_sort": self.score_default_sort.currentText(),
            "score_real_value": self.score_real.value(),
            "score_placeholder": self.score_ph.value(),
            "score_env_file": self.score_env.value(),
            "score_doc_file": self.score_doc.value(),
            "use_learning": self.use_learning.isChecked(),
            "learning_weight": self.learning_weight.value(),
        }


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class SearchWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ksearch")
        self.resize(1280, 780)

        self.cfg = load_config()
        self.history = load_history()
        self.learning = load_learning()
        self._current_query = ""

        self.procs = []
        self.results = {}            # path -> {hits:[(ln,prev)], kinds:set, words:set, name_hit:bool}
        self.hits_content = 0
        self.hits_names = 0
        self.partial_words = []
        self.match_required_mode = "exact"
        self.row_count_displayed = 0
        self.smart_active = False         # snapshot at search start

        # output buffers + flush timer (batched UI)
        self._content_buffer = []    # list of (path, lineno, preview)
        self._name_buffer = []       # list of path
        self.flush_timer = QTimer(self)
        self.flush_timer.timeout.connect(self._flush_buffers)

        self._build_ui()

    # -- UI build --

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)
        v.setContentsMargins(8, 8, 8, 4)

        # row 1: keyword + mode + scope + match
        top = QHBoxLayout()
        top.addWidget(QLabel("Keyword:"))
        self.kw = QLineEdit()
        self.kw.setPlaceholderText("text, regex, or env-var name (e.g. great gatsby OR OPENCODE_ZEN_API_KEY)")
        self.kw.setToolTip("Text or regex to find.")
        self.kw.returnPressed.connect(self.start_search)
        top.addWidget(self.kw, 4)

        top.addWidget(QLabel("Mode:"))
        self.mode = QComboBox()
        self.mode.addItems(["fast", "deep", "ultra"])
        self.mode.setToolTip("fast=cwd+key dirs · deep=$HOME · ultra=/")
        top.addWidget(self.mode)

        top.addWidget(QLabel("Scope:"))
        self.scope = QComboBox()
        self.scope.addItem("auto (mode default)", None)
        self.scope.addItem(f"cwd ({os.getcwd()})", os.getcwd())
        self.scope.addItem(f"$HOME ({HOME})", HOME)
        self.scope.addItem("/ (whole system)", "/")
        self.scope.addItem(f"Projects ({HOME}/Projects)", f"{HOME}/Projects")
        self.scope.addItem("Browse...", "__browse__")
        self.scope.setToolTip("Override search root. Browse... = pick folder.")
        self.scope.activated.connect(self._scope_changed)
        top.addWidget(self.scope, 2)

        top.addWidget(QLabel("Match:"))
        self.match_mode = QComboBox()
        self.match_mode.addItem("exact", "exact")
        self.match_mode.addItem("any word", "any")
        self.match_mode.addItem("all words", "all")
        self.match_mode.setToolTip("exact=literal · any=any word · all=every word")
        top.addWidget(self.match_mode)

        top.addWidget(QLabel("Smart:"))
        self.smart_mode = QComboBox()
        self.smart_mode.addItem("off — show everything", "off")
        self.smart_mode.addItem("show all, tag each", "tag")
        self.smart_mode.addItem("values only (real keys)", "values")
        self.smart_mode.addItem("templates only (where key goes)", "templates")
        self.smart_mode.setCurrentIndex(1)  # default = tag (safest)
        self.smart_mode.setToolTip("tag=color hits · values=real keys · templates=where to set")
        self.smart_mode.currentIndexChanged.connect(self._update_plan)
        top.addWidget(self.smart_mode)

        self.strict_value_cb = QCheckBox("Strict value")
        self.strict_value_cb.setToolTip("Require NAME=value (env-var queries only).")
        self.strict_value_cb.setChecked(True)  # default ON: less noise on env-var queries
        self.strict_value_cb.toggled.connect(self._update_plan)
        top.addWidget(self.strict_value_cb)

        v.addLayout(top)

        # row 2: filters + buttons
        opt = QHBoxLayout()
        self.content_cb = QCheckBox("Content (rg)")
        self.content_cb.setToolTip("Search file contents.")
        self.content_cb.setChecked(True)
        self.names_cb = QCheckBox("Filenames (fd)")
        self.names_cb.setToolTip("Search file/folder names.")
        self.names_cb.setChecked(True)
        opt.addWidget(self.content_cb)
        opt.addWidget(self.names_cb)
        opt.addStretch(1)

        self.search_btn = QPushButton("Search")
        self.search_btn.setShortcut(QKeySequence("Ctrl+Return"))
        self.search_btn.setToolTip("Run (Ctrl+Enter)")
        self.search_btn.clicked.connect(self.start_search)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setToolTip("Kill running searches")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_search)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setToolTip("Clear results")
        self.clear_btn.clicked.connect(self.clear_results)
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.setToolTip("Excludes, caps, priority...")
        self.settings_btn.clicked.connect(self.open_settings)
        self.expand_btn = QPushButton("Expand all")
        self.expand_btn.setToolTip("Open all file rows")
        self.expand_btn.clicked.connect(self._expand_all)
        self.collapse_btn = QPushButton("Collapse all")
        self.collapse_btn.setToolTip("Collapse all file rows")
        self.collapse_btn.clicked.connect(self._collapse_all)
        opt.addWidget(self.expand_btn)
        opt.addWidget(self.collapse_btn)
        opt.addWidget(self.search_btn)
        opt.addWidget(self.stop_btn)
        opt.addWidget(self.clear_btn)
        opt.addWidget(self.settings_btn)
        v.addLayout(opt)

        # row 3: query plan preview (explains what'll be searched)
        plan_row = QHBoxLayout()
        plan_row.addWidget(QLabel("Plan:"))
        self.plan_label = QLabel("type a keyword to see plan...")
        self.plan_label.setStyleSheet("color: #88c0ff; font-family: monospace; font-size: 10px;")
        self.plan_label.setWordWrap(True)
        plan_row.addWidget(self.plan_label, 1)
        v.addLayout(plan_row)

        # wire plan updates to every relevant control
        self.kw.textChanged.connect(self._update_plan)
        self.mode.currentIndexChanged.connect(self._update_plan)
        self.match_mode.currentIndexChanged.connect(self._update_plan)
        self.content_cb.toggled.connect(self._update_plan)
        self.names_cb.toggled.connect(self._update_plan)
        self.scope.currentIndexChanged.connect(self._update_plan)

        # splitter: history (left) | tree (right)
        splitter = QSplitter(Qt.Horizontal)

        hist_panel = QWidget()
        hp = QVBoxLayout(hist_panel)
        hp.setContentsMargins(0, 0, 0, 0)
        hp.addWidget(QLabel("History  (double-click to re-run)"))
        self.history_list = QListWidget()
        self.history_list.setToolTip("Double-click to re-run. Right-click for menu.")
        self.history_list.itemDoubleClicked.connect(self._reuse_history)
        self.history_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.history_list.customContextMenuRequested.connect(self._history_menu)
        hp.addWidget(self.history_list, 1)
        hb = QHBoxLayout()
        clear_hist = QPushButton("Clear history")
        clear_hist.setToolTip("Erase all saved searches.")
        clear_hist.clicked.connect(self.clear_history)
        hb.addWidget(clear_hist)
        hp.addLayout(hb)
        splitter.addWidget(hist_panel)

        # results tree
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["File / Hit", "Score", "Count", "Preview"])
        self.tree.setToolTip("Double-click=open. Right-click=menu. Header=sort.")
        self.tree.headerItem().setToolTip(0, "File path")
        self.tree.headerItem().setToolTip(1, "Relevance score (smart/partial only)")
        self.tree.headerItem().setToolTip(2, "Hit count")
        self.tree.headerItem().setToolTip(3, "Match preview")
        # Score column hidden by default (only meaningful for multi-word or smart mode)
        self.tree.setColumnHidden(1, True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Interactive)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tree.setColumnWidth(0, 460)
        self.tree.setUniformRowHeights(True)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(1, Qt.DescendingOrder)
        self.tree.setAlternatingRowColors(True)
        self.tree.setFont(QFont("monospace", 9))
        self.tree.itemDoubleClicked.connect(self._on_item_activated)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        splitter.addWidget(self.tree)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 1060])
        v.addWidget(splitter, 1)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage(self._precheck())

        self._refresh_history_panel()
        self._apply_defaults_from_cfg()
        self._apply_visuals_from_cfg()
        self._update_plan()

    def _apply_defaults_from_cfg(self):
        """Set control defaults from saved cfg."""
        c = self.cfg
        idx = self.mode.findText(c.get("default_mode", "fast"))
        if idx >= 0:
            self.mode.setCurrentIndex(idx)
        idx = self.match_mode.findData(c.get("default_match", "exact"))
        if idx >= 0:
            self.match_mode.setCurrentIndex(idx)
        idx = self.smart_mode.findData(c.get("default_smart", "tag"))
        if idx >= 0:
            self.smart_mode.setCurrentIndex(idx)
        self.strict_value_cb.setChecked(c.get("default_strict", True))
        self.content_cb.setChecked(c.get("default_content", True))
        self.names_cb.setChecked(c.get("default_names", True))

    def _apply_visuals_from_cfg(self):
        """Toggle panel visibility, alt-rows, plan label per cfg."""
        c = self.cfg
        # plan label visibility
        self.plan_label.setVisible(c.get("show_plan_label", True))
        # alt rows
        self.tree.setAlternatingRowColors(c.get("row_alt_color", True))
        # history panel: find its parent QWidget and toggle
        # the history panel is the first widget added to the splitter
        try:
            sp = self.centralWidget().findChild(QSplitter)
            if sp:
                sp.widget(0).setVisible(c.get("show_history_panel", True))
        except Exception:
            pass
        # font on tree
        self.tree.setFont(QFont(c.get("font_family", "monospace"), int(c.get("font_size", 9))))

    # -- query plan preview --

    def _update_plan(self, *_):
        raw = self.kw.text().strip()
        if len(raw) < MIN_QUERY_LEN:
            self.plan_label.setText("(query too short — min 2 chars)")
            return

        bits = []
        match_mode = self.match_mode.currentData()
        strict = self.strict_value_cb.isChecked() and is_envvar_query(raw)
        smart_val = self.smart_mode.currentData()
        mode = self.mode.currentText()

        # what regex/strategy
        if strict:
            bits.append(f"regex='{build_smart_regex(raw)}'  (Strict value: requires NAME=value)")
        else:
            regex, words, partial = expand_query(raw, match_mode)
            if partial:
                bits.append(f"regex='{regex}'  (split={words}, mode={match_mode})")
            else:
                bits.append(f"literal/regex='{raw}'  (match={match_mode})")

        # scope/backend
        which = []
        if self.content_cb.isChecked():
            which.append("content")
        if self.names_cb.isChecked():
            which.append("filenames")
        bits.append(f"searching: {'+'.join(which) if which else 'NOTHING (enable a backend)'}")
        bits.append(f"depth={mode}")

        # smart-filter overlay
        if smart_val == "tag":
            bits.append("smart: TAG each hit (green=value / orange=template), no drop")
        elif smart_val == "values":
            bits.append("smart: VALUES ONLY — drop placeholder/empty lines")
        elif smart_val == "templates":
            bits.append("smart: TEMPLATES ONLY — only placeholders/empty (find where to set)")

        # cap reminder
        bits.append(f"cap={self.cfg['row_cap']} rows")

        self.plan_label.setText("  |  ".join(bits))

    # -- helpers --

    def _precheck(self):
        parts = []
        parts.append("rg ok" if RG else "rg MISSING")
        parts.append("fd ok" if FD else "fd MISSING")
        parts.append("nice ok" if NICE else "nice n/a")
        parts.append("ionice ok" if IONICE else "ionice n/a")
        return " | ".join(parts) + " | ready"

    def _scope_changed(self, _idx):
        if self.scope.currentData() == "__browse__":
            d = QFileDialog.getExistingDirectory(self, "Pick search root", HOME)
            if d:
                self.scope.insertItem(self.scope.count() - 1, d, d)
                self.scope.setCurrentIndex(self.scope.count() - 2)
            else:
                self.scope.setCurrentIndex(0)

    # -- history --

    def _refresh_history_panel(self):
        self.history_list.clear()
        for h in reversed(self.history[-MAX_HISTORY:]):
            label = f"[{h.get('match','exact')}/{h.get('mode','fast')}] {h.get('keyword','')}"
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, h)
            self.history_list.addItem(it)

    def _reuse_history(self, item):
        h = item.data(Qt.UserRole)
        if not h:
            return
        self.kw.setText(h.get("keyword", ""))
        idx = self.mode.findText(h.get("mode", "fast"))
        if idx >= 0:
            self.mode.setCurrentIndex(idx)
        idx = self.match_mode.findData(h.get("match", "exact"))
        if idx >= 0:
            self.match_mode.setCurrentIndex(idx)
        self.start_search()

    def _history_menu(self, pos):
        m = QMenu(self)
        rerun = m.addAction("Re-run")
        rm = m.addAction("Remove from history")
        chosen = m.exec_(self.history_list.viewport().mapToGlobal(pos))
        item = self.history_list.itemAt(pos)
        if not item:
            return
        h = item.data(Qt.UserRole)
        if chosen == rerun and h:
            self._reuse_history(item)
        elif chosen == rm and h:
            self.history = [x for x in self.history if x != h]
            save_history(self.history)
            self._refresh_history_panel()

    def clear_history(self):
        if QMessageBox.question(self, "Clear history", "Erase all saved searches?") == QMessageBox.Yes:
            self.history = []
            save_history(self.history)
            self._refresh_history_panel()

    def _push_history(self, keyword, mode, match):
        entry = {"keyword": keyword, "mode": mode, "match": match, "ts": int(time.time())}
        self.history = [x for x in self.history if not (
            x.get("keyword") == keyword and x.get("mode") == mode and x.get("match") == match
        )]
        self.history.append(entry)
        save_history(self.history)
        self._refresh_history_panel()

    # -- search lifecycle --

    def clear_results(self):
        self.stop_search()
        self.tree.clear()
        self.results.clear()
        self._content_buffer.clear()
        self._name_buffer.clear()
        self.hits_content = 0
        self.hits_names = 0
        self.row_count_displayed = 0
        self.status.showMessage("cleared")

    def start_search(self):
        raw = self.kw.text().strip()
        if len(raw) < MIN_QUERY_LEN:
            self.status.showMessage(f"query too short (min {MIN_QUERY_LEN} chars)")
            return
        if not re.search(r"\w", raw):
            self.status.showMessage("query has no word characters — refusing")
            return
        if not (self.content_cb.isChecked() or self.names_cb.isChecked()):
            self.status.showMessage("enable at least one of content / filenames")
            return

        match_mode = self.match_mode.currentData()
        # Smart = post-filter only (placeholder drop + file boost). Combinable.
        self.smart_mode_value = self.smart_mode.currentData()
        self.smart_active = self.smart_mode_value != "off"
        # Strict value = regex rewrite (only meaningful for env-var-looking queries).
        strict_value = self.strict_value_cb.isChecked() and is_envvar_query(raw)

        if strict_value:
            regex = build_smart_regex(raw)
            words = [raw.strip().rstrip("=")]
            partial = False
            match_mode = "exact"
        else:
            regex, words, partial = expand_query(raw, match_mode)
            if match_mode == "all" and len(words) < 2:
                match_mode = "any" if partial else "exact"

        self.stop_search()
        self.clear_results()
        self.partial_words = words if not strict_value else []
        self.match_required_mode = match_mode
        self._current_query = raw

        mode = self.mode.currentText()
        scope = self.scope.currentData()
        if scope == "__browse__":
            scope = None
        roots = self._resolve_roots(mode, scope)

        self._push_history(raw, mode, match_mode)
        self.status.showMessage(
            f"searching mode={mode} match={match_mode} words={words} roots={len(roots)}"
        )

        self.search_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.flush_timer.start(self.cfg["flush_ms"])

        for root in roots:
            if self.content_cb.isChecked() and RG:
                self._spawn_rg(regex, mode, root)
            if self.names_cb.isChecked() and FD:
                self._spawn_fd(regex, mode, root)

        if not self.procs:
            self.flush_timer.stop()
            self.search_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.status.showMessage("no backends available — install ripgrep/fdfind")

    def _resolve_roots(self, mode, scope):
        if scope:
            return [scope]
        if mode == "fast":
            seen, out = set(), []
            for d in PRIORITY_DIRS:
                rp = os.path.realpath(d)
                if rp in seen or not os.path.isdir(rp):
                    continue
                seen.add(rp)
                out.append(rp)
            return out or [HOME]
        if mode == "deep":
            return [HOME]
        if mode == "ultra":
            return ["/"]
        return [os.getcwd()]

    def stop_search(self):
        self.flush_timer.stop()
        for p in self.procs:
            try:
                p.kill()
            except Exception:
                pass
        self.procs.clear()
        self.search_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    # -- subprocess spawn --

    def _spawn_rg(self, regex, mode, root):
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.SeparateChannels)
        proc.readyReadStandardOutput.connect(lambda p=proc: self._read_rg(p))
        proc.finished.connect(lambda *_: self._proc_done(proc))
        argv = build_rg_args(regex, mode, root, self.cfg)
        proc.start(argv[0], argv[1:])
        self.procs.append(proc)

    def _spawn_fd(self, regex, mode, root):
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.SeparateChannels)
        proc.readyReadStandardOutput.connect(lambda p=proc: self._read_fd(p))
        proc.finished.connect(lambda *_: self._proc_done(proc))
        argv = build_fd_args(regex, mode, root, self.cfg)
        proc.start(argv[0], argv[1:])
        self.procs.append(proc)

    def _proc_done(self, proc):
        try:
            self.procs.remove(proc)
        except ValueError:
            pass
        proc.deleteLater()
        if not self.procs:
            self._flush_buffers()         # drain remaining
            self.flush_timer.stop()
            self._finalize_results()
            self.search_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.status.showMessage(
                f"done. files={len(self.results)} "
                f"content_hits={self.hits_content} name_hits={self.hits_names} "
                f"match={self.match_required_mode}"
            )

    # -- output readers (lightweight: buffer only) --

    def _read_rg(self, proc):
        try:
            data = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        except Exception:
            return
        cap = self.cfg["row_cap"]
        for line in data.splitlines():
            if not line:
                continue
            if self.row_count_displayed + len(self._content_buffer) >= cap:
                # buffer overflow guard: drop remaining + kill procs soon
                self._maybe_stop_on_cap()
                return
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            self._content_buffer.append((parts[0], parts[1], parts[2]))

    def _read_fd(self, proc):
        try:
            data = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        except Exception:
            return
        cap = self.cfg["row_cap"]
        for line in data.splitlines():
            if not line:
                continue
            if self.row_count_displayed + len(self._name_buffer) >= cap:
                self._maybe_stop_on_cap()
                return
            self._name_buffer.append(line)

    def _maybe_stop_on_cap(self):
        if self.procs:
            self.status.showMessage("row cap reached — stopping further searches")
            self.stop_search()

    # -- buffer flush into tree (batched UI updates) --

    def _flush_buffers(self):
        if not self._content_buffer and not self._name_buffer:
            self._tick_status()
            return

        # toggle sort off during bulk insert -> O(n) instead of O(n*log n)
        was_sorting = self.tree.isSortingEnabled()
        self.tree.setSortingEnabled(False)
        self.tree.setUpdatesEnabled(False)
        try:
            for path, ln, preview in self._content_buffer:
                self._ingest_content_hit(path, ln, preview)
                self.hits_content += 1
            self._content_buffer.clear()

            for path in self._name_buffer:
                self._ingest_name_hit(path)
                self.hits_names += 1
            self._name_buffer.clear()
        finally:
            self.tree.setUpdatesEnabled(True)
            self.tree.setSortingEnabled(was_sorting)
        self._tick_status()

    def _ingest_content_hit(self, path, lineno, preview):
        rec = self.results.get(path)
        if rec is None:
            rec = self._new_record(path)
            if self.smart_active:
                # file-shape bonus (env vs doc) — uses tunable weights
                low = path.lower()
                bonus = 0
                for pat in ENV_FILE_PATTERNS:
                    if pat in low:
                        bonus += self.cfg.get("score_env_file", 3); break
                for pat in NOISE_FILE_PATTERNS:
                    if pat in low:
                        bonus += self.cfg.get("score_doc_file", -3); break
                rec["smart_score"] += bonus
                # adaptive learning bonus (your past clicks teach the scorer)
                if self.cfg.get("use_learning", True):
                    rec["smart_score"] += int(
                        learning_bonus(path, self.learning)
                        * self.cfg.get("learning_weight", 2)
                    )
        is_ph = hit_is_placeholder(preview) if self.smart_active else False
        rec["hits"].append((lineno, preview[:300], is_ph))
        if self.smart_active:
            if is_ph:
                rec["tmpl_hits"] += 1
                rec["smart_score"] += self.cfg.get("score_placeholder", -1)
            else:
                rec["real_hits"] += 1
                rec["smart_score"] += self.cfg.get("score_real_value", 5)
        self._update_words(rec, preview)
        self._refresh_file_node(rec)

    def _ingest_name_hit(self, path):
        rec = self.results.get(path)
        if rec is None:
            rec = self._new_record(path)
        rec["name_hit"] = True
        self._update_words(rec, os.path.basename(path))
        self._refresh_file_node(rec)

    def _new_record(self, path):
        node = SortItem(self.tree)
        node.setText(0, path)
        node.setData(0, Qt.UserRole + 1, {"kind": "file", "path": path})
        rec = {
            "path": path,
            "hits": [],            # list of (lineno, preview, is_placeholder)
            "name_hit": False,
            "words": set(),
            "node": node,
            "real_hits": 0,
            "tmpl_hits": 0,
            "smart_score": 0,
        }
        self.results[path] = rec
        self.row_count_displayed += 1
        return rec

    def _update_words(self, rec, haystack):
        if not self.partial_words:
            return
        low = haystack.lower()
        for w in self.partial_words:
            if w.lower() in low:
                rec["words"].add(w.lower())

    def _refresh_file_node(self, rec):
        node = rec["node"]
        n_hits = len(rec["hits"])
        total_count = n_hits + (1 if rec["name_hit"] else 0)
        suffix = []
        if rec["name_hit"]:
            suffix.append("name")
        if n_hits:
            suffix.append(f"{n_hits} hit{'s' if n_hits != 1 else ''}")
        label = shortpath(rec["path"])
        if suffix:
            label = f"{label}  ({', '.join(suffix)})"
        node.setText(0, label)
        # count column (text + numeric sort hint)
        node.setText(2, str(total_count))
        node.setData(2, Qt.UserRole, total_count)
        # score column (text + numeric sort hint)
        if self.partial_words:
            node.setText(1, f"{len(rec['words'])}/{len(self.partial_words)}")
            node.setData(1, Qt.UserRole, len(rec["words"]))
        else:
            node.setText(1, "—")
            node.setData(1, Qt.UserRole, 0)
        # file path sort hint (case-insensitive)
        node.setData(0, Qt.UserRole, rec["path"].lower())
        # preview teaser in col 3
        if rec["hits"]:
            node.setText(3, rec["hits"][0][1])
        elif rec["name_hit"]:
            node.setText(3, os.path.basename(rec["path"]))
        node.setData(3, Qt.UserRole, node.text(3).lower())
        # children: append new hits as child nodes (lazy growth)
        if self.cfg.get("show_preview") and n_hits > 0 and node.childCount() < n_hits:
            existing = node.childCount()
            for hit in rec["hits"][existing:]:
                ln, prev, is_ph = hit if len(hit) == 3 else (hit[0], hit[1], False)
                child = SortItem(node)
                tag = ""
                if self.smart_active:
                    tag = " [tmpl]" if is_ph else " [value]"
                child.setText(0, f"L{ln}{tag}")
                child.setText(3, prev)
                try:
                    child.setData(0, Qt.UserRole, int(ln))
                except (TypeError, ValueError):
                    child.setData(0, Qt.UserRole, 0)
                child.setData(3, Qt.UserRole, prev.lower())
                child.setData(0, Qt.UserRole + 1, {"kind": "hit", "path": rec["path"], "line": ln})
                # color by classification (from cfg)
                if self.smart_active:
                    color = QColor(
                        self.cfg.get("tmpl_color", "#f0a050") if is_ph
                        else self.cfg.get("value_color", "#7ed957")
                    )
                    for col in (0, 3):
                        child.setForeground(col, color)

    def _finalize_results(self):
        if self.match_required_mode == "all" and len(self.partial_words) > 1:
            required = {w.lower() for w in self.partial_words}
            for p, rec in list(self.results.items()):
                if not required.issubset(rec["words"]):
                    idx = self.tree.indexOfTopLevelItem(rec["node"])
                    if idx >= 0:
                        self.tree.takeTopLevelItem(idx)
                    self.results.pop(p, None)
            self.row_count_displayed = len(self.results)

        # smart filter: 3 modes
        if self.smart_active:
            mode = self.smart_mode_value
            for p, rec in list(self.results.items()):
                drop = False
                if rec["hits"]:
                    if mode == "values" and rec["real_hits"] == 0 and not rec["name_hit"]:
                        drop = True
                    elif mode == "templates" and rec["tmpl_hits"] == 0 and not rec["name_hit"]:
                        drop = True
                if drop:
                    idx = self.tree.indexOfTopLevelItem(rec["node"])
                    if idx >= 0:
                        self.tree.takeTopLevelItem(idx)
                    self.results.pop(p, None)
            # for "templates only" mode, also strip non-placeholder child hits
            if mode == "templates":
                for p, rec in self.results.items():
                    node = rec["node"]
                    for i in range(node.childCount() - 1, -1, -1):
                        child = node.child(i)
                        if "[value]" in child.text(0):
                            node.removeChild(child)
                    # re-sync hits list w/ template-only
                    rec["hits"] = [h for h in rec["hits"] if (len(h) == 3 and h[2])]
                    self._refresh_file_node(rec)
            elif mode == "values":
                for p, rec in self.results.items():
                    node = rec["node"]
                    for i in range(node.childCount() - 1, -1, -1):
                        child = node.child(i)
                        if "[tmpl]" in child.text(0):
                            node.removeChild(child)
                    rec["hits"] = [h for h in rec["hits"] if not (len(h) == 3 and h[2])]
                    self._refresh_file_node(rec)
            # push smart_score into score column for sorting
            for p, rec in self.results.items():
                rec["node"].setText(1, str(rec["smart_score"]))
                rec["node"].setData(1, Qt.UserRole, rec["smart_score"])
            self.row_count_displayed = len(self.results)

        # show/hide Score column based on usefulness + user pref
        score_useful = (
            self.cfg.get("use_score", True)
            and (bool(self.partial_words and len(self.partial_words) > 1) or self.smart_active)
        )
        self.tree.setColumnHidden(1, not score_useful)

        # re-enable sorting; honor user's default sort preference
        self.tree.setSortingEnabled(True)
        default_col_map = {"score": 1, "count": 2, "file": 0, "preview": 3}
        pref = self.cfg.get("score_default_sort", "score")
        target_col = default_col_map.get(pref, 1)
        if target_col == 1 and not score_useful:
            target_col = 2  # fall back to count if score hidden
        self.tree.sortByColumn(target_col, Qt.DescendingOrder)

    def _tick_status(self):
        running = len(self.procs)
        cap = self.cfg["row_cap"]
        self.status.showMessage(
            f"files={len(self.results)}/{cap}  "
            f"content_hits={self.hits_content + len(self._content_buffer)}  "
            f"name_hits={self.hits_names + len(self._name_buffer)}  "
            f"running={running}"
        )

    # -- tree interactions --

    def _expand_all(self):
        self.tree.expandAll()

    def _collapse_all(self):
        self.tree.collapseAll()

    def _on_item_activated(self, item, _col):
        meta = item.data(0, Qt.UserRole + 1)
        if not meta:
            return
        if meta["kind"] == "file":
            self.open_file(meta["path"])
        elif meta["kind"] == "hit":
            self.open_file(meta["path"], int(meta.get("line", 1) or 1))

    def _tree_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        meta = item.data(0, Qt.UserRole + 1)
        if not meta:
            return
        m = QMenu(self)
        a_file = m.addAction("Open file in editor")
        a_folder = m.addAction("Open containing folder")
        a_copy = m.addAction("Copy path")
        chosen = m.exec_(self.tree.viewport().mapToGlobal(pos))
        if chosen == a_file:
            self.open_file(meta["path"], int(meta.get("line", 1) or 1))
        elif chosen == a_folder:
            self.open_folder(meta["path"])
        elif chosen == a_copy:
            QApplication.clipboard().setText(meta["path"])

    # -- open helpers --

    def open_file(self, path, line=1):
        # adaptive learning: count this click
        if self.cfg.get("use_learning", True):
            record_click(self.learning, path, self._current_query)
            save_learning(self.learning)
        editor = self.cfg.get("editor_cmd") or detect_editor()
        if not editor:
            QMessageBox.warning(self, "No editor", "No editor found. Set one in Settings.")
            return
        base = os.path.basename(editor)
        try:
            if "code" in base:
                subprocess.Popen([editor, "--goto", f"{path}:{line}"], close_fds=True)
            elif "subl" in base:
                subprocess.Popen([editor, f"{path}:{line}"], close_fds=True)
            elif "gnome-text-editor" in base or "gedit" in base or "kate" in base:
                subprocess.Popen([editor, path], close_fds=True)
            else:
                subprocess.Popen([editor, path], close_fds=True)
        except Exception as e:
            QMessageBox.warning(self, "Open failed", f"{e}")

    def open_folder(self, path):
        # also a learning signal (lighter than file-open)
        if self.cfg.get("use_learning", True):
            record_click(self.learning, path, self._current_query)
            save_learning(self.learning)
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        opener = shutil.which("xdg-open") or shutil.which("gio")
        if not opener:
            QMessageBox.warning(self, "No opener", "xdg-open not available.")
            return
        try:
            subprocess.Popen([opener, folder], close_fds=True)
        except Exception as e:
            QMessageBox.warning(self, "Open failed", f"{e}")

    # -- settings --

    def open_settings(self):
        dlg = SettingsDialog(self, self.cfg)
        if dlg.exec_() == QDialog.Accepted:
            self.cfg.update(dlg.result())
            save_config(self.cfg)
            apply_theme(QApplication.instance(), self.cfg)
            self._apply_visuals_from_cfg()
            self.status.showMessage("settings saved — visuals applied")


def main():
    if not RG:
        sys.stderr.write("ERROR: ripgrep (rg) not found in PATH\n")
        sys.exit(1)
    app = QApplication(sys.argv)
    cfg = load_config()
    apply_theme(app, cfg)
    w = SearchWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
