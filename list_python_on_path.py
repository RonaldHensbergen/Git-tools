#!/usr/bin/env python3
"""List Python scripts that are executable from anywhere via $PATH.

Scans every directory on $PATH (in order, skipping duplicates) for
executable entries that are Python scripts -- either by file extension
(`.py`) or by a `#!...python` shebang line for extensionless commands (the
convention used by this project's own tools, e.g. `check_my_work` symlinked
to `check_my_work.py`).

For each match, reports the command name as you'd type it, the real
underlying file (following symlinks), and which PATH directory it was
found in. Useful for auditing what's actually exposed on PATH versus what
exists in a project like this one, and for catching broken symlinks left
behind by a moved/renamed script.

Pass --broken-only to only show entries whose target no longer exists.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

_SHEBANG_PYTHON_HINTS = ("python",)


@dataclass
class PathScript:
    command: str
    path: str
    real_path: str
    directory: str
    is_symlink: bool
    broken: bool

    def to_json(self) -> dict:
        return asdict(self)


def _looks_like_python_shebang(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            first_line = f.readline(256)
    except OSError:
        return False
    if not first_line.startswith(b"#!"):
        return False
    shebang = first_line.decode("utf-8", errors="replace").strip()
    return any(hint in shebang for hint in _SHEBANG_PYTHON_HINTS)


def _is_python_script(path: Path) -> bool:
    if path.suffix == ".py":
        return True
    # Extensionless commands (e.g. symlinks like `check_my_work`) are only
    # counted if their target still exists and starts with a python
    # shebang; a dangling symlink can't be read, so it falls through to
    # the broken-entry handling in iter_path_scripts() instead.
    if not path.exists():
        return False
    return _looks_like_python_shebang(path)


def ordered_unique_path_dirs() -> list[Path]:
    seen: set[str] = set()
    dirs: list[Path] = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or entry in seen:
            continue
        seen.add(entry)
        dirs.append(Path(entry))
    return dirs


def iter_path_scripts() -> list[PathScript]:
    results: list[PathScript] = []
    seen_commands: set[str] = set()

    for directory in ordered_unique_path_dirs():
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            continue

        for entry in entries:
            if entry.name in seen_commands:
                continue

            is_symlink = entry.is_symlink()
            broken = is_symlink and not entry.exists()

            if not broken and not _is_python_script(entry):
                continue
            if broken and entry.suffix != ".py" and not is_symlink:
                continue

            if broken:
                # A dangling symlink's target can't be inspected, so fall
                # back to name-based heuristics: report it if the link
                # itself or its (now-missing) target name suggests Python.
                target = os.readlink(entry)
                if entry.suffix != ".py" and not target.endswith(".py"):
                    continue

            seen_commands.add(entry.name)
            try:
                real_path = str(entry.resolve())
            except OSError:
                real_path = str(entry)

            results.append(
                PathScript(
                    command=entry.name,
                    path=str(entry),
                    real_path=real_path,
                    directory=str(directory),
                    is_symlink=is_symlink,
                    broken=broken,
                )
            )

    return results


def print_report(scripts: list[PathScript], broken_only: bool) -> None:
    shown = [s for s in scripts if s.broken] if broken_only else scripts

    if not shown:
        print("(no broken Python entries found on PATH)" if broken_only else "(no Python scripts found on PATH)")
        return

    for script in shown:
        marker = "  BROKEN" if script.broken else ""
        link_note = f" -> {script.real_path}" if script.is_symlink else ""
        print(f"{script.command}{link_note}  [{script.directory}]{marker}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--broken-only",
        action="store_true",
        help="Only list entries whose symlink target no longer exists",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the human-readable report",
    )
    args = parser.parse_args()

    scripts = iter_path_scripts()
    broken = [s for s in scripts if s.broken]

    if args.json:
        shown = broken if args.broken_only else scripts
        print(json.dumps([s.to_json() for s in shown], indent=2))
        return 1 if (args.broken_only and broken) or (not args.broken_only and broken) else 0

    print_report(scripts, args.broken_only)
    if broken and not args.broken_only:
        print(f"\n{len(broken)} broken entr{'y' if len(broken) == 1 else 'ies'} found (see above).")

    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
