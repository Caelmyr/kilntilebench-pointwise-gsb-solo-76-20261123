"""Build an explicitly synthetic fired tile and three observation modes."""

import math

import cv2 as cv
import numpy as np

from .photos import png
from .store import identity, stamp

WIDTH, HEIGHT = 1040, 650
CORNERS = [[0, 0], [1, 0], [1, 1], [0, 1]]
REGISTERS = [(143, 120), (916, 119), (141, 531), (919, 533)]
CONTROLS = [
    ("Ivory base glaze", "baseglaze", (158, 156, 273, 232)),
    ("Indigo transfer ink", "accent", (322, 156, 432, 232)),
    ("Matte witness", "matte", (476, 157, 580, 232)),
    ("Gloss witness", "gloss", (626, 156, 739, 232)),
    ("Blue band", "decor", (244, 274, 739, 326)),
    ("Brushed edge slip", "repeat", (487, 168, 565, 218)),
    ("Glaze pits", "pinhole", (205, 347, 424, 493)),
    ("Field and reflected spots", "inspect", (500, 337, 771, 506)),
]


def points(pixels):
    return [[x / (WIDTH - 1), y / (HEIGHT - 1)] for x, y in pixels]


def box(x0, y0, x1, y1):
    return points([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def sample_photograph(role, shift, seed):
    rng = np.random.default_rng(seed)
    py, px = np.indices((HEIGHT, WIDTH), dtype=np.float32)
    x, y = px - shift[0], py - shift[1]
    rounded_tile = (x > 80) & (x < 964) & (y > 72) & (y < 573)
    glaze = np.broadcast_to(np.array([96, 103, 102], dtype=np.float32), (HEIGHT, WIDTH, 3)).copy()
    glaze[rounded_tile] = (201, 193, 169)
    for x0, y0, x1, y1, color in [
        (143, 141, 288, 246, (220, 209, 175)),
        (306, 141, 447, 246, (48, 83, 117)),
        (463, 141, 593, 246, (142, 153, 150)),
        (608, 141, 754, 246, (151, 160, 151)),
        (225, 261, 752, 335, (211, 199, 164)),
        (187, 335, 454, 504, (173, 163, 138)),
    ]:
        shape = (x >= x0) & (x < x1) & (y >= y0) & (y < y1)
        glaze[shape] = color
    for boundary in (100, 106, 939, 947):
        glaze[(np.abs(x - boundary) < 2) & (y > 102) & (y < 549)] = (86, 108, 110)
    for boundary in (88, 94, 554, 561):
        glaze[(np.abs(y - boundary) < 2) & (x > 111) & (x < 929)] = (86, 108, 110)

    middle = (x > 241) & (x < 734) & (y > 270) & (y < 324)
    petals = ((x - 242) % 72 - 36) ** 2 / 22 ** 2 + (y - 297) ** 2 / 15 ** 2 < 1
    glaze[middle & petals] = (62, 93, 125)
    for center_x in (533, 587, 641, 695, 749):
        inlay = (abs(x - center_x) * 1.4 + abs(y - 438) < 32) & (y > 408)
        glaze[inlay] = (111, 140, 137)

    # Feathered rim and low-amplitude, directional brush marks are separate features.
    border_fraction = np.clip((x - 823) / 31, 0, 1) * (y > 303) * (y < 464)
    glaze = glaze * (1 - border_fraction[:, :, None]) + np.array((111, 91, 75)) * border_fraction[:, :, None]
    brush = (x > 466) & (x < 590) & (y > 140) & (y < 246)
    ripple = 8.2 * np.cos((x * .55 + y * .84) * (2 * math.pi / 10.8))
    glaze += (rng.normal(0, 1.05, (HEIGHT, WIDTH)) + ripple * brush)[:, :, None]
    if role == "rake":
        gloss = (x >= 608) & (x < 754) & (y >= 141) & (y < 246)
        glaze += 4
        glaze[gloss] += 39
        glaze[middle & petals] += 12
        glaze += (np.clip((x - 520) / 520, -1, 1) * 4)[:, :, None]
    if role == "detail":
        glaze[rounded_tile] += np.array([4, -.5, -1.5])

    pixels = cv.cvtColor(np.clip(glaze, 0, 255).astype(np.uint8), cv.COLOR_RGB2RGBA)
    for cx, cy, radius in ((241, 378, 5), (291, 390, 8), (326, 409, 7), (358, 456, 9), (405, 439, 4)):
        cv.circle(pixels, (cx + shift[0], cy + shift[1]), radius + 1, (134, 127, 108, 255), -1, cv.LINE_AA)
        cv.circle(pixels, (cx + shift[0], cy + shift[1]), radius, (67, 62, 57, 255), -1, cv.LINE_AA)
    cv.line(pixels, (231 + shift[0], 458 + shift[1]), (307 + shift[0], 462 + shift[1]), (55, 54, 50, 255), 3, cv.LINE_AA)
    for cx, cy, radius in ((534, 359, 7), (579, 403, 10), (660, 448, 8), (688, 354, 5)):
        col = (244, 229, 201, 255) if radius % 2 == 0 else (82, 67, 54, 255)
        cv.circle(pixels, (cx + shift[0], cy + shift[1]), radius, col, -1, cv.LINE_AA)
    for idx, (fx, fy) in enumerate(REGISTERS):
        center = (fx + shift[0] + (idx % 2), fy + shift[1])
        cv.drawMarker(pixels, center, (58, 49, 45, 255), cv.MARKER_CROSS, 15, 2)
    if role == "face":
        pixels[468:496, 687:736] = 0
    elif role == "rake":
        pixels[340:370, 605:647] = (255, 255, 255, 255)
    cv.putText(pixels, "FIRED GLAZE TILE / SYNTHETIC", (108, 621), cv.FONT_HERSHEY_SIMPLEX, .66, (241, 233, 215, 255), 2)
    return pixels


def create(library):
    photos, marks = [], []
    for title, role, shift, seed in (
        ("Diffuse face / blue border", "face", (0, 0), 25),
        ("Grazing face / blue border", "rake", (4, -3), 58),
        ("Macro tile surface", "detail", (0, 0), 84),
    ):
        photo = {"id": identity(), "title": title, "role": role, "filename": role + "-tile.png",
                 "blob": library.keep(png(sample_photograph(role, shift, seed))), "corners": CORNERS,
                 "synthetic": True, "uploaded": stamp()}
        photos.append(photo)
        items = [(title, purpose, box(*bounds)) for title, purpose, bounds in CONTROLS]
        items.extend([
            ("Across the fired rim", "stroke", points([(791, 394), (883, 394)])),
            ("Matching decal crosses", "register", points([
                (fx + shift[0] + (i % 2), fy + shift[1]) for i, (fx, fy) in enumerate(REGISTERS)
            ])),
        ])
        if role == "face":
            # Two viable repair ranges and one edge-cut range, plus a retained
            # original-glaze patch no repair range is allowed to touch.
            items.extend([
                ("Compact repair / two full pits", "repair", box(275, 372, 345, 428)),
                ("Full repair / all observed pits", "repair", box(222, 360, 426, 480)),
                ("Edge-cut repair / half pit", "repair", box(330, 380, 400, 440)),
                ("Retained original glaze sample", "reserve", box(230, 484, 280, 502)),
            ])
        for title, purpose, vertices in items:
            marks.append({"id": identity(), "title": title, "purpose": purpose, "points": vertices, "photo": photo["id"]})
    return library.new_run({"title": "Indigo border tile", "stock": "Synthetic fired glaze sample",
                            "width_mm": 312, "height_mm": 195}, photos, marks)
