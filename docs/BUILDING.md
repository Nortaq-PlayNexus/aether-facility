# Building AETHER FACILITY

The facility ships as both runnable Python and a single-file Windows executable
built with [PyInstaller](https://pyinstaller.org/).

## Prerequisites

- Windows 10/11
- Python 3.8+ (built and tested on 3.14)
- `pip install pyinstaller`

## Build

From the facility root:

```bat
python -m PyInstaller ^
  --onefile ^
  --name AETHER_FACILITY ^
  --paths _CORE ^
  --distpath dist ^
  --workpath build\pyi ^
  --specpath build ^
  _SCRIPTS\facility.py
```

Output: `dist\AETHER_FACILITY.exe`

## How the exe finds its data

`_SCRIPTS/facility.py` computes the facility root as follows:

```python
if getattr(sys, "frozen", False):
    FACILITY_ROOT = Path(sys.executable).resolve().parent   # next to the exe
else:
    FACILITY_ROOT = Path(__file__).resolve().parents[1]     # source layout
```

In frozen mode the exe reads `CONFIG.json`, `_DATA/`, `INTAKE/`, etc. **relative to its
own folder**. So keep the exe inside the facility root (the layout shipped in the repo),
or alongside a copy of `CONFIG.json` and `_DATA/sources.json`.

## Smoke test

```bat
echo 8 & echo 0 | AETHER_FACILITY.exe
```

Should print the Command Center menu, dump stats, and exit cleanly.

## Notes

- `--paths _CORE` is required so PyInstaller can resolve the engine imports
  (`db`, `vault`, `scout`, `report`) which are injected into `sys.path` at runtime.
- Runtime data (`master.db`, `manifest.json`, `REPORTING/`) is never bundled — it is
  created on first run in the facility root.
