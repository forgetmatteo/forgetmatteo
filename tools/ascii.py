"""Turn a portrait into the ASCII art shown on the left of the profile card.

Run once locally (needs Pillow and numpy), then commit the resulting ascii.txt:

    python3 tools/ascii.py avatar.jpg ascii.txt [path/to/monospace.ttf]

Each character cell of the photo is compared with every printable character drawn in a
monospace font, and the closest shape wins. Edges get slashes, bars and underscores in the
right direction instead of a plain brightness ramp, which keeps the face recognisable.
"""

import string
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

# The art is drawn smaller than the text (11px on 14px lines in build.py) for more detail.
ROWS = 42
COLS = 58
FONT_SIZE = 11
CELL_W, CELL_H = 7, 14
# Crop box in source pixels (left, top, right, bottom). Its height should be about
# (ROWS * CELL_H) / (COLS * CELL_W) times its width, or the face gets stretched.
CROP = (95, 30, 365, 443)
# Darkness curve: above 1 keeps skin light so hair, eyes and glasses stand out.
GAMMA = 2.6
# How much outlines count compared with tone.
EDGE_WEIGHT = 1.6
# How dark the darkest area gets, relative to the densest character.
INK_SCALE = 1.15
# How strongly a character must match the cell's darkness, compared with its shape.
DENSITY_WEIGHT = 12
# Cells lighter than this share of the densest character stay blank, which clears the backdrop.
BLANK_BELOW = 0.25
CHARSET = " " + "".join(c for c in string.punctuation + string.ascii_letters + string.digits)
DEFAULT_FONT = "/System/Library/Fonts/Menlo.ttc"


def glyphs(font_path):
    font = ImageFont.truetype(font_path, FONT_SIZE)
    shapes = []
    for char in CHARSET:
        cell = Image.new("L", (CELL_W, CELL_H), 0)
        ImageDraw.Draw(cell).text((0, 1), char, fill=255, font=font)
        shapes.append(np.asarray(cell.filter(ImageFilter.GaussianBlur(0.8)), dtype=np.float32) / 255)
    return np.stack(shapes)


def ink(src):
    photo = ImageOps.autocontrast(Image.open(src).convert("L"), cutoff=1)
    tone = ImageOps.invert(photo).point(lambda v: int(255 * (v / 255) ** GAMMA))
    # Outlines are found on the whole photo before cropping, so the crop border is not an edge.
    edges = photo.filter(ImageFilter.GaussianBlur(1.2)).filter(ImageFilter.FIND_EDGES)
    edges = edges.point(lambda v: min(255, int(v * EDGE_WEIGHT * 4)))
    combined = Image.fromarray(np.maximum(np.asarray(tone), np.asarray(edges)))
    combined = combined.crop(CROP).resize((COLS * CELL_W, ROWS * CELL_H), Image.LANCZOS)
    return np.asarray(combined.filter(ImageFilter.GaussianBlur(0.8)), dtype=np.float32) / 255


def convert(src, font_path):
    shapes = glyphs(font_path)
    flat = shapes.reshape(len(CHARSET), -1)
    # Scale the photo so its darkest areas match the densest glyph, not a solid block.
    density = flat.mean(axis=1)
    target = ink(src) * density.max() * INK_SCALE
    lines = []
    for y in range(ROWS):
        row = ""
        for x in range(COLS):
            cell = target[y * CELL_H:(y + 1) * CELL_H, x * CELL_W:(x + 1) * CELL_W].reshape(-1)
            if cell.mean() < BLANK_BELOW * density.max():
                row += " "
                continue
            # Shape picks the direction of an edge, density keeps the overall shading right.
            cost = ((flat - cell) ** 2).mean(axis=1) + DENSITY_WEIGHT * (density - cell.mean()) ** 2
            row += CHARSET[int(cost.argmin())]
        lines.append(row.rstrip())
    while lines and not lines[-1]:
        lines.pop()
    return lines


if __name__ == "__main__":
    source, target = sys.argv[1], sys.argv[2]
    font_path = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_FONT
    with open(target, "w", encoding="utf-8") as out:
        out.write("\n".join(convert(source, font_path)) + "\n")
