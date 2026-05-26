# ksearch — Session Summary

**Date:** 2026-05-26
**Author:** bigdan7 (Pop!_OS 22.04, Intel CPU, 16GB RAM, no NVIDIA/CUDA)
**Outcome:** A single-file ELF binary (`~/Desktop/ksearch`, ~47 MB) that provides a fast, dark-themed, GUI keyword search across files, with adaptive scoring, history, and a tabbed settings panel.

---

## 1. What the user originally asked for

> "create a gui that will search diff levels in the system and all files for keywords, it should have options fast, deep, and if any other, it needs to be fast, it needs to actually work and find the keywords by checking first in relevant places"

Core requirements derived from that ask:

1. **Graphical user interface** — not a CLI.
2. **Multiple search depths** — at minimum `fast`, `deep`, and one more option.
3. **Genuine speed** — perceived UI responsiveness and backend speed.
4. **Correctness** — actually find keywords, not stub behavior.
5. **Smart prioritization** — search the most relevant places first.

---

## 2. What it became (additions beyond the original ask)

The session iterated through user feedback. The final product extends the original ask considerably:

| # | Capability | Trigger from user |
|---|---|---|
| 1 | Partial multi-word matching (`exact` / `any word` / `all words`) | "if I'm looking for a three word title, find similar" |
| 2 | Single-file double-clickable binary | "package into one file… so I could just double-click and run it like an app" |
| 3 | Background priority + batched UI + dir exclusions | "it froze my box for a few seconds" |
| 4 | Dark theme, tree-grouped results, history, clickable open, settings panel | "make it work fast and find fast, some memory… black theme… bundle them so it doesn't flood the screen… click on file or folder and open" |
| 5 | Smart env-var filtering (placeholder detection, file-type boost/demote) | "searching `OPENCODE_ZEN_API_KEY=`… not just noise that would be clogging the area" |
| 6 | Three-way Smart selector (off / tag / values only / templates only) | "if no key found it will not show? if I need to find where the damn key needs to be" |
| 7 | "Plan" line under controls explaining what regex will run | "make it bit more friendly… easy to choose and understand what it will be looking for" |
| 8 | Trimmed tooltips (1-liners) | "make popup helper more helper… so much text to read" |
| 9 | Auto-hide score column when irrelevant + shorten paths | "Score 1/1 1/1 everywhere"; long absolute paths |
| 10 | Tabbed Settings (6 tabs): Search · Behavior · Appearance · Defaults · Scoring · Learning | "make me a settings menu where I can toggle features and visuals" |
| 11 | Adaptive scoring that learns from user clicks | "if score would learn from my clicking and searching" |
| 12 | System-wide bootstrap (XDG recently-used, bash/zsh history, vim MRU) | "learn from this system's searches… load something into it so it can have better scoring" |

**Nothing from the original ask was dropped.** Every requirement is still present in the final build.

---

## 3. Final feature inventory

### Search backends
- **ripgrep (`rg`)** for file content. Configured with `--smart-case`, `--max-columns 300`, `--max-filesize 5M` (configurable), `--threads cpu/2`, custom `-g '!pattern'` excludes, optional `*.ext` whitelist.
- **fdfind** for filenames/folders. Same exclude logic.
- Both wrapped with `nice -n 10` and `ionice -c 3` so background searches do not freeze the desktop.

### Search depth modes
- **fast** — searches a priority list (cwd, `$HOME`, `~/Projects`, `~/Documents`, `~/Desktop`, `~/Downloads`, `~/.config`) with `.gitignore` rules obeyed. Best for daily work.
- **deep** — searches `$HOME`, no-ignore, hidden files included. Best for full personal data.
- **ultra** — root `/`, no-ignore, hidden files. Whole filesystem.
- A **Scope** dropdown overrides the default root: `cwd`, `$HOME`, `/`, `~/Projects`, or `Browse…` to pick any folder.

### Match modes
- **exact** — query is one literal/regex.
- **any word** — splits on whitespace, builds `(?i)(w1|w2|w3)` regex.
- **all words** — same regex but post-filters: only keeps files that contain every word at least once.

### Smart filter (3-way)
| Mode | Behavior |
|---|---|
| `off` | No classification or boosting. Show raw rg/fd output. |
| `tag` *(default)* | Show all hits but color-code each: **green** for real value, **orange** for placeholder/empty/`${var}`. |
| `values only` | Drop files where every hit is a placeholder. Only "where the key is actually set". |
| `templates only` | Drop files where any hit is a real value. Only "where the key needs to go". |

### Strict value (regex rewrite)
When the query looks like an environment-variable name (`[A-Z][A-Z0-9_]{2,}`), enabling Strict rewrites the regex to `\bNAME\s*=\s*\S` — requires at least one non-whitespace character after the `=`. Combinable with any Smart mode and any Match mode.

### Result tree
- Top-level rows are **files** (path shortened to `~/...` when under `$HOME`).
- Child rows are **individual hits** with line numbers and full preview.
- Header click sorts any column (`File`, `Score`, `Count`, `Preview`). Score column is **hidden** by default and auto-shown only when meaningful (multi-word partial or Smart mode).
- Double-click on a file row opens the file in the default editor (auto-detects VS Code, gedit, gnome-text-editor, kate, subl). Double-click on a hit child opens at that line (`code --goto path:line` when VS Code is present).
- Right-click context menu: Open file in editor · Open containing folder · Copy path.

### History (memory)
- Left-side panel lists recent searches.
- Double-click re-runs that exact query (keyword + mode + match).
- Right-click for: Re-run · Remove.
- "Clear history" button.
- Persisted to `~/.config/ksearch/history.json`, 50-entry rolling buffer.

### Settings dialog (6 tabs)
1. **Search** — row cap, max file size, file-type whitelist, excluded paths (rg glob syntax).
2. **Behavior** — UI refresh interval, low CPU/IO priority toggles, group by folder, show preview children, custom editor command.
3. **Appearance** — Dark/Light theme, font family + size, value/template colors (color pickers), History panel visibility, Plan label visibility, alternate-row coloring.
4. **Defaults** — default state for Mode, Match, Smart, Strict, Content, Filenames toggles on startup.
5. **Scoring** — master toggle (disable score entirely), default sort column, tunable point weights (+real, +placeholder, +env-file, +doc-file).
6. **Learning** — toggle adaptive scoring, weight slider, stats display, top-clicked paths list, "Bootstrap from system" with source checkboxes (GUI files / shell history / vim MRU), "Clear all learning data".

All settings persist to `~/.config/ksearch/config.json`.

### Adaptive scoring
- Every file/folder opened (double-click in tree or right-click menu) increments counters in `~/.config/ksearch/learning.json`: per-path, per-parent-dir, per-extension, per-query.
- On subsequent searches, the smart score gets a bonus equal to `log2(1+clicks) × weight`. Log scale prevents a single hot path from dominating.
- The user can **bootstrap** the learning store from existing system data:
  - `~/.local/share/recently-used.xbel` — XDG standard for "recently used files" updated by GUI apps (file manager, image viewers, editors, etc.). On this machine it imported **146 paths**.
  - `~/.bash_history` / `~/.zsh_history` — regex-extracts absolute paths mentioned in shell commands. Imported **35 paths**.
  - `~/.viminfo` — vim's MRU file list. Not present on this box, returned 0.
- Per-source weights: GUI opens = 2×, vim edits = 2×, shell mentions = 1× (because shell paths are noisier).
- Stored 100% locally, no network, no telemetry. Master toggle off in Settings → Learning if not wanted.

### Performance safeguards
- **Result streaming** — rg/fd output is buffered, flushed to UI every 150 ms (configurable) so the main thread is never blocked by a flood of matches.
- **Row cap** — 2000 by default. Searches auto-stop on overflow.
- **Default exclusions** — `/proc`, `/sys`, `/dev`, `/run`, `/snap`, `/var/cache`, `/var/lib/flatpak`, `/var/lib/docker`, `~/.cache`, `~/.local/share/Trash`, `node_modules`, `.git`, `.venv`, `venv`, `__pycache__`, `build`, `dist`, `.next`, `target`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`. Editable in Settings → Search.
- **File size cap** — skip files larger than 5 MB (configurable up to 500 MB).
- **`nice -n 10` + `ionice -c 3`** — every backend process runs at the lowest scheduling priority.
- **`rg --threads cpu/2`** — leaves at least half the cores free for the UI and other apps.

---

## 4. Architecture

### File layout

```
~/Projects/ksearch/
├── ksearch.py             # Source (single-file Python, ~900 lines)
├── ksearch.spec           # PyInstaller spec (auto-generated)
├── dist/
│   └── ksearch            # Standalone ELF binary, ~47 MB
├── build/                 # PyInstaller scratch (can be ignored)
└── SESSION_SUMMARY.md     # This file

~/Desktop/
└── ksearch                # Deployed copy of the binary

~/.local/share/applications/
└── ksearch.desktop        # App-menu launcher (search "ksearch" in activities)

~/.config/ksearch/
├── config.json            # User settings
├── history.json           # Last 50 searches
└── learning.json          # Adaptive scoring store
```

### High-level flow

```
User types query → SearchWindow.start_search()
        │
        ├─ expand_query()      → builds regex from match mode
        ├─ build_smart_regex() → only if Strict + env-var pattern
        ├─ _resolve_roots()    → list of dirs to search
        │
        ├─ For each root: spawn QProcess running
        │      [ionice -c 3] [nice -n 10] rg <args> root
        │      [ionice -c 3] [nice -n 10] fdfind <args> root
        │
        ├─ QProcess.readyReadStandardOutput → _read_rg / _read_fd
        │      (only buffers lines, no UI work)
        │
        ├─ QTimer (150 ms) → _flush_buffers()
        │      └─ For each buffered hit:
        │            _ingest_content_hit()  or  _ingest_name_hit()
        │              ↓
        │            _new_record()          (if first time for this path)
        │              ↓
        │            file_relevance_bonus()
        │            learning_bonus()       (if adaptive scoring on)
        │            hit_is_placeholder()   (if Smart mode active)
        │              ↓
        │            _refresh_file_node()   (updates QTreeWidget row)
        │
        └─ All QProcesses finished → _proc_done() drains buffer →
                  _finalize_results():
                      • drop placeholder-only files (Smart=values)
                      • drop value-bearing files    (Smart=templates)
                      • populate score column
                      • sort by user preference
                      • show/hide score column
```

### Key Python modules and functions

#### Persistence layer (lines ~75–195 of `ksearch.py`)

| Function | Purpose |
|---|---|
| `load_config()` / `save_config()` | Read/write `config.json` against `DEFAULT_CONFIG` schema |
| `load_history()` / `save_history()` | Read/write `history.json` (capped at `MAX_HISTORY=50`) |
| `load_learning()` / `save_learning()` | Read/write `learning.json` with `paths`, `dirs`, `exts`, `queries`, `total_clicks` |
| `record_click(L, path, query)` | Increment all relevant counters when user opens something |
| `learning_bonus(path, L)` | `log2(1+path_clicks)*2 + log2(1+dir_clicks) + log2(1+ext_clicks)*0.5` |

#### System bootstrap (lines ~200–280)

| Function | Source | Weight |
|---|---|---|
| `_harvest_recently_used()` | Parse `~/.local/share/recently-used.xbel` XML, extract `file://` URLs | 2× |
| `_harvest_shell_history()` | Regex `(?:^|\s)(/[A-Za-z0-9._\-/+@]+)` against `~/.bash_history` and `~/.zsh_history`, keep only paths that currently exist | 1× |
| `_harvest_vim_mru()` | Parse `~/.viminfo` lines starting with `> ` | 2× |
| `bootstrap_learning_from_system(L, sources)` | Calls the harvesters and bumps `L["paths"]`, `L["dirs"]`, `L["exts"]` with weighted counts. Returns a summary dict. |

#### Query semantics (lines ~285–340)

| Function | What it does |
|---|---|
| `expand_query(text, mode)` | If `exact`: return `text` unchanged. Otherwise split on whitespace, drop stopwords (`a`, `the`, `of`…) and tokens <2 chars, build `(?i)(w1|w2|...)` regex. |
| `is_envvar_query(text)` | Returns True if query matches `^[A-Z][A-Z0-9_]{2,}=?$` (Strict value will rewrite). |
| `build_smart_regex(text)` | Returns `\bNAME\s*=\s*\S` — Strict value's rewritten pattern. |
| `hit_is_placeholder(preview)` | True if line contains any of `PLACEHOLDER_TOKENS` (`your_`, `<your`, `xxx`, `changeme`, …) OR matches `=\s*$` (empty) OR `=\s*\$\{?` (unresolved ref) OR `=\s*<…>` (angle-bracket placeholder). |
| `file_relevance_bonus(path)` | `+3` if path matches `ENV_FILE_PATTERNS` (`/.env`, `/.bashrc`, `.toml`, `.yaml`, `.conf`, …). `-3` if path matches `NOISE_FILE_PATTERNS` (`.md`, `.rst`, `README`, `.example`, `.sample`, `/docs/`, …). Weights configurable per Scoring tab. |

#### Subprocess wrapping (lines ~345–410)

```python
def wrap_low_priority(argv, cfg):
    out = []
    if cfg.get("use_ionice") and IONICE: out += [IONICE, "-c", "3"]
    if cfg.get("use_nice")   and NICE:   out += [NICE, "-n", "10"]
    return out + list(argv)
```

`build_rg_args()` and `build_fd_args()` assemble the rg/fd argv arrays from the current Mode + cfg (excludes, file-type whitelist, file-size cap), then pass through `wrap_low_priority()`.

#### Theming (lines ~415–520)

`apply_theme(app, cfg)` uses Qt `QPalette` + a stylesheet. Two palettes (dark and light), font family/size pulled from `cfg`, applied at app startup and re-applied whenever the user saves settings.

#### `SortItem` (sortable tree row)

Subclasses `QTreeWidgetItem` to override `__lt__` so the Score, Count, and line-number columns sort numerically (using `data(col, Qt.UserRole)` as the numeric sort key) instead of lexically (which would order `L10` before `L9`).

#### `SettingsDialog` (~600–900)

A `QTabWidget` with six tab-builder methods (`_tab_search`, `_tab_behavior`, `_tab_appearance`, `_tab_defaults`, `_tab_scoring`, `_tab_learning`). The Learning tab additionally has a `Bootstrap from system` button that calls `bootstrap_learning_from_system()` and shows a result summary in a message box. `_restore_all()` resets every widget to `DEFAULT_CONFIG` (still requires Save). `result()` returns the full settings dict back to `SearchWindow.open_settings()`.

A small custom `ColorButton(QPushButton)` opens `QColorDialog` and stores the resulting hex string for value/template colors.

#### `SearchWindow` (~905–end)

The main window. Holds:

- A vertical layout with: top control row (keyword + mode + scope + match + smart + strict), second row (content/filename toggles + buttons), Plan line, a `QSplitter` with History panel on the left and result tree on the right, and a status bar.
- `procs: list[QProcess]` of running searches.
- `results: dict[path, rec]` where `rec` holds `hits`, `name_hit`, `words`, `node`, `real_hits`, `tmpl_hits`, `smart_score`.
- A `QTimer` (`flush_timer`) that calls `_flush_buffers()` at the configured interval.

Notable methods:

- `start_search()` validates the query (≥2 chars, has word characters), resolves roots, spawns processes, starts the flush timer.
- `stop_search()` kills every running QProcess and stops the timer.
- `_proc_done()` removes the process from the list; when the list becomes empty, calls `_finalize_results()` and re-enables sorting on the tree.
- `_finalize_results()` applies Smart-mode dropping rules, populates the score column, hides it if not meaningful, sets the default sort column.
- `_update_plan()` updates the cyan "Plan:" line under the controls. Wired to every relevant control's signal so it refreshes live.

---

## 5. How the scoring math works (step-by-step)

Take an example query: `OPENCODE_ZEN_API_KEY`, Smart=`tag`, Strict ON, adaptive learning ON with bootstrap done.

For each file the regex hits, the scorer computes:

```
smart_score = 0

# File-shape bonus (one-time per file)
if path matches /.env|.toml|.bashrc|.yaml|.conf/:
    smart_score += cfg.score_env_file        # default +3
elif path matches /.md|.rst|README|.example/:
    smart_score += cfg.score_doc_file        # default -3

# Per-hit bonus
for each line that matched in this file:
    if hit_is_placeholder(line):
        smart_score += cfg.score_placeholder # default -1
        rec.tmpl_hits += 1
    else:
        smart_score += cfg.score_real_value  # default +5
        rec.real_hits += 1

# Adaptive bonus (once per file)
if cfg.use_learning:
    bonus  = log2(1 + clicks_on_this_path) * 2
    bonus += log2(1 + clicks_on_this_dir)
    bonus += log2(1 + clicks_on_this_ext) * 0.5
    smart_score += int(bonus * cfg.learning_weight)   # default weight 2
```

A `.env` file with a real key in a directory the user has opened often will easily score 13+, while a `README.md` with a placeholder mention will land negative.

Sort is descending on this number by default — but the user can click any column header to override, and turn the entire scoring system off in Settings → Scoring.

---

## 6. How to launch and use

### Launch
- **App menu**: search "ksearch" in Pop!_OS activities → click.
- **Desktop**: double-click the `ksearch` icon on the desktop.
- **Terminal**: `~/Desktop/ksearch` or `~/Projects/ksearch/dist/ksearch`.

### First run
1. Open **Settings → Learning** and click **Bootstrap now**. This imports paths from your existing usage so the scorer has a head start.

### Typical workflow
- **Find a config value**: type `OPENCODE_ZEN_API_KEY`, leave Smart=`tag`, Strict=on. Real values appear in green, placeholders in orange.
- **Find where to set a key**: change Smart to `templates only`. Only files with empty/placeholder lines remain.
- **Find a phrase**: type `great gatsby novel`, change Match to `any word`, leave Smart=`tag`. Results sort by how many of your three words each file contains.
- **Look at the Plan line** under the controls to see exactly what regex and post-filter will run before you click Search.

### After several searches
The adaptive scorer notices which files you keep opening. Files in the directories where you actually work float to the top automatically.

---

## 7. Suggested visualizations for Claude design

This document is structured so it can be fed directly into Claude (claude.ai's document/artifact tools or Claude design). Useful one-pager visualizations to ask for:

1. **Architecture diagram** — a flow showing: user input → SearchWindow → QProcess streams of rg/fd → buffer → flush timer → tree updates → finalize. Section 4 above has the textual flow.
2. **Scoring decision tree** — given query type (env-var vs free-text), Smart mode (off/tag/values/templates), Strict toggle, what regex runs and what post-filters apply. Section 5 has the math.
3. **UI mockup** — top controls row, Plan line, History panel, color-coded tree, status bar. Section 3 lists every control.
4. **Data flow for the Learning system** — clicks → record_click → JSON store → learning_bonus → smart_score on next search. Section 4 describes it.
5. **Comparison panel** — "Before ksearch (raw `grep` output)" vs "After ksearch (color-tagged tree)" for the env-var scenario.
6. **Settings tab map** — six tabs and what each contains. Section 3 lists them.

If a JSON spec of the architecture would be more useful for a design tool than this Markdown, the same content can be re-emitted as JSON on request.

---

## 8. Differences from the original ask

| Original ask | Final state | Difference |
|---|---|---|
| GUI search | Yes — PyQt5 single-window | Met |
| `fast` and `deep` modes plus one more | `fast`, `deep`, `ultra` | Met (added a third explicitly) |
| Be fast | Batched UI, low priority, exclusions, threading | Exceeded |
| Actually find keywords | rg backend (literal/regex), tested | Met |
| Check relevant places first | Fast mode walks priority dirs in order | Met |
| (Not requested) | Partial match, history, dark theme, tree grouping, click-to-open, smart filtering, adaptive learning, system bootstrap, 6-tab settings, double-click binary | Added based on user feedback during the session |

No requested feature was removed or downgraded. Everything the user originally asked for is intact and still the default behavior on launch.

---

## 9. Reproducing the build

If the source is modified, rebuild with:

```bash
cd ~/Projects/ksearch
~/.local/bin/python3 -m PyInstaller --onefile --windowed --name ksearch --noconfirm ksearch.py
cp -f dist/ksearch ~/Desktop/ksearch
```

Dependencies: `ripgrep`, `fdfind`, Python 3.10+, PyQt5, PyInstaller (only for build). All present on this machine.
