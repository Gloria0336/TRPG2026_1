from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "app" / "static" / "dice_rituals"
DEFAULT_CONFIG = Path(__file__).with_name("dice_ritual_config.json")
SIZE = 512
CX = CY = SIZE // 2
FPS_DURATION_MS = 42
FRAME_COUNT = 56


Color = tuple[int, int, int]


@dataclass(frozen=True)
class Ritual:
    slug: str
    title: str
    subtitle: str
    result: int
    accent: tuple[int, int, int]
    danger: tuple[int, int, int]
    rune_shift: float
    sparks: int
    sigil: str


@dataclass(frozen=True)
class DiceSet:
    id: str
    name: str
    caption: str
    background_top: Color
    background_bottom: Color
    die_fill: Color
    die_text: Color
    bright: Color
    text_primary: Color
    text_secondary: Color
    accent_bias: Color
    spark_bias: Color


RITUALS = [
    Ritual("01_initiative", "INITIATIVE", "turn order awakens", 16, (235, 190, 92), (122, 48, 29), 0.0, 50, "I"),
    Ritual("02_attack", "ATTACK ROLL", "blade seeks blood", 18, (255, 206, 93), (155, 42, 32), 0.7, 62, "A"),
    Ritual("03_saving_throw", "SAVING THROW", "fate grips the throat", 12, (219, 181, 96), (78, 90, 130), 1.1, 52, "S"),
    Ritual("04_ability_check", "ABILITY CHECK", "skill against the dark", 14, (240, 201, 111), (56, 93, 77), 1.6, 46, "C"),
    Ritual("05_stealth", "STEALTH", "shadow holds its breath", 17, (206, 178, 114), (40, 54, 64), 2.2, 58, "N"),
    Ritual("06_perception", "PERCEPTION", "the hidden thing blinks", 19, (255, 217, 121), (71, 88, 83), 2.8, 66, "E"),
    Ritual("07_arcana", "ARCANA", "old glyphs answer", 15, (246, 198, 86), (75, 58, 130), 3.3, 60, "R"),
    Ritual("08_death_save", "DEATH SAVE", "one pulse remains", 10, (222, 174, 72), (140, 26, 33), 3.9, 70, "D"),
    Ritual("09_critical_hit", "CRITICAL HIT", "the crown cracks open", 20, (255, 225, 134), (210, 48, 32), 4.4, 92, "XX"),
    Ritual("10_critical_fail", "CRITICAL FAIL", "the omen bites back", 1, (193, 142, 70), (184, 27, 42), 5.0, 84, "I"),
    Ritual("11_damage", "DAMAGE", "iron counts its debt", 11, (236, 186, 82), (161, 51, 36), 5.6, 56, "DMG"),
    Ritual("12_healing", "HEALING", "gold mends the vein", 13, (246, 210, 116), (58, 118, 83), 6.1, 48, "+"),
    Ritual("13_luck", "LUCK", "coin lands edgewise", 7, (255, 221, 126), (92, 61, 28), 6.7, 64, "L"),
    Ritual("14_curse", "CURSE", "black wax seals it", 4, (215, 154, 72), (132, 31, 76), 7.2, 72, "X"),
    Ritual("15_boss_roll", "BOSS ROLL", "the table goes quiet", 18, (255, 204, 88), (187, 46, 37), 7.8, 88, "B"),
    Ritual("16_secret_roll", "SECRET ROLL", "behind the screen", 9, (198, 170, 112), (44, 44, 54), 8.3, 42, "?"),
    Ritual("17_fate_bend", "FATE BEND", "the thread is pulled", 19, (255, 218, 108), (72, 77, 116), 8.9, 80, "F"),
    Ritual("18_wild_magic", "WILD MAGIC", "the spell laughs first", 6, (244, 190, 91), (103, 56, 153), 9.4, 95, "W"),
    Ritual("19_inspiration", "INSPIRATION", "a vow catches fire", 20, (255, 229, 151), (64, 92, 126), 10.0, 78, "*"),
    Ritual("20_final_verdict", "FINAL VERDICT", "the die has spoken", 20, (255, 224, 127), (174, 39, 31), 10.5, 110, "V"),
]


def color(value: list[int] | tuple[int, int, int]) -> Color:
    if len(value) != 3:
        raise ValueError(f"Expected RGB color with 3 values, got {value!r}")
    return int(value[0]), int(value[1]), int(value[2])


def mix(a: Color, b: Color, weight: float) -> Color:
    return (
        int(lerp(a[0], b[0], weight)),
        int(lerp(a[1], b[1], weight)),
        int(lerp(a[2], b[2], weight)),
    )


def load_dice_set(config_path: Path, requested_id: str | None = None) -> DiceSet:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    dice_sets = raw.get("dice_sets", {})
    dice_set_id = requested_id or raw.get("active_dice_set_id")
    if not dice_set_id:
        raise ValueError(f"{config_path} must define active_dice_set_id or pass --dice-set-id")
    if dice_set_id not in dice_sets:
        available = ", ".join(sorted(dice_sets)) or "(none)"
        raise ValueError(f"Unknown dice_set_id {dice_set_id!r}. Available: {available}")

    data = dice_sets[dice_set_id]
    return DiceSet(
        id=dice_set_id,
        name=str(data.get("name", dice_set_id)),
        caption=str(data.get("caption", "DISCORD DICE RITUAL")),
        background_top=color(data["background_top"]),
        background_bottom=color(data["background_bottom"]),
        die_fill=color(data["die_fill"]),
        die_text=color(data["die_text"]),
        bright=color(data["bright"]),
        text_primary=color(data["text_primary"]),
        text_secondary=color(data["text_secondary"]),
        accent_bias=color(data["accent_bias"]),
        spark_bias=color(data["spark_bias"]),
    )


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/cinzel.ttf",
        "C:/Windows/Fonts/georgia.ttf",
        "C:/Windows/Fonts/georgiab.ttf" if bold else "C:/Windows/Fonts/georgia.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


FONT_TITLE = font(28, True)
FONT_SUB = font(16)
FONT_RESULT = font(88, True)
FONT_DIE = font(48, True)
FONT_RUNE = font(18, True)
FONT_SMALL = font(13, True)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def ease_out_back(t: float) -> float:
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * pow(t - 1, 3) + c1 * pow(t - 1, 2)


def rgba(color: tuple[int, int, int], alpha: int) -> tuple[int, int, int, int]:
    return color[0], color[1], color[2], max(0, min(255, alpha))


def centered(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fnt, fill):
    box = draw.textbbox((0, 0), text, font=fnt)
    w = box[2] - box[0]
    h = box[3] - box[1]
    draw.text((xy[0] - w / 2, xy[1] - h / 2), text, font=fnt, fill=fill)


def polygon(cx: float, cy: float, radius: float, sides: int, rot: float) -> list[tuple[float, float]]:
    return [
        (cx + math.cos(rot + math.tau * i / sides) * radius, cy + math.sin(rot + math.tau * i / sides) * radius)
        for i in range(sides)
    ]


def draw_background(draw: ImageDraw.ImageDraw, ritual: Ritual, dice_set: DiceSet, frame: int):
    for y in range(SIZE):
        t = y / SIZE
        r = int(lerp(dice_set.background_top[0], dice_set.background_bottom[0], t))
        g = int(lerp(dice_set.background_top[1], dice_set.background_bottom[1], t))
        b = int(lerp(dice_set.background_top[2], dice_set.background_bottom[2], t))
        draw.line((0, y, SIZE, y), fill=(r, g, b, 255))

    pulse = (math.sin(frame * 0.17 + ritual.rune_shift) + 1) / 2
    halo = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    accent = mix(ritual.accent, dice_set.accent_bias, 0.62)
    for radius, alpha in [(238, 16), (188, 24), (132, int(22 + pulse * 18))]:
        hd.ellipse((CX - radius, CY - radius, CX + radius, CY + radius), outline=rgba(accent, alpha), width=3)
    return halo.filter(ImageFilter.GaussianBlur(12))


def draw_magic_circle(layer: Image.Image, ritual: Ritual, dice_set: DiceSet, progress: float, frame: int):
    draw = ImageDraw.Draw(layer)
    spin = frame * 0.045 + ritual.rune_shift
    glow = int(115 + 75 * math.sin(frame * 0.19 + ritual.rune_shift))
    accent = mix(ritual.accent, dice_set.accent_bias, 0.62)
    for radius, width, offset in [(204, 3, 0), (171, 2, 0.35), (126, 2, -0.25), (84, 1, 0.9)]:
        box = (CX - radius, CY - radius, CX + radius, CY + radius)
        start = math.degrees(spin + offset)
        draw.arc(box, start, start + 286, fill=rgba(accent, glow), width=width)
        draw.arc(box, start + 190, start + 242, fill=rgba(dice_set.bright, glow), width=width)

    for sides, radius, alpha, rot in [(3, 153, 90, spin), (4, 116, 62, -spin * 0.75), (6, 188, 72, spin * 0.45)]:
        pts = polygon(CX, CY, radius, sides, rot)
        draw.line(pts + [pts[0]], fill=rgba(accent, alpha), width=2)

    runes = ["D20", "STR", "DEX", "WIS", "CHA", "CON", "INT", ritual.sigil]
    for i, text in enumerate(runes):
        angle = spin * 0.7 + math.tau * i / len(runes)
        x = CX + math.cos(angle) * 218
        y = CY + math.sin(angle) * 218
        centered(draw, (int(x), int(y)), text, FONT_RUNE, rgba(accent, 140))

    sweep = int(min(255, progress * 340))
    draw.line((CX, CY, CX + math.cos(spin * 2) * 224, CY + math.sin(spin * 2) * 224), fill=rgba(dice_set.bright, sweep), width=2)


def draw_sparks(layer: Image.Image, ritual: Ritual, dice_set: DiceSet, frame: int):
    draw = ImageDraw.Draw(layer)
    rng = random.Random(f"{dice_set.id}:{ritual.slug}")
    accent = mix(ritual.accent, dice_set.accent_bias, 0.62)
    for i in range(ritual.sparks):
        seed_angle = rng.random() * math.tau
        drift = frame * (0.012 + rng.random() * 0.026)
        radius = 48 + rng.random() * 210
        wobble = math.sin(frame * 0.09 + i) * 9
        angle = seed_angle + drift
        x = CX + math.cos(angle) * (radius + wobble)
        y = CY + math.sin(angle) * (radius + wobble)
        size = 1 + rng.random() * 3
        alpha = int(45 + 130 * ((math.sin(frame * 0.21 + i * 1.7) + 1) / 2))
        color = accent if i % 5 else dice_set.spark_bias
        draw.ellipse((x - size, y - size, x + size, y + size), fill=rgba(color, alpha))


def draw_die(layer: Image.Image, ritual: Ritual, dice_set: DiceSet, frame: int, progress: float):
    draw = ImageDraw.Draw(layer)
    roll_t = min(1, progress / 0.72)
    reveal_t = max(0, (progress - 0.64) / 0.36)
    orbit = (1 - roll_t) * 112
    wobble = math.sin(frame * 0.62 + ritual.rune_shift) * (1 - reveal_t) * 24
    scale = 1 + math.sin(frame * 0.3) * 0.04 + ease_out_back(reveal_t) * 0.38
    x = CX + math.cos(frame * 0.29 + ritual.rune_shift) * orbit
    y = CY + math.sin(frame * 0.23 + ritual.rune_shift) * orbit + wobble
    radius = 62 * scale
    rot = frame * 0.22 * (1 - reveal_t) + ritual.rune_shift

    shadow = polygon(x + 8, y + 10, radius + 8, 20, rot)
    draw.polygon(shadow, fill=(0, 0, 0, 90))
    pts = polygon(x, y, radius, 20, rot)
    accent = mix(ritual.accent, dice_set.accent_bias, 0.62)
    draw.polygon(pts, fill=rgba(dice_set.die_fill, 245), outline=rgba(accent, 230))

    inner = polygon(x, y, radius * 0.73, 20, -rot * 0.6)
    draw.line(inner + [inner[0]], fill=rgba(dice_set.bright, 95), width=2)
    for i in range(0, 20, 2):
        draw.line((x, y, pts[i][0], pts[i][1]), fill=rgba(accent, 50), width=1)

    fake_value = ((frame * 7 + ritual.result * 3) % 20) + 1
    value = ritual.result if reveal_t > 0.55 else fake_value
    value_font = FONT_RESULT if reveal_t > 0.55 else FONT_DIE
    fill = rgba(dice_set.die_text, 255) if ritual.result == 20 else rgba(accent, 245)
    if ritual.result == 1 and reveal_t > 0.55:
        fill = rgba((231, 61, 74), 245)
    centered(draw, (int(x), int(y - 3)), str(value), value_font, fill)

    if ritual.result == 1 and reveal_t > 0.55:
        for offset in [-18, 12, 28]:
            draw.line((x + offset, y - 48, x + offset * 0.4, y + 50), fill=rgba(ritual.danger, 185), width=3)


def draw_title(layer: Image.Image, ritual: Ritual, dice_set: DiceSet, progress: float):
    draw = ImageDraw.Draw(layer)
    top_alpha = int(min(255, progress * 520))
    accent = mix(ritual.accent, dice_set.accent_bias, 0.62)
    centered(draw, (CX, 48), ritual.title, FONT_TITLE, rgba(dice_set.text_primary, top_alpha))
    centered(draw, (CX, 78), ritual.subtitle, FONT_SUB, rgba(dice_set.text_secondary, int(top_alpha * 0.82)))
    draw.rounded_rectangle((128, 438, 384, 467), radius=8, outline=rgba(accent, 130), fill=(5, 5, 7, 150), width=1)
    centered(draw, (CX, 453), dice_set.caption, FONT_SMALL, rgba(dice_set.text_secondary, 205))


def draw_burst(layer: Image.Image, ritual: Ritual, dice_set: DiceSet, frame: int, progress: float):
    reveal_t = max(0, (progress - 0.72) / 0.28)
    if reveal_t <= 0:
        return
    draw = ImageDraw.Draw(layer)
    alpha = int((1 - reveal_t) * 220)
    radius = 36 + reveal_t * 248
    color = dice_set.bright if ritual.result >= 19 else mix(ritual.accent, dice_set.accent_bias, 0.62)
    if ritual.result == 1:
        color = ritual.danger
    draw.ellipse((CX - radius, CY - radius, CX + radius, CY + radius), outline=rgba(color, alpha), width=5)
    for i in range(28):
        angle = math.tau * i / 28 + frame * 0.03
        inner = 58 + reveal_t * 32
        outer = 86 + reveal_t * 202
        draw.line(
            (
                CX + math.cos(angle) * inner,
                CY + math.sin(angle) * inner,
                CX + math.cos(angle) * outer,
                CY + math.sin(angle) * outer,
            ),
            fill=rgba(color, int(alpha * 0.72)),
            width=2,
        )


def render_frame(ritual: Ritual, dice_set: DiceSet, frame: int) -> Image.Image:
    progress = frame / (FRAME_COUNT - 1)
    base = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 255))
    bg = ImageDraw.Draw(base)
    base.alpha_composite(draw_background(bg, ritual, dice_set, frame))

    circle = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw_magic_circle(circle, ritual, dice_set, progress, frame)
    base.alpha_composite(circle.filter(ImageFilter.GaussianBlur(0.25)))

    sparks = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw_sparks(sparks, ritual, dice_set, frame)
    base.alpha_composite(sparks)

    die = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw_burst(die, ritual, dice_set, frame, progress)
    draw_die(die, ritual, dice_set, frame, progress)
    base.alpha_composite(die.filter(ImageFilter.GaussianBlur(0.1)))

    ui = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw_title(ui, ritual, dice_set, progress)
    base.alpha_composite(ui)

    vignette = Image.new("L", (SIZE, SIZE), 0)
    vd = ImageDraw.Draw(vignette)
    vd.ellipse((-70, -50, SIZE + 70, SIZE + 80), fill=230)
    vignette = Image.eval(vignette.filter(ImageFilter.GaussianBlur(38)), lambda p: 255 - p)
    shade = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    shade.putalpha(vignette.point(lambda p: int(p * 0.78)))
    base.alpha_composite(shade)
    return base.convert("P", palette=Image.Palette.ADAPTIVE, colors=128)


def generate(output_dir: Path, dice_set: DiceSet):
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"dice_set_id={dice_set.id} name={dice_set.name}")
    for ritual in RITUALS:
        frames = [render_frame(ritual, dice_set, frame) for frame in range(FRAME_COUNT)]
        output = output_dir / f"{ritual.slug}.gif"
        frames[0].save(
            output,
            save_all=True,
            append_images=frames[1:],
            duration=FPS_DURATION_MS,
            loop=0,
            optimize=True,
            disposal=2,
        )
        print(output.relative_to(ROOT))


def main():
    parser = argparse.ArgumentParser(description="Generate 20 themed 512px Discord dice ritual GIFs.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--dice-set-id", help="Override active_dice_set_id from dice_ritual_config.json.")
    args = parser.parse_args()
    dice_set = load_dice_set(args.config, args.dice_set_id)
    output_dir = args.output / dice_set.id if args.output == DEFAULT_OUTPUT else args.output
    generate(output_dir, dice_set)


if __name__ == "__main__":
    main()
