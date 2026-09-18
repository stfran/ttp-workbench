#!/usr/bin/env python3
"""Make RAF-AG filesystem traversal deterministic across installations."""

from pathlib import Path
import re
import sys


FILES = (
    "utils.py",
    "classes/managment.py",
    "classes/finalizing.py",
)
LISTDIR_ASSIGNMENT = re.compile(
    r"^(?P<indent>\s*files\s*=\s*)os\.listdir\((?P<argument>[^)\n]+)\)(?P<trailing>\s*)$",
    re.MULTILINE,
)


def patch(root: Path) -> int:
    changed = 0
    matched = 0
    for relative in FILES:
        path = root / relative
        source = path.read_text(encoding="utf-8")

        def replace(match: re.Match[str]) -> str:
            nonlocal matched
            matched += 1
            return (
                f"{match.group('indent')}sorted(os.listdir({match.group('argument')}))"
                f"{match.group('trailing')}"
            )

        updated = LISTDIR_ASSIGNMENT.sub(replace, source)
        if updated != source:
            path.write_text(updated, encoding="utf-8")
            changed += 1

    if matched == 0:
        already_patched = sum(
            path.read_text(encoding="utf-8").count("files = sorted(os.listdir(")
            for path in (root / relative for relative in FILES)
        )
        if already_patched != 14:
            raise RuntimeError(
                f"Expected 14 deterministic directory traversals, found {already_patched}"
            )
    elif matched != 14:
        raise RuntimeError(f"Expected to patch 14 directory traversals, found {matched}")
    return changed


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"Usage: {Path(sys.argv[0]).name} RAF_AG_ROOT")
    root = Path(sys.argv[1]).resolve()
    changed = patch(root)
    print(f"RAF-AG deterministic traversal patch: {changed} file(s) changed")
