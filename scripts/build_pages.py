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
    DIST_DIR.mkdir(parents=True)
    shutil.copy2(STATIC_DIR / "index.html", DIST_DIR / "index.html")
    shutil.copytree(STATIC_DIR, DIST_DIR / "static", ignore=shutil.ignore_patterns("index.html"))
    (DIST_DIR / ".nojekyll").write_text("", encoding="utf-8")


if __name__ == "__main__":
    main()
