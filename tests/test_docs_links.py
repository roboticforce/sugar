"""Guard against documentation link rot.

Relative links in the README, docs/ and the macOS app README are part of the
product surface: they are how people discover the newer features. This test
fails when a relative Markdown link or image points at a path that does not
exist. External URLs and pure anchors are ignored (they cannot be checked
offline, and anchor validation is out of scope).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
FENCE = re.compile(r"```.*?```", re.DOTALL)


def _docs_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "apps/macos/README.md"]
    files.extend(sorted((ROOT / "docs").rglob("*.md")))
    return [path for path in files if path.is_file()]


def _relative_targets(text: str):
    text = FENCE.sub("", text)
    for raw in LINK.findall(text):
        target = raw.strip().split()[0].strip("<>")
        if not target or target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path = target.split("#", 1)[0].split("?", 1)[0]
        if path:
            yield path


def test_relative_documentation_links_resolve():
    broken: list[str] = []
    for path in _docs_files():
        for target in _relative_targets(path.read_text(encoding="utf-8")):
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                broken.append(f"{path.relative_to(ROOT)} -> {target}")
    assert not broken, "broken relative documentation links:\n" + "\n".join(broken)
