"""Physical-plane measurements for registered ceramic photographs."""
import math

import cv2 as cv
import numpy as np

from image_lab.validation import Invalid

from .model import number
from .photos import coordinates, image, region


def tint(shown, mask, color, amount=.58):
    shown[mask] = shown[mask] * (1 - amount) + np.asarray(color) * amount


def selection(frame, mark, minimum=32):
    mask = region(frame, mark) & frame.useful
    if np.count_nonzero(mask) < minimum:
        raise Invalid("The marked patch needs more usable image pixels.")
    return mask


def rounded(values, digits=4):
    return np.round(values, digits).tolist()


def lab(pixels):
    # Convert standard RGB-encoded input to display-referred Lab.
    return cv.cvtColor(np.asarray(pixels, np.float32).reshape(-1, 1, 3), cv.COLOR_RGB2LAB).reshape(-1, 3)


def glaze_tone(frame, marks, _opts):
    mask = selection(frame, marks["region"])
    lab_pixels = lab(frame.rgb[mask])
    low, center, high = np.percentile(lab_pixels, [10, 50, 90], axis=0)
    rows = [
        {"channel": name, "p10": round(float(low[i]), 4), "median": round(float(center[i]), 4),
         "p90": round(float(high[i]), 4)}
        for i, name in enumerate(("L*", "a*", "b*"))
    ]
    shown = image(frame)
    tint(shown, mask, (.38, .78, .86), .28)
    return {"pixels": int(mask.sum()), "lab": rounded(center), "lightness_spread": round(float(high[0] - low[0]), 4)}, rows, shown


def glaze_contrast(frame, marks, _opts):
    masks = {key: selection(frame, marks[key]) for key in ("baseglaze", "accent")}
    values = {key: np.median(lab(frame.rgb[area]), axis=0) for key, area in masks.items()}
    lightness_drop = float(values["baseglaze"][0] - values["accent"][0])
    distance = float(np.linalg.norm(values["baseglaze"] - values["accent"]))
    shown = image(frame)
    tint(shown, masks["baseglaze"], (.84, .75, .32))
    tint(shown, masks["accent"], (.39, .68, .96))
    rows = [{"mark": marks[key]["title"], "lab": rounded(value), "pixels": int(masks[key].sum())}
            for key, value in values.items()]
    return {"lightness_drop": round(lightness_drop, 4), "delta_e76": round(distance, 4)}, rows, shown


def decal_projection(frame, baseglaze, accent):
    blank_rgb = np.median(frame.rgb[selection(frame, baseglaze)], axis=0)
    accent_rgb = np.median(frame.rgb[selection(frame, accent)], axis=0)
    difference = accent_rgb - blank_rgb
    leverage = float(difference @ difference)
    if leverage < .014:
        raise Invalid("Choose two glaze controls with stronger color separation.")
    return (frame.rgb - blank_rgb) @ difference / leverage


def decal_coverage(frame, marks, opts):
    area = selection(frame, marks["region"])
    fraction = decal_projection(frame, marks["baseglaze"], marks["accent"])
    threshold = number(opts.get("threshold", .5), "decal likeness threshold", .1, .9)
    decorated = area & (fraction >= threshold)
    border = area & (np.abs(fraction - threshold) < .08)
    shown = image(frame)
    tint(shown, area & ~decorated, (.36, .71, .95), .2)
    tint(shown, decorated, (.27, .79, .51))
    tint(shown, border, (.94, .67, .29), .34)
    rows = [
        {"class": key, "pixels": int(mask.sum())}
        for key, mask in (("Decal-like", decorated), ("Base-like", area & ~decorated), ("Near threshold", border))
    ]
    return {"decal_like_area_pct": round(100 * decorated.sum() / area.sum(), 3),
            "threshold": threshold, "pixels": int(area.sum())}, rows, shown


def brush_period(frame, marks, _opts):
    mask = selection(frame, marks["region"], 250)
    ys, xs = np.nonzero(mask)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    if min(y1 - y0, x1 - x0) < 24:
        raise Invalid("Use at least 24 calibrated pixels along both repeat patch axes.")
    luma = frame.luma[y0:y1, x0:x1]
    present = mask[y0:y1, x0:x1]
    window = np.outer(np.hanning(luma.shape[0]), np.hanning(luma.shape[1]))
    centered = np.where(present, luma - np.mean(luma[present]), 0)
    power = np.abs(np.fft.fftshift(np.fft.fft2(centered * window))) ** 2
    freq_x = np.fft.fftshift(np.fft.fftfreq(power.shape[1], d=frame.step[0]))
    freq_y = np.fft.fftshift(np.fft.fftfreq(power.shape[0], d=frame.step[1]))
    xx, yy = np.meshgrid(freq_x, freq_y)
    possible = ((yy > 0) | ((yy == 0) & (xx > 0))) & (np.hypot(xx, yy) > .18)
    rows = []
    candidate = np.where(possible, power, 0)
    peak = float(candidate.max())
    if peak <= 1e-6:
        raise Invalid("This patch does not contain enough periodic tonal variation.")
    for i in range(1, 5):
        iy, ix = np.unravel_index(int(np.argmax(candidate)), candidate.shape)
        strength = float(candidate[iy, ix]) / peak
        if strength < .075:
            break
        rows.append({"peak": i, "spacing_mm": round(float(1 / np.hypot(xx[iy, ix], yy[iy, ix])), 4),
                     "brush_direction_deg": round((math.degrees(math.atan2(yy[iy, ix], xx[iy, ix])) + 90) % 180, 2),
                     "relative_power": round(strength, 4)})
        candidate[max(0, iy - 3):iy + 4, max(0, ix - 3):ix + 4] = 0
    shown = image(frame)
    tint(shown, mask, (.52, .79, .91), .22)
    return {"dominant_spacing_mm": rows[0]["spacing_mm"], "dominant_brush_deg": rows[0]["brush_direction_deg"],
            "peaks": len(rows)}, rows, shown


def pinhole_map(frame, marks, opts):
    """Measure visible, compact dark pits and keep broad shaded marks separate."""
    area = selection(frame, marks["region"])
    cutoff = number(opts.get("luma", .31), "dark pit threshold", .05, .95)
    candidates = area & (frame.luma <= cutoff)
    count, labels, stats, centroids = cv.connectedComponentsWithStats(candidates.astype(np.uint8), connectivity=8)
    pixel_area = frame.step[0] * frame.step[1]
    rows = []
    pits = np.zeros(area.shape, dtype=bool)
    for label in range(1, count):
        x, y, width, height, pixels = stats[label]
        area_mm2 = float(pixels * pixel_area)
        horizontal, vertical = width * frame.step[0], height * frame.step[1]
        if not .10 <= area_mm2 <= 80 or max(horizontal, vertical) / min(horizontal, vertical) > 2.6:
            continue
        local = labels[y:y + height, x:x + width] == label
        pits[y:y + height, x:x + width] |= local
        rows.append({"area_mm2": round(area_mm2, 4), "center_mm": rounded(centroids[label] * frame.step),
                     "width_mm": round(horizontal, 4), "height_mm": round(vertical, 4)})
    rows.sort(key=lambda item: item["area_mm2"], reverse=True)
    shown = image(frame)
    tint(shown, area & ~candidates, (.25, .78, .73), .22)
    tint(shown, candidates & ~pits, (.65, .36, .83), .44)
    tint(shown, pits, (.95, .48, .24))
    return {"pit_count": len(rows), "pit_area_mm2": round(float(pits.sum() * pixel_area), 4),
            "observed_mm2": round(float(area.sum() * pixel_area), 4)}, rows[:40], shown


def rim_transition(frame, marks, opts):
    knots = coordinates(marks["line"]["points"], frame.rgb.shape)
    length = float(np.linalg.norm((knots[1] - knots[0]) * frame.step))
    if length < 1:
        raise Invalid("Use a transect at least 1 mm long.")
    count = min(220, max(24, int(length * 5)))
    locations = np.linspace(knots[0], knots[1], count, dtype=np.float32)
    xs, ys = locations.T
    luma = cv.remap(frame.luma, xs.reshape(1, -1), ys.reshape(1, -1), cv.INTER_LINEAR)[0]
    valid = cv.remap(frame.useful.astype(np.float32), xs.reshape(1, -1), ys.reshape(1, -1), cv.INTER_LINEAR)[0] >= .999
    if valid.sum() < count * .85:
        raise Invalid("More of the edge transect must be visible in the photograph.")
    strip = max(3, count // 7)
    lead, tail = float(np.median(luma[:strip])), float(np.median(luma[-strip:]))
    if abs(tail - lead) < .07:
        raise Invalid("The two ends should fall on visibly different glaze tones.")
    relative = np.convolve(np.pad((luma - lead) / (tail - lead), 2, mode="edge"), np.full(5, .2), mode="valid")
    position = np.linspace(0, length, count)
    limits = [np.flatnonzero(valid & (relative >= level)) for level in (.1, .9)]
    if any(len(indices) == 0 for indices in limits):
        raise Invalid("The transect does not bracket the glaze rim transition.")
    width = float(position[limits[1][0]] - position[limits[0][0]])
    shown = image(frame)
    cv.line(shown, tuple(np.rint(knots[0]).astype(int)), tuple(np.rint(knots[1]).astype(int)), (.96, .52, .27), 2)
    rows = [{"position_mm": round(float(x), 3), "luma": round(float(y), 4) if on else None}
            for x, y, on in zip(position, luma, valid)]
    return {"edge_width_mm": round(max(0, width), 4), "length_mm": round(length, 4), "usable_samples": int(valid.sum())}, rows, shown


def decal_offsets(frame, other, marks, _opts):
    fixed = coordinates(marks["reference"]["points"], frame.rgb.shape)
    moved = coordinates(marks["comparison"]["points"], other.rgb.shape)
    if fixed.shape != moved.shape:
        raise Invalid("Both decal marks need the same number of centres in corresponding order.")
    difference = (fixed - moved) * frame.step
    shift = np.mean(difference, axis=0)
    offset = difference - shift
    shown = image(frame)
    rows = []
    for i, (fxy, mxy, dxy, residual) in enumerate(zip(fixed, moved, difference, offset), 1):
        start, end = tuple(np.rint(fxy).astype(int)), tuple(np.rint(mxy).astype(int))
        cv.drawMarker(shown, start, (.32, .89, .63), cv.MARKER_CROSS, 16, 2)
        cv.drawMarker(shown, end, (.98, .56, .31), cv.MARKER_TILTED_CROSS, 16, 2)
        cv.line(shown, start, end, (.76, .71, .34), 1)
        rows.append({"point": i, "reference_mm": rounded(fxy * frame.step), "comparison_mm": rounded(mxy * frame.step),
                     "displacement_mm": rounded(dxy), "residual_mm": round(float(np.linalg.norm(residual)), 4)})
    rms = np.sqrt(np.sum(offset ** 2) / len(offset))
    return {"suggested_shift_mm": rounded(shift), "rms_after_shift_mm": round(float(rms), 4)}, rows, shown


def grazing_response(frame, other, marks, _opts):
    """Use two illumination witnesses to normalize an observed raking response."""
    usable = frame.useful & other.useful
    masks = {name: region(frame, marks[name]) & usable for name in ("matte", "gloss", "decor")}
    if any(mask.sum() < 32 for mask in masks.values()):
        raise Invalid("All three marked patches need at least 32 pixels visible in both photographs.")
    delta = other.luma - frame.luma
    values = {name: float(np.median(delta[mask])) for name, mask in masks.items()}
    denominator = values["gloss"] - values["matte"]
    if abs(denominator) < .025:
        raise Invalid("Choose matte and gloss controls with distinct responses to grazing light.")
    shown = image(frame)
    colors = {"matte": (.91, .49, .39), "gloss": (.40, .75, .96), "decor": (.87, .66, .97)}
    rows = []
    for name, mask in masks.items():
        tint(shown, mask, colors[name], .44)
        rows.append({"patch": marks[name]["title"], "face_luma": round(float(np.median(frame.luma[mask])), 4),
                     "grazing_luma": round(float(np.median(other.luma[mask])), 4),
                     "difference": round(values[name], 4), "paired_pixels": int(mask.sum())})
    response = (values["decor"] - values["matte"]) / denominator
    return {"relative_grazing_response": round(response, 4), "control_separation": round(denominator, 4)}, rows, shown


def _patch_difference(frame, other, mark, usable):
    mask = region(frame, mark) & usable
    if mask.sum() < 32:
        raise Invalid(f"The “{mark['title']}” patch needs at least 32 pixels visible in both photographs.")
    return mask, float(np.median((other.luma - frame.luma)[mask]))


def gloss_scale(frame, other, marks, opts):
    """Read one check patch through two independent matte/gloss witness pairs."""
    usable = frame.useful & other.useful
    expected = number(opts.get("expected", .22), "expected check reading", -10, 10)
    low = number(opts.get("low", .1), "check interval low", -10, 10)
    high = number(opts.get("high", .35), "check interval high", -10, 10)
    if low > high:
        raise Invalid("The check interval needs a low value no greater than its high value.")
    if not low <= expected <= high:
        raise Invalid("The expected check reading must fall inside the check interval.")
    rule = opts.get("rule", "separation")
    if rule not in ("separation", "closeness"):
        raise Invalid("Choose a valid selection rule.")
    tie_fraction = number(opts.get("tie_tolerance", .1), "near-tie fraction", 0, 1)
    tie_margin = tie_fraction * (high - low)
    interval = f"{low:g}–{high:g}"
    colors = {
        "A": ((.91, .49, .39), (.40, .75, .96), (.94, .78, .34)),
        "B": ((.73, .51, .91), (.27, .85, .59), (.55, .72, .96)),
    }
    shown = image(frame)
    rows, scales = [], []
    for key, label in (("a", "A"), ("b", "B")):
        differences, titles = {}, {}
        for slot in ("matte", "gloss", "check"):
            mark = marks[f"{slot}_{key}"]
            mask, value = _patch_difference(frame, other, mark, usable)
            differences[slot] = value
            titles[slot] = mark["title"]
            tint(shown, mask, colors[label][("matte", "gloss", "check").index(slot)], .42)
        separation = differences["gloss"] - differences["matte"]
        if abs(separation) < .025:
            raise Invalid(f"Scale {label} needs matte and gloss witnesses with responses at least 0.025 apart.")
        reading = (differences["check"] - differences["matte"]) / separation
        rows.append({
            "scale": label, "matte_witness": titles["matte"], "gloss_witness": titles["gloss"],
            "check_patch": titles["check"], "matte_difference": round(differences["matte"], 4),
            "gloss_difference": round(differences["gloss"], 4), "separation": round(separation, 4),
            "check_difference": round(differences["check"], 4), "relative_response": round(reading, 4),
            "expected": expected, "interval": interval, "deviation": round(abs(reading - expected), 4),
            "inside_interval": bool(low <= reading <= high), "selected": False, "note": "",
        })
        scales.append({"label": label, "reading": reading, "separation": separation, "row": rows[-1]})

    passing = [scale for scale in scales if scale["row"]["inside_interval"]]
    if not passing:
        readings = ", ".join(f"{scale['label']}={scale['reading']:.4f}" for scale in scales)
        raise Invalid(f"Neither scale reads its check patch inside {interval}: {readings}. Adjust the marks or interval.")
    if len(passing) == 1:
        chosen = passing[0]
        note = "Only one scale read its check patch inside the entered interval."
    elif rule == "separation":
        chosen = max(passing, key=lambda scale: abs(scale["separation"]))
        note = "Both scales read inside the interval; chose the wider matte–gloss separation."
    else:
        ordered = sorted(passing, key=lambda scale: abs(scale["reading"] - expected))
        closest, runner_up = ordered[0], ordered[1]
        if abs(closest["row"]["deviation"] - runner_up["row"]["deviation"]) <= tie_margin:
            chosen = max(passing, key=lambda scale: abs(scale["separation"]))
            note = ("Both scales read inside the interval and were near-tied on the expected value; "
                    "broke the tie with the wider matte–gloss separation.")
        else:
            chosen = closest
            note = "Both scales read inside the interval; chose the reading closest to the expected value."
    for scale in scales:
        row = scale["row"]
        row["selected"] = scale is chosen
        if scale is chosen:
            row["note"] = "Selected: " + note
        elif not row["inside_interval"]:
            row["note"] = "Outside the entered check interval."
        else:
            row["note"] = "Inside the interval but not selected."
    summary = {
        "chosen_scale": chosen["label"], "chosen_relative_response": round(chosen["reading"], 4),
        "expected": expected, "interval_low": low, "interval_high": high,
        "chosen_separation": round(chosen["separation"], 4), "scales_inside": len(passing),
        "selection_rule": rule, "selection_note": note,
    }
    return summary, rows, shown


def _bright_components(frame, mask, residual, threshold, minimum_mm2, aspect_limit=2.6, fill_limit=.22):
    """Yield compact, solid bright components with their peak residual and area."""
    pixel_area = frame.step[0] * frame.step[1]
    candidates = mask & (residual >= threshold)
    candidates = cv.morphologyEx(candidates.astype(np.uint8), cv.MORPH_OPEN, np.ones((3, 3), np.uint8)).astype(bool)
    count, labels, stats, _ = cv.connectedComponentsWithStats(candidates.astype(np.uint8), connectivity=8)
    for label in range(1, count):
        width, height = stats[label, cv.CC_STAT_WIDTH], stats[label, cv.CC_STAT_HEIGHT]
        area = float(stats[label, cv.CC_STAT_AREA])
        if max(width, height) / min(width, height) > aspect_limit or area / (width * height) < fill_limit:
            continue
        area_mm2 = area * pixel_area
        if area_mm2 < minimum_mm2:
            continue
        component = labels == label
        yield component, area_mm2, float(residual[component].max())


def _evidence_gaps(frame, mask, minimum_mm2):
    """Yield contiguous one-photograph-only regions inside the window."""
    pixel_area = frame.step[0] * frame.step[1]
    count, labels, stats, centroids = cv.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    for label in range(1, count):
        area_mm2 = float(stats[label, cv.CC_STAT_AREA] * pixel_area)
        if area_mm2 < minimum_mm2:
            continue
        yield labels == label, area_mm2, centroids[label]


def raking_spots(raking, diffuse, marks, opts, scale):
    """Find anomalous grazing highlights in a window through a previously adopted gloss scale."""
    window = region(raking, marks["region"])
    if window.sum() < 64:
        raise Invalid("Mark a larger inspection window.")
    minimum = number(opts.get("minimum_mm2", .15), "minimum spot area", .03, 40)
    scale_span = number(opts.get("scale_contrast", .5), "spot contrast in scale units", .05, 20)
    separation = float(scale["witnesses"]["separation"])
    if abs(separation) < .025:
        raise Invalid("The adopted gloss scale lacks a usable matte-gloss separation.")
    threshold = scale_span * abs(separation)
    both = window & raking.useful & diffuse.useful
    raking_only = window & raking.useful & ~diffuse.useful
    diffuse_only = window & diffuse.useful & ~raking.useful
    if both.sum() < 64:
        raise Invalid("The window needs at least 64 pixels clear in both photographs.")

    # Excess of the grazing view over the diffuse view, normalized by the frozen matte-gloss span.
    render = np.where(both, raking.luma - diffuse.luma, 0.0).astype(np.float32)
    baseline = cv.GaussianBlur(render, (0, 0), 8)
    residual = (render - baseline) / abs(separation)
    shown = image(raking)
    tint(shown, window, (.42, .68, .80), .10)
    rows = []
    for component, area_mm2, peak in _bright_components(raking, both, residual, scale_span, minimum):
        tint(shown, component, (.95, .48, .24), .6)
        ys, xs = np.nonzero(component)
        rows.append({
            "status": "Measured", "direction": "Bright", "size_mm2": round(area_mm2, 4),
            "center_mm": rounded([float(np.mean(xs) * raking.step[0]), float(np.mean(ys) * raking.step[1])]),
            "paired_pixels": int(component.sum()), "clear_photo": "Both photographs",
            "peak_scale_excess": round(peak, 4), "peak_contrast": round(peak * abs(separation), 4), "note": "",
        })
    measured = len(rows)
    gaps = 0
    for mask, clear in ((diffuse_only, "Diffuse photograph only"), (raking_only, "Grazing photograph only")):
        for component, area_mm2, center in _evidence_gaps(raking, mask, minimum):
            tint(shown, component, (.62, .40, .82), .55)
            gaps += 1
            rows.append({
                "status": "Missing evidence", "direction": "Unobserved", "size_mm2": round(area_mm2, 4),
                "center_mm": rounded([float(center[0] * raking.step[0]), float(center[1] * raking.step[1])]),
                "paired_pixels": 0, "clear_photo": clear, "peak_scale_excess": None, "peak_contrast": None,
                "note": "Only one photograph is clear here, so the adopted references cannot confirm a spot.",
            })
    rows.sort(key=lambda row: (row["status"] != "Measured", -row["size_mm2"]))
    pixel_area = raking.step[0] * raking.step[1]
    missing_area = float((raking_only | diffuse_only).sum() * pixel_area)
    summary = {
        "scale": scale.get("title", "Adopted gloss scale"), "scale_id": scale.get("id"),
        "scale_label": scale.get("label"),
        "bright_spots": measured, "missing_evidence": gaps,
        "missing_evidence_mm2": round(missing_area, 4),
        "window_mm2": round(float(window.sum() * pixel_area), 4),
        "threshold_scale_units": scale_span, "threshold_luma": round(threshold, 4),
        "reference_separation": round(separation, 4),
        "evidence_status": "complete" if gaps == 0 and missing_area < pixel_area else "incomplete",
    }
    return summary, rows[:50], shown


def paired_coverage(frame, other, marks, _opts):
    domain = region(frame, marks["region"])
    valid_a = domain & frame.useful
    valid_b = domain & other.useful
    both = valid_a & valid_b
    groups = [
        ("Both photos", both, (.27, .85, .59)),
        ("Reference only", valid_a & ~valid_b, (.38, .67, .99)),
        ("Comparison only", valid_b & ~valid_a, (.73, .51, .91)),
        ("Neither photo", domain & ~(valid_a | valid_b), (.94, .48, .23)),
    ]
    if not np.any(domain):
        raise Invalid("Mark a larger comparison area.")
    shown = image(frame)
    total = int(domain.sum())
    rows = []
    for label, mask, color in groups:
        tint(shown, mask, color)
        rows.append({"observation": label, "pixels": int(mask.sum()), "percent": round(100 * mask.sum() / total, 3)})
    return {"paired_percent": rows[0]["percent"], "marked_pixels": total}, rows, shown


def surface_marks(frame, marks, opts):
    area = selection(frame, marks["region"])
    contrast = number(opts.get("contrast", .075), "contrast", .02, .35)
    minimum = number(opts.get("minimum_mm2", .15), "minimum spot area", .03, 40)
    luma = frame.luma
    filled = np.where(frame.useful, luma, float(np.median(luma[area])))
    reference = cv.GaussianBlur(filled.astype(np.float32), (0, 0), 8)
    residual = filled - reference
    accumulated = np.zeros(area.shape, dtype=bool)
    shown = image(frame)
    rows = []
    for name, detected, color in (
        ("Dark", area & (residual < -contrast), (.93, .45, .27)),
        ("Light", area & (residual > contrast), (.34, .72, .95)),
    ):
        count, component, stats, centroids = cv.connectedComponentsWithStats(detected.astype(np.uint8), connectivity=8)
        for label in range(1, count):
            size = stats[label, cv.CC_STAT_AREA] * frame.step[0] * frame.step[1]
            if size < minimum:
                continue
            selected = component == label
            accumulated |= selected
            tint(shown, selected, color)
            rows.append({"direction": name, "size_mm2": round(float(size), 4),
                         "center_mm": rounded(centroids[label] * frame.step),
                         "peak_contrast": round(float(np.abs(residual[selected]).max()), 4)})
    rows.sort(key=lambda row: row["size_mm2"], reverse=True)
    return {"spots": len(rows), "spot_fraction_pct": round(100 * accumulated.sum() / area.sum(), 3)}, rows[:50], shown


def color_drift(frame, other, marks, opts):
    domain = region(frame, marks["region"])
    area = domain & frame.useful & other.useful
    if area.sum() < 32:
        raise Invalid("Both photographs need usable pixels in this patch.")
    tolerance = number(opts.get("delta_e", 5), "color difference threshold", .5, 40)
    first = lab(frame.rgb[area])
    second = lab(other.rgb[area])
    delta = np.linalg.norm(first - second, axis=1)
    above = np.zeros(area.shape, dtype=bool)
    above[area] = delta > tolerance
    shown = image(frame)
    tint(shown, area & ~above, (.23, .81, .67), .32)
    tint(shown, above, (.96, .51, .35))
    rows = [
        {"statistic": "Median", "delta_e76": round(float(np.median(delta)), 4)},
        {"statistic": "90th percentile", "delta_e76": round(float(np.percentile(delta, 90)), 4)},
    ]
    return {"median_delta_e76": rows[0]["delta_e76"], "above_tolerance_pct": round(100 * above.sum() / area.sum(), 3),
            "paired_pixels": int(area.sum())}, rows, shown
