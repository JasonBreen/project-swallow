# Editor setup: VS Code and Antigravity

The repository ships one editor configuration in `.vscode/`. It works in
Visual Studio Code and in Google Antigravity, which is a VS Code fork and reads
the same settings, launch, and task files. The toolchain needs Python 3.11 or
newer and has no third-party runtime dependencies, so a virtual environment is
optional.

## Open the project

1. Open the checkout folder (`G:\Project-Swallow` on the owner's machine) or
   `project-swallow.code-workspace`, and trust the workspace. Restricted mode
   ignores the terminal environment and tasks.
2. Run **Python: Select Interpreter** and choose Python 3.11 or newer. If you
   want a virtual environment, **Python: Create Environment** places `.venv`
   inside the checkout, which keeps it on `G:` and out of Git.
3. Install the extensions below.

| Editor | Extensions |
|---|---|
| VS Code | Accept the workspace recommendations: Python, Python Debugger, and Pylance. |
| Antigravity | Install Python and Python Debugger from its Open VSX marketplace. Pylance is licensed only for Microsoft's VS Code and is not available there; install **basedpyright** (`detachhead.basedpyright`) as the language server instead. |

Do not run basedpyright and Pylance together in VS Code; both are language
servers and duplicate every diagnostic.

## What is configured

- **Testing.** Test Explorer discovers the unittest suite in `tests/`.
  `.vscode/swallow.env` supplies `PYTHONPATH=src`, so tests import
  `swallow_tools` on a fresh clone without a `.env` file or an editable install.
  Discovery uses the repository root as the top-level directory (`-t .`).
  Recent Python extension versions force that for project-based discovery
  ([vscode-python#26033](https://github.com/microsoft/vscode-python/issues/26033)),
  so `tests/` is a package whose `__init__.py` keeps the shared test helpers
  importable. `-s tests` with and without `-t .` find the same tests.
  The **Swallow: unit tests** task is the default test task and runs the same
  command as `AGENTS.md`.
- **Run and Debug.** Use F5 to debug or Ctrl+F5 to run without the debugger:

  | Configuration | Command |
  |---|---|
  | Swallow: inspect disc image | `python -m swallow_tools IMAGE --hash --file-hashes` |
  | Swallow: decode BPH index | `python -m swallow_tools.bph IMAGE` |
  | Swallow: compare two disc images | `python -m swallow_tools.compare LEFT RIGHT` |
  | Swallow: validate BPH reference disc | `scripts/validate_bph_references.py --output-dir local-reports --reference KEY IMAGE` |
  | Swallow: walk BPB containers | `python -m swallow_tools.bpb IMAGE --decode` |
  | Swallow: validate BPB reference disc | `scripts/validate_bpb_references.py --output-dir local-reports --reference KEY IMAGE` |
  | Swallow: unit tests | `python -m unittest discover -s tests -v` |
  | Python: current file | the open file |

  Disc configurations prompt for an extracted BIN image. The defaults follow
  the `G:\ProjectSwallow-data` layout in [discs/README.md](../discs/README.md);
  replace them on other machines. The tools read images without modifying
  them. The validation runs write metadata JSON only to the ignored
  `local-reports/` directory, and the selected key is checked against
  baseline hashes rather than trusted from the filename. The BPB walk decodes
  Ulz data in memory and prints metadata only; the BPB validation hashes the
  whole image and walks every entry, so it takes several minutes per disc.
- **Terminal.** New integrated terminals set `PYTHONPATH` to `src`, so the
  README commands work without setup.
- **Temporary files.** Every debug configuration, Test Explorer run (through
  `swallow.env`), and new Windows terminal sets `TEMP` and `TMP` to
  `G:\ProjectSwallow-data\tmp`, because `AGENTS.md` forbids the `C:` drive.
  Create that directory first. Where it does not exist, Python's `tempfile`
  skips it and falls back to the system default, which on the owner's machine
  is on `C:`.
- **Type analysis.** `[tool.pyright]` in `pyproject.toml` is read by both
  Pylance and basedpyright, so both editors report the same results. It
  analyses `src/`, `tests/`, and `scripts/` as Python 3.11, the oldest
  supported version, and uses Pylance's default `off` mode. In that mode the
  editor reports syntax errors, unresolved imports, and undefined names.
  Without this section, basedpyright's stricter default reported 27 errors and
  349 warnings on the current code. The `basic` and `standard` modes each
  report 20 errors. Most are `int | None` arithmetic in `bph.py` and the
  validation script, or negative tests that deliberately pass invalid types.
  Raising the mode needs a separate code change.
- **Files.** New and saved files use LF line endings and a final newline, which
  matches every tracked file. Trailing whitespace is trimmed. Disc archives
  are excluded from search.

`project-swallow.code-workspace` repeats the terminal settings because VS Code
applies window-scoped settings from the workspace file, not from the folder,
when the workspace file is opened.

## Antigravity agents

Antigravity's agents must follow [AGENTS.md](../AGENTS.md): use the `G:` drive,
keep each task bounded, and run the test command before finishing. Under that
contract, starting parallel agents from the Agent Manager requires an explicit
request. Recent Antigravity versions are reported to load `AGENTS.md` from the
workspace root. If yours does not, add it as a workspace rule.

## Checked

After merging tooling v0.5.0, all 111 unit tests pass on Windows with Python
3.12. `-s tests` and `-s tests -t .` discover the same 111 tests; before
`tests/__init__.py`, the second form failed with `Start directory is not
importable`. `launch.json` and `settings.json` parse as JSON. The editors
themselves were not relaunched, and the type-checker counts below predate the
v0.4.0 modules and were not re-measured.

On Linux with Python 3.13, before v0.4.0: all 53 unit tests pass. With the shared
configuration, the pyright 1.1.414 and basedpyright 1.40.2 command-line
checkers both report 0 errors and 0 warnings. Without `PYTHONPATH`, test
discovery fails with `No module named 'swallow_tools'`; the committed env file
prevents that. The editors themselves were not launched for this check.
