"""Freeze an adopted gloss scale together with the two photographs that produced it."""
from image_lab.validation import Invalid

from .model import identifier
from .store import identity, stamp

CONSTITUENTS = ("matte", "gloss", "check")
PHOTO_FIELDS = ("id", "title", "name", "role", "filename", "blob", "corners", "synthetic")
MARK_FIELDS = ("id", "title", "purpose", "points", "photo")


def frozen_photo(photo):
    frozen = {key: photo.get(key) for key in PHOTO_FIELDS}
    frozen["sha256"] = photo.get("blob")
    return frozen


def frozen_mark(mark):
    return {key: mark.get(key) for key in MARK_FIELDS}


def active(run):
    """Return the currently adopted scale, falling back to the last kept snapshot."""
    scales = run.get("scales", [])
    if not scales:
        return None
    selected = run.get("adopted_scale")
    return next((scale for scale in scales if scale["id"] == selected), scales[-1])


def snapshot(run, reading, title):
    """Describe the selected scale in a reading as an immutable, self-contained reference."""
    if reading.get("mode") != "gloss-scale":
        raise Invalid("Only a gloss scale check can be adopted as a reference.")
    summary = reading.get("summary") or {}
    label = summary.get("chosen_scale")
    row = next((item for item in reading.get("rows", []) if item.get("scale") == label), None)
    if row is None or not row.get("selected"):
        raise Invalid("The reading does not contain a selected gloss scale.")
    if not row.get("inside_interval"):
        raise Invalid("The selected scale did not keep its check patch inside the entered interval.")
    if len(reading.get("photos", [])) != 2:
        raise Invalid("The reading needs both the diffuse and grazing photographs.")
    marks = reading.get("marks") or {}
    missing = [f"{slot}_{label.lower()}" for slot in CONSTITUENTS
               if f"{slot}_{label.lower()}" not in marks]
    if missing:
        raise Invalid("The reading lacks the marks for the selected scale.")
    diffuse, raking = reading["photos"]
    if diffuse["id"] == raking["id"]:
        raise Invalid("The reading needs two different photographs.")
    roles = {diffuse["role"], raking["role"]}
    if roles != {"face", "rake"}:
        raise Invalid("A gloss scale needs one diffuse-face and one grazing-light photograph.")
    if raking["role"] == "face":
        diffuse, raking = raking, diffuse
    chosen_marks = {slot: frozen_mark(marks[f"{slot}_{label.lower()}"]) for slot in CONSTITUENTS}
    stamp_now = stamp()
    return {
        "id": identity(),
        "title": title or f"Adopted gloss scale {label}",
        "label": label,
        "created": stamp_now,
        "reading_id": reading["id"],
        "reading_recorded": reading.get("recorded"),
        "source_revision": reading.get("source_revision"),
        "photos": {"diffuse": frozen_photo(diffuse), "raking": frozen_photo(raking)},
        "photo_ids": {"diffuse": diffuse["id"], "raking": raking["id"]},
        "marks": chosen_marks,
        "witnesses": {
            "matte_difference": row["matte_difference"],
            "gloss_difference": row["gloss_difference"],
            "separation": row["separation"],
            "check_difference": row["check_difference"],
            "relative_response": row["relative_response"],
        },
        "check": {
            "expected": row["expected"],
            "interval_low": summary.get("interval_low"),
            "interval_high": summary.get("interval_high"),
            "interval": row["interval"],
            "deviation": row["deviation"],
        },
        "selection_rule": summary.get("selection_rule"),
        "scales_inside": summary.get("scales_inside"),
    }


def require(run, value):
    """Look up an adopted scale by id, refusing ids that were never frozen into this run."""
    wanted = identifier(value)
    scale = next((item for item in run.get("scales", []) if item["id"] == wanted), None)
    if scale is None:
        raise Invalid("That gloss scale is not adopted in this sample run.")
    return scale
