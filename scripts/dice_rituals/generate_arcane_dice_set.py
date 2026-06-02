from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

from generate_arcane_d20_demo import ROOT, generate


OUTPUT_DIR = ROOT / "app" / "static" / "dice_rituals" / "arcane_dice"


def is_valid_gif(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        with Image.open(path) as image:
            return image.size == (512, 512) and getattr(image, "n_frames", 1) == 72
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="Generate the full arcane_dice d20 GIF set.")
    parser.add_argument("--force", action="store_true", help="Regenerate existing valid GIFs.")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for result in range(1, 21):
        output = OUTPUT_DIR / f"result_{result:02d}.gif"
        if not args.force and is_valid_gif(output):
            print(f"skip {output.relative_to(ROOT)}")
            continue
        generate(output, result)


if __name__ == "__main__":
    main()
