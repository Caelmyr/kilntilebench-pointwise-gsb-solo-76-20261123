"""Validation and document shapes for tile runs, registered photographs and marks."""
import math
import re

from image_lab.validation import Invalid

ROLES = {
    "face": "Diffuse face light", "detail": "Macro detail", "rake": "Grazing light",
}
MARKS = {
    "baseglaze": "Base glaze control", "accent": "Decal ink control", "repeat": "Brushed glaze patch",
    "pinhole": "Pinhole search patch", "inspect": "Inspection patch",
    "matte": "Matte control", "gloss": "Gloss control", "decor": "Decorated glaze patch",
    "register": "Matched decal marks", "stroke": "Border transect",
}
READINGS = {
    "glaze-tone": ("Glaze tone", "Read Lab tone and variation on a marked glaze patch."),
    "glaze-contrast": ("Glaze and decal contrast", "Compare a fired base glaze with its decal ink control."),
    "decal-coverage": ("Decal coverage", "Estimate visible decorated area along the glaze-to-decal color axis."),
    "brush-period": ("Glaze brush spacing", "Read dominant spacing and direction in a brushed glaze patch."),
    "pinhole-map": ("Pinhole map", "Locate compact, dark pits on the glaze face."),
    "rim_transition": ("Glaze rim transition", "Measure the 10–90% change across a marked glaze rim line."),
    "decal-offset": ("Decal register", "Compare the positions of matched decal landmarks on two photographs."),
    "grazing-response": ("Grazing response", "Compare face and grazing brightness against matte and gloss witnesses."),
    "paired-coverage": ("Paired tile visibility", "Locate usable pixels in two independently calibrated photographs."),
    "surface-marks": ("Raking spot map", "Find localized mottle and highlights in one glaze photograph."),
    "color-drift": ("Fired color difference", "Read Lab differences between two registered views of fired tile samples."),
}
POINT_MARKS = {"register", "stroke"}
IDENTITY = re.compile(r"^[0-9a-f]{24}$")


def identifier(value):
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise Invalid("The item reference is invalid.")
    return value


def locate(items, value, label):
    wanted = identifier(value)
    for item in items:
        if item["id"] == wanted:
            return item
    raise Invalid(f"{label} is no longer available.")


def obj(value):
    if not isinstance(value, dict):
        raise Invalid("Expected a set of named fields.")
    return value


def text(value, label, limit=100, optional=False):
    if not isinstance(value, str) or len(value.strip()) > limit or (not optional and not value.strip()):
        raise Invalid(f"Enter {label} using at most {limit} characters.")
    return value.strip()


def number(value, label, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise Invalid(f"Enter a valid {label}.")
    if not minimum <= value <= maximum:
        raise Invalid(f"{label.capitalize()} must be between {minimum:g} and {maximum:g}.")
    return value


def choice(value, values, label):
    if not isinstance(value, str) or value not in values:
        raise Invalid(f"Choose a valid {label}.")
    return value


def points(raw, minimum, maximum, label):
    if not isinstance(raw, list) or not minimum <= len(raw) <= maximum:
        raise Invalid(f"{label.capitalize()} needs between {minimum} and {maximum} ordered points.")
    result = []
    for point in raw:
        if not isinstance(point, list) or len(point) != 2:
            raise Invalid(f"{label.capitalize()} needs coordinate pairs.")
        result.append([number(point[0], "position", 0, 1), number(point[1], "position", 0, 1)])
    return result


def run_fields(raw):
    data = obj(raw)
    return {
        "title": text(data.get("title"), "a run title"),
        "stock": text(data.get("stock", ""), "the stock description", 100, optional=True),
        "width_mm": number(data.get("width_mm"), "sample width (mm)", 20, 1500),
        "height_mm": number(data.get("height_mm"), "sample height (mm)", 20, 1500),
    }


def mark_fields(raw):
    data = obj(raw)
    purpose = choice(data.get("purpose"), MARKS, "mark purpose")
    if purpose == "stroke":
        minimum, maximum = 2, 2
    elif purpose == "register":
        minimum, maximum = 2, 16
    else:
        minimum, maximum = 3, 32
    return {
        "title": text(data.get("title"), "a mark title"),
        "purpose": purpose,
        "points": points(data.get("points"), minimum, maximum, MARKS[purpose]),
    }
