"""Build the static dashboard artifact for GitHub Pages."""
from __future__ import annotations

import shutil
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT_DIR / "app" / "web" / "static"
DIST_DIR = ROOT_DIR / "dist"


def main() -> None:
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    shutil.copytree(STATIC_DIR, DIST_DIR)
    (DIST_DIR / ".nojekyll").write_text("", encoding="utf-8")


if __name__ == "__main__":
    main()
