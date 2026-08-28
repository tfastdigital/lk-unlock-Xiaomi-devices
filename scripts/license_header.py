#!/usr/bin/env python3
"""Keep the AGPL copyright notice in every source file.

This small helper scans the repository for Python source files and makes sure
each one starts with the project's copyright and license header. It is used by
the ``check-license`` and ``add-license`` tasks so the attribution is never
accidentally dropped from the solution.

Usage:
    python scripts/license_header.py check   # report files missing the header
    python scripts/license_header.py add     # insert the header where missing
    python scripts/license_header.py list    # list every file that carries it

The header text lives at the top of this module so there is a single source of
truth for the wording.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Single source of truth for the header that must appear in each .py file.
HEADER = """\
# lk-unlock - Unlock the bootloader of Xiaomi MTK devices by patching the LK image.
# Copyright (C) 2026 TFast Digital Agency - https://tfastdigital.com
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU Affero General Public License for more
# details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

ROOT = Path(__file__).resolve().parent.parent

# Files we never touch (generated, vendored, or non-source).
EXCLUDES = {"__pycache__", ".git", "venv", ".venv", "node_modules"}


def _source_files() -> list[Path]:
    """Return every .py file under the repo root, skipping excluded dirs."""
    files: list[Path] = []
    for path in ROOT.rglob("*.py"):
        if any(part in EXCLUDES for part in path.parts):
            continue
        files.append(path)
    return sorted(files)


def _has_header(text: str) -> bool:
    return "Copyright (C) 2026 TFast Digital Agency" in text


def _strip_shebang(text: str) -> str:
    """Remove a leading shebang line so the header can be inserted after it."""
    if text.startswith("#!"):
        newline = text.find("\n")
        if newline != -1:
            return text[newline + 1 :], text[: newline + 1]
    return text, ""


def check() -> int:
    """Report files missing the header. Exit 1 if any are found."""
    missing = [p for p in _source_files() if not _has_header(p.read_text(encoding="utf-8"))]
    if missing:
        print(f"[!] {len(missing)} file(s) missing the copyright header:")
        for path in missing:
            print(f"    - {path.relative_to(ROOT)}")
        print("\nRun: python scripts/license_header.py add")
        return 1
    print(f"[+] All {len(_source_files())} source files carry the copyright header.")
    return 0


def add() -> int:
    """Insert the header into any file that is missing it."""
    changed = 0
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        if _has_header(text):
            continue
        body, shebang = _strip_shebang(text)
        # Only prepend when the file still has real content after the shebang.
        if body.strip():
            path.write_text(shebang + HEADER + body, encoding="utf-8")
            print(f"[+] Header added to {path.relative_to(ROOT)}")
            changed += 1
    if changed:
        print(f"\n[+] Added the header to {changed} file(s).")
    else:
        print("[+] No files needed a header.")
    return 0


def list_files() -> int:
    """Print every file that already carries the header."""
    files = [p for p in _source_files() if _has_header(p.read_text(encoding="utf-8"))]
    for path in files:
        print(path.relative_to(ROOT))
    print(f"\n[+] {len(files)} file(s) carry the header.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if not argv or argv[0] not in {"check", "add", "list"}:
        print(__doc__)
        return 2
    if argv[0] == "check":
        return check()
    if argv[0] == "add":
        return add()
    return list_files()


if __name__ == "__main__":
    sys.exit(main())
