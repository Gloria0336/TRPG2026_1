from __future__ import annotations

import argparse
import math
import random
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "app" / "static" / "dice_rituals" / "demos" / "arcane_d20_result_6.gif"
SIZE = 512
CX = CY = SIZE // 2
FRAME_COUNT = 72
FRAME_DURATION_MS = 76

GOLD = (238, 187, 80)
BRIGHT_GOLD = (255, 232, 154)
DEEP_GOLD = (137, 87, 29)
BLACK_GLASS = (16, 13, 10)
EMBER = (255, 108, 38)
GREEN_FIRE = (71, 255, 64)
DEEP_GREEN = (12, 93, 36)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/georgiab.ttf" if bold else "C:/Windows/Fonts/georgia.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


FONT_NUMBER = font(54, True)
FONT_FINAL_NUMBER = font(78, True)


def rgba(color: tuple[int, int, int], alpha: int) -> tuple[int, int, int, int]:
    return color[0], color[1], color[2], max(0, min(255, int(alpha)))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return int(lerp(a[0], b[0], t)), int(lerp(a[1], b[1], t)), int(lerp(a[2], b[2], t))


def centered(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, fnt, fill):
    box = draw.textbbox((0, 0), text, font=fnt)
    draw.text((xy[0] - (box[0] + box[2]) / 2, xy[1] - (box[1] + box[3]) / 2), text, font=fnt, fill=fill)


def normalize(v: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(sum(axis * axis for axis in v))
    return v[0] / length, v[1] / length, v[2] / length


def rotate(v: tuple[float, float, float], ax: float, ay: float, az: float) -> tuple[float, float, float]:
    x, y, z = v
    cy, sy = math.cos(ay), math.sin(ay)
    x, z = x * cy + z * sy, -x * sy + z * cy
    cx, sx = math.cos(ax), math.sin(ax)
    y, z = y * cx - z * sx, y * sx + z * cx
    cz, sz = math.cos(az), math.sin(az)
    x, y = x * cz - y * sz, x * sz + y * cz
    return x, y, z


def project(v: tuple[float, float, float], scale: float = 172, offset: tuple[float, float] = (0, 0)) -> tuple[float, float]:
    x, y, z = v
    perspective = 2.75 / (2.75 + z)
    return CX + offset[0] + x * scale * perspective, CY + offset[1] + y * scale * perspective


def icosahedron() -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]:
    phi = (1 + math.sqrt(5)) / 2
    verts = [
        (-1, phi, 0),
        (1, phi, 0),
        (-1, -phi, 0),
        (1, -phi, 0),
        (0, -1, phi),
        (0, 1, phi),
        (0, -1, -phi),
        (0, 1, -phi),
        (phi, 0, -1),
        (phi, 0, 1),
        (-phi, 0, -1),
        (-phi, 0, 1),
    ]
    faces = [
        (0, 11, 5),
        (0, 5, 1),
        (0, 1, 7),
        (0, 7, 10),
        (0, 10, 11),
        (1, 5, 9),
        (5, 11, 4),
        (11, 10, 2),
        (10, 7, 6),
        (7, 1, 8),
        (3, 9, 4),
        (3, 4, 2),
        (3, 2, 6),
        (3, 6, 8),
        (3, 8, 9),
        (4, 9, 5),
        (2, 4, 11),
        (6, 2, 10),
        (8, 6, 7),
        (9, 8, 1),
    ]
    return [normalize(v) for v in verts], faces


VERTS, FACES = icosahedron()
FACE_NUMBERS = [6, 14, 2, 17, 9, 20, 4, 11, 1, 18, 7, 13, 3, 16, 10, 19, 5, 12, 8, 15]


def triangle_center(points: list[tuple[float, float]]) -> tuple[float, float]:
    return sum(p[0] for p in points) / 3, sum(p[1] for p in points) / 3


def signed_area(points: list[tuple[float, float]]) -> float:
    area = 0.0
    for index, (x1, y1) in enumerate(points):
        x2, y2 = points[(index + 1) % len(points)]
        area += x1 * y2 - x2 * y1
    return area / 2


def inset_triangle(points: list[tuple[float, float]], amount: float) -> list[tuple[float, float]]:
    cx, cy = triangle_center(points)
    return [(lerp(cx, x, amount), lerp(cy, y, amount)) for x, y in points]


def label_triangle(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    top_index = min(range(3), key=lambda index: points[index][1])
    top = points[top_index]
    lower = [points[index] for index in range(3) if index != top_index]
    lower_left, lower_right = sorted(lower, key=lambda point: point[0])
    return [top, lower_left, lower_right]


def face_normal(face_verts: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    a, b, c = face_verts
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    return normalize((uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx))


def smoothstep(t: float) -> float:
    t = max(0, min(1, t))
    return t * t * (3 - 2 * t)


@lru_cache(maxsize=20)
def find_target_rotation(result: int) -> tuple[float, float, float]:
    face_index = FACE_NUMBERS.index(result)
    target_face = FACES[face_index]
    best_score = -999.0
    best_rotation = (-0.35, 0.0, 0.0)
    for ax_step in range(-9, 10):
        ax = ax_step * 0.12
        for ay_step in range(0, 53):
            ay = ay_step * math.tau / 52
            for az_step in range(-8, 9):
                az = az_step * 0.10
                rotated = [rotate(v, ax, ay, az) for v in VERTS]
                normal = face_normal([rotated[i] for i in target_face])
                center_z = sum(rotated[i][2] for i in target_face) / 3
                center_x = sum(rotated[i][0] for i in target_face) / 3
                center_y = sum(rotated[i][1] for i in target_face) / 3
                score = normal[2] * 3.0 + center_z - abs(center_x) * 0.28 - abs(center_y) * 0.28
                if score > best_score:
                    best_score = score
                    best_rotation = (ax, ay, az)
    return best_rotation


def draw_background(frame: int) -> Image.Image:
    return Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))


def draw_arcane_fire(layer: Image.Image, frame: int):
    draw = ImageDraw.Draw(layer)
    rng = random.Random(6042)
    for i in range(92):
        phase = (frame * 0.026 + rng.random()) % 1
        angle = rng.random() * math.tau + frame * 0.018
        radius = lerp(72, 230, phase)
        bend = math.sin(frame * 0.11 + i) * 18
        x = CX + math.cos(angle) * (radius + bend)
        y = CY + math.sin(angle) * (radius + bend * 0.45)
        size = lerp(4.2, 0.8, phase) * (0.7 + rng.random() * 0.8)
        color = BRIGHT_GOLD if i % 4 else EMBER
        alpha = int(210 * (1 - phase) ** 1.4)
        draw.ellipse((x - size, y - size, x + size, y + size), fill=rgba(color, alpha))

    for i in range(18):
        angle = frame * 0.08 + i * math.tau / 18
        start = 72 + math.sin(frame * 0.15 + i) * 12
        end = 186 + math.cos(frame * 0.09 + i) * 28
        draw.line(
            (
                CX + math.cos(angle) * start,
                CY + math.sin(angle) * start,
                CX + math.cos(angle + 0.045) * end,
                CY + math.sin(angle + 0.045) * end,
            ),
            fill=rgba(BRIGHT_GOLD, 52),
            width=2,
        )


def draw_face_runes(draw: ImageDraw.ImageDraw, points: list[tuple[float, float]], frame: int, alpha: int):
    center = triangle_center(points)
    edge = min(math.dist(points[0], points[1]), math.dist(points[1], points[2]), math.dist(points[2], points[0]))
    cx, cy = center
    green_alpha = int(alpha * (0.55 + 0.25 * math.sin(frame * 0.22 + cx * 0.013)))
    glass = inset_triangle(points, 0.72)
    draw.polygon(glass, fill=rgba(DEEP_GREEN, green_alpha))

    pulse = math.sin(frame * 0.17 + cx * 0.021 + cy * 0.013)
    scan = (math.sin(frame * 0.11 + edge * 0.03) + 1) / 2
    for amount, line_alpha in [
        (0.79 + pulse * 0.035, alpha),
        (0.57 + math.sin(frame * 0.19 + cx * 0.017) * 0.055, int(alpha * 0.74)),
        (0.35 + math.cos(frame * 0.23 + cy * 0.015) * 0.045, int(alpha * 0.48)),
    ]:
        tri = inset_triangle(points, amount)
        draw.line(tri + [tri[0]], fill=rgba(BRIGHT_GOLD, line_alpha), width=2 if amount > 0.7 else 1)

    mids = [
        ((points[0][0] + points[1][0]) / 2, (points[0][1] + points[1][1]) / 2),
        ((points[1][0] + points[2][0]) / 2, (points[1][1] + points[2][1]) / 2),
        ((points[2][0] + points[0][0]) / 2, (points[2][1] + points[0][1]) / 2),
    ]
    for i, mid in enumerate(mids):
        node = (
            lerp(center[0], mid[0], 0.52 + 0.20 * math.sin(frame * 0.13 + i * 2.1)),
            lerp(center[1], mid[1], 0.52 + 0.20 * math.cos(frame * 0.15 + i * 1.7)),
        )
        rune = inset_triangle([points[i], node, center], 0.62 + 0.16 * math.sin(frame * 0.14 + i))
        draw.line(rune + [rune[0]], fill=rgba(GOLD, int(alpha * 0.62)), width=1)

    orbit_points = []
    for i in range(6):
        angle = frame * 0.075 + i * math.tau / 6
        warp = 0.34 + 0.07 * math.sin(frame * 0.18 + i * 1.3)
        orbit_points.append((cx + math.cos(angle) * edge * warp, cy + math.sin(angle) * edge * warp * 0.55))
    for i, point in enumerate(orbit_points):
        next_point = orbit_points[(i + 2) % len(orbit_points)]
        draw.line((point[0], point[1], next_point[0], next_point[1]), fill=rgba(GREEN_FIRE, int(alpha * (0.18 + scan * 0.22))), width=1)

    scan_start = lerp(points[0][0], points[1][0], scan), lerp(points[0][1], points[1][1], scan)
    scan_end = lerp(points[2][0], center[0], 1 - scan), lerp(points[2][1], center[1], 1 - scan)
    draw.line((scan_start[0], scan_start[1], scan_end[0], scan_end[1]), fill=rgba(BRIGHT_GOLD, int(alpha * 0.36)), width=1)


def draw_face_number(
    layer: Image.Image,
    points: list[tuple[float, float]],
    value: int,
    brightness: float,
    reveal: float,
    is_result: bool,
):
    edge = min(math.dist(points[0], points[1]), math.dist(points[1], points[2]), math.dist(points[2], points[0]))
    if edge < 34:
        return

    label_points = label_triangle(points)
    source_size = 192
    source = Image.new("RGBA", (source_size, source_size), (0, 0, 0, 0))
    texture = ImageDraw.Draw(source)
    number_font = FONT_FINAL_NUMBER if is_result and reveal > 0.65 and edge > 78 else FONT_NUMBER
    alpha = int((95 + brightness * 150) * (0.68 + reveal * 0.32))
    if is_result:
        alpha = int(lerp(alpha, 255, reveal))

    source_points = [(96, 20), (32, 156), (160, 156)]
    if signed_area(label_points) * signed_area(source_points) < 0:
        source_points = [source_points[0], source_points[2], source_points[1]]

    mask = Image.new("L", (source_size, source_size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.polygon(source_points, fill=255)
    text_center = triangle_center(source_points)
    centered(texture, (text_center[0] + 2.0, text_center[1] + 2.5), str(value), number_font, rgba((0, 0, 0), int(alpha * 0.82)))
    centered(texture, text_center, str(value), number_font, rgba(GREEN_FIRE if not is_result else BRIGHT_GOLD, 54 + brightness * 74))
    centered(texture, text_center, str(value), number_font, rgba((255, 234, 168), alpha))
    source.putalpha(Image.composite(source.getchannel("A"), Image.new("L", (source_size, source_size), 0), mask))

    transformed = source.transform(
        (SIZE, SIZE),
        Image.Transform.AFFINE,
        affine_from_dest_to_source(label_points, source_points),
        resample=Image.Resampling.BICUBIC,
    )
    layer.alpha_composite(transformed)


def affine_from_dest_to_source(
    dest: list[tuple[float, float]],
    source: list[tuple[float, float]],
) -> tuple[float, float, float, float, float, float]:
    (x0, y0), (x1, y1), (x2, y2) = dest
    (u0, v0), (u1, v1), (u2, v2) = source
    det = x0 * (y1 - y2) + x1 * (y2 - y0) + x2 * (y0 - y1)
    if abs(det) < 0.001:
        return (1, 0, 0, 0, 1, 0)
    a = (u0 * (y1 - y2) + u1 * (y2 - y0) + u2 * (y0 - y1)) / det
    b = (u0 * (x2 - x1) + u1 * (x0 - x2) + u2 * (x1 - x0)) / det
    c = (u0 * (x1 * y2 - x2 * y1) + u1 * (x2 * y0 - x0 * y2) + u2 * (x0 * y1 - x1 * y0)) / det
    d = (v0 * (y1 - y2) + v1 * (y2 - y0) + v2 * (y0 - y1)) / det
    e = (v0 * (x2 - x1) + v1 * (x0 - x2) + v2 * (x1 - x0)) / det
    f = (v0 * (x1 * y2 - x2 * y1) + v1 * (x2 * y0 - x0 * y2) + v2 * (x0 * y1 - x1 * y0)) / det
    return a, b, c, d, e, f


def draw_d20(layer: Image.Image, frame: int, result: int):
    draw = ImageDraw.Draw(layer)
    progress = frame / (FRAME_COUNT - 1)
    reveal = smoothstep((progress - 0.50) / 0.50)
    target_ax, target_ay, target_az = find_target_rotation(result)
    decay = (1 - reveal) ** 2.15
    roll_time = frame * 0.5
    ax = target_ax + decay * (3.8 + roll_time * 0.095 + math.sin(roll_time * 0.18) * 0.42)
    ay = target_ay + decay * (math.tau * 2.65 + roll_time * 0.145 + math.sin(roll_time * 0.12) * 0.34)
    az = target_az + decay * (2.4 + roll_time * 0.125 + math.cos(roll_time * 0.16) * 0.30)
    scale = 1.02 + reveal * 0.13 + math.sin(frame * 0.19) * 0.03
    offset = (
        math.sin(frame * 0.12) * decay * 38,
        math.cos(frame * 0.095) * decay * 26 + math.sin(frame * 0.21) * decay * 10,
    )

    rotated = [rotate(v, ax, ay, az) for v in VERTS]
    projected = [project((v[0] * scale, v[1] * scale, v[2] * scale), 164, offset) for v in rotated]
    face_data = []
    light = normalize((-0.35, -0.55, 1.0))
    for face in FACES:
        rv = [rotated[i] for i in face]
        normal = face_normal(rv)
        depth = sum(v[2] for v in rv) / 3
        brightness = max(0.0, normal[0] * light[0] + normal[1] * light[1] + normal[2] * light[2])
        face_data.append((depth, brightness, face, normal))

    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    visible_points = [projected[i] for _, _, face, normal in face_data if normal[2] > -0.2 for i in face]
    if visible_points:
        min_x, min_y = min(p[0] for p in visible_points), min(p[1] for p in visible_points)
        max_x, max_y = max(p[0] for p in visible_points), max(p[1] for p in visible_points)
        sd.ellipse((min_x + 16, max_y - 26, max_x + 24, max_y + 28), fill=(0, 0, 0, 130))
        layer.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(16)))

    for face_index, (_, brightness, face, normal) in sorted(enumerate(face_data), key=lambda item: item[1][0]):
        if normal[2] < -0.20:
            continue
        pts = [projected[i] for i in face]
        shade = 0.18 + brightness * 0.82
        fill = mix(BLACK_GLASS, DEEP_GOLD, min(0.35, shade * 0.28))
        outline = mix(DEEP_GOLD, BRIGHT_GOLD, min(1, brightness * 1.1))
        inner_glass = inset_triangle(pts, 0.88)
        draw.polygon(pts, fill=rgba((4, 4, 5), 255))
        draw.line(pts + [pts[0]], fill=rgba((2, 2, 2), 230), width=8)
        draw.polygon(inner_glass, fill=rgba(fill, 238), outline=rgba(outline, 205))
        draw.line(inner_glass + [inner_glass[0]], fill=rgba(BRIGHT_GOLD, int(55 + brightness * 145)), width=2)
        if brightness > 0.08:
            draw_face_runes(draw, inner_glass, frame + face_index * 5, int(42 + brightness * 120))
        draw_face_number(
            layer,
            inner_glass,
            FACE_NUMBERS[face_index],
            brightness,
            reveal,
            FACE_NUMBERS[face_index] == result and normal[2] > 0.58,
        )


def render_frame(frame: int, result: int) -> Image.Image:
    base = draw_background(frame)

    d20 = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw_d20(d20, frame, result)
    base.alpha_composite(d20)

    return base


def generate(output: Path, result: int):
    output.parent.mkdir(parents=True, exist_ok=True)
    frames = [render_frame(frame, result) for frame in range(FRAME_COUNT)]
    frames[0].save(
        output,
        save_all=True,
        append_images=frames[1:],
        duration=FRAME_DURATION_MS,
        optimize=True,
        disposal=2,
        transparency=0,
    )
    print(output.relative_to(ROOT))


def main():
    parser = argparse.ArgumentParser(description="Generate one black-gold arcane full-d20 roll demo GIF.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--result", type=int, default=6)
    args = parser.parse_args()
    if not 1 <= args.result <= 20:
        raise ValueError("--result must be between 1 and 20")
    generate(args.output, args.result)


if __name__ == "__main__":
    main()
