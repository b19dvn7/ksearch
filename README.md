# ksearch

Fast keyword search GUI for Linux. Single-file PyQt5 app built on `ripgrep` + `fdfind`.

## Features

- **Three depth modes**: `fast` (cwd + priority dirs), `deep` ($HOME), `ultra` (/).
- **Three match modes**: `exact`, `any word`, `all words` (multi-word partial matching).
- **Smart env-var filter**: 4-way mode (off / tag / values only / templates only) for finding API keys and config — drops placeholder lines like `your_key_here`, `<insert>`, `${VAR}`, empty `KEY=`, boosts `.env`/`.toml`/`.conf` files, demotes docs.
- **Strict value**: rewrites env-var queries to require a real value (`KEY=something`).
- **Tree-grouped results**: files as parent rows, hits as expandable children with color-coded tags (green=real value, orange=placeholder).
- **Click-to-open**: double-click a file row to open in editor, hit row to jump to line.
- **History panel**: re-run past searches with one click.
- **Adaptive scoring**: learns from your clicks. Files/dirs/extensions you actually open get a score bonus on future searches.
- **System bootstrap**: import learning data from `recently-used.xbel`, `bash_history`, `zsh_history`, and `viminfo` — gives the scorer a head start based on your real usage.
- **Tabbed settings**: Search · Behavior · Appearance (dark/light theme, fonts, colors) · Defaults · Scoring · Learning.
- **Performance**: batched UI updates, `nice -n 10` + `ionice -c 3` priority, default exclusions for `node_modules`, `.git`, `/proc`, caches, etc. UI stays responsive during full-system scans.

See [`SESSION_SUMMARY.md`](SESSION_SUMMARY.md) for full architecture, scoring math, and the development story.

## Requirements

- Linux (developed on Pop!_OS 22.04)
- Python 3.10+
- PyQt5
- `ripgrep` (`rg`)
- `fdfind` (Debian/Ubuntu package; on other distros it's `fd`)

## Run from source

```bash
git clone https://github.com/b19dvn7/ksearch.git
cd ksearch
python3 ksearch.py
```

## Build a standalone binary

```bash
pip install --user pyinstaller
python3 -m PyInstaller --onefile --windowed --name ksearch --noconfirm ksearch.py
# binary lands in dist/ksearch (~47 MB, fully self-contained)
```

## App-menu launcher

A `.desktop` file template is included. Copy to `~/.local/share/applications/`, update paths, then `update-desktop-database ~/.local/share/applications`.

## Configuration

All state persists locally to `~/.config/ksearch/`:

- `config.json` — settings (theme, weights, defaults, excludes…)
- `history.json` — last 50 searches
- `learning.json` — click-counts driving the adaptive scorer

No network. No telemetry.

## License

MIT
