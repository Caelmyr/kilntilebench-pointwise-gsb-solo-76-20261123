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


def compact_pits(frame, area, cutoff):
    """Detect compact dark pits inside an observed area; broad shaded marks stay separate."""
    candidates = area & (frame.luma <= cutoff)
    count, labels, stats, centroids = cv.connectedComponentsWithStats(candidates.astype(np.uint8), connectivity=8)
    pixel_area = frame.step[0] * frame.step[1]
    pits = np.zeros(area.shape, dtype=bool)
    rows = []
    for label in range(1, count):
        x, y, width, height, pixels = stats[label]
        area_mm2 = float(pixels * pixel_area)
        horizontal, vertical = width * frame.step[0], height * frame.step[1]
        if not .10 <= area_mm2 <= 80 or max(horizontal, vertical) / min(horizontal, vertical) > 2.6:
            continue
        local = labels[y:y + height, x:x + width] == label
        pits[y:y + height, x:x + width] |= local
        rows.append({
            "label": label, "x": int(x), "y": int(y), "width": int(width), "height": int(height),
            "area_mm2": round(area_mm2, 4), "center_mm": rounded(centroids[label] * frame.step),
            "width_mm": round(float(horizontal), 4), "height_mm": round(float(vertical), 4),
        })
    rows.sort(key=lambda item: item["area_mm2"], reverse=True)
    return candidates, labels, pits, rows


def fully_observed(pit_row, labels, observed):
    """A pit counts only when every detected pixel is observed and unobscured.

    Pits cut by the inspection patch boundary, a missing-coverage area, or any
    unphotographed position fail this check and cannot be counted as full pits.
    """
    return bool(np.all(observed[labels == pit_row["label"]]))


def pit_pixels(pit_row, labels):
    return labels == pit_row["label"]


def pit_touches(pit_row, labels, footprint):
    return bool(np.any(footprint & pit_pixels(pit_row, labels)))


def pit_inside(pit_row, labels, footprint):
    pixels = pit_pixels(pit_row, labels)
    return bool(np.all(footprint[pixels]))


def pinhole_map(frame, marks, opts):
    """Measure visible, compact dark pits and keep broad shaded marks separate."""
    area = selection(frame, marks["region"])
    cutoff = number(opts.get("luma", .31), "dark pit threshold", .05, .95)
    candidates, _labels, pits, rows = compact_pits(frame, area, cutoff)
    pixel_area = frame.step[0] * frame.step[1]
    shown = image(frame)
    tint(shown, area & ~candidates, (.25, .78, .73), .22)
    tint(shown, candidates & ~pits, (.65, .36, .83), .44)
    tint(shown, pits, (.95, .48, .24))
    public_rows = [{key: value for key, value in row.items() if key not in ("label", "x", "y", "width", "height")}
                   for row in rows]
    return {"pit_count": len(rows), "pit_area_mm2": round(float(pits.sum() * pixel_area), 4),
            "observed_mm2": round(float(area.sum() * pixel_area), 4)}, public_rows[:40], shown


def repair_selection(frame, marks, opts):
    """Pick exactly one glaze repair range from the marked candidates."""
    minimum = number(opts.get("minimum_pits", 2), "minimum full observed pits", 1, 40)
    cutoff = number(opts.get("luma", .31), "dark pit threshold", .05, .95)
    inspection = selection(frame, marks["inspection"])
    reserve = None
    if "reserve" in marks and marks["reserve"] is not None:
        reserve = region(frame, marks["reserve"])
    candidates, labels, _pits, pit_rows = compact_pits(frame, inspection, cutoff)
    observed = inspection & frame.useful
    pixel_area = frame.step[0] * frame.step[1]
    height, width = inspection.shape

    evaluations = []
    for mark in marks["candidates"]:
        footprint = region(frame, mark)
        if not np.any(footprint & observed):
            raise Invalid("Each repair range needs usable observed image pixels inside the inspection face.")
        contained, edge_cut = [], []
        for pit in pit_rows:
            if not fully_observed(pit, labels, observed):
                continue
            if pit_inside(pit, labels, footprint):
                contained.append(pit)
            elif pit_touches(pit, labels, footprint):
                edge_cut.append(pit)
        touches_reserve = bool(reserve is not None and np.any(footprint & reserve))
        eligible = (not touches_reserve) and len(contained) >= minimum and not edge_cut
        evaluations.append({
            "mark_id": mark["id"], "title": mark["title"], "eligible": eligible,
            "full_observed_pits": len(contained), "edge_cut_pits": len(edge_cut),
            "touches_retained_glaze": touches_reserve,
            "footprint_mm2": round(float(footprint.sum() * pixel_area), 4),
            "covered_pit_area_mm2": round(float(sum(pit["area_mm2"] for pit in contained)), 4),
            "points": mark["points"],
        })

    eligible_ranges = [item for item in evaluations if item["eligible"]]
    if not eligible_ranges:
        raise Invalid("No repair range covers the minimum fully observed pits without cutting a pit or touching the retained glaze.")
    # Prefer the least disturbance of sound original glaze; break ties by the
    # larger covered pit area, then keep the result deterministic.
    chosen = sorted(eligible_ranges,
                    key=lambda item: (item["footprint_mm2"], -item["covered_pit_area_mm2"], item["title"]))[0]

    chosen_mark = next(mark for mark in marks["candidates"] if mark["id"] == chosen["mark_id"])
    chosen_footprint = region(frame, chosen_mark)
    chosen_pits = [pit for pit in pit_rows
                   if fully_observed(pit, labels, observed) and pit_inside(pit, labels, chosen_footprint)]
    snapshot_pits = []
    for pit in chosen_pits:
        x0 = pit["x"] / (width - 1)
        y0 = pit["y"] / (height - 1)
        x1 = (pit["x"] + pit["width"] - 1) / (width - 1)
        y1 = (pit["y"] + pit["height"] - 1) / (height - 1)
        snapshot_pits.append({
            "center_mm": pit["center_mm"], "area_mm2": pit["area_mm2"],
            "width_mm": pit["width_mm"], "height_mm": pit["height_mm"],
            "bbox_points": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
        })

    shown = image(frame)
    tint(shown, observed & ~candidates, (.25, .78, .73), .18)
    tint(shown, candidates, (.65, .36, .83), .34)
    if reserve is not None:
        tint(shown, reserve, (.95, .84, .32), .5)
    for item in evaluations:
        footprint = region(frame, next(mark for mark in marks["candidates"] if mark["id"] == item["mark_id"]))
        tint(shown, footprint, (.3, .62, .96) if item["mark_id"] != chosen["mark_id"] else (.95, .48, .24), .42)

    rows = [{
        "repair_range": item["title"], "eligible": item["eligible"],
        "full_observed_pits": item["full_observed_pits"], "edge_cut_pits": item["edge_cut_pits"],
        "touches_retained_glaze": item["touches_retained_glaze"],
        "footprint_mm2": item["footprint_mm2"], "covered_pit_area_mm2": item["covered_pit_area_mm2"],
        "adopted": item["mark_id"] == chosen["mark_id"],
    } for item in evaluations]
    summary = {
        "adopted_repair_range": chosen["title"], "adopted_mark_id": chosen["mark_id"],
        "minimum_full_observed_pits": minimum, "dark_pit_threshold": cutoff,
        "eligible_ranges": len(eligible_ranges), "candidate_ranges": len(evaluations),
        "adopted_full_observed_pits": chosen["full_observed_pits"],
        "adopted_footprint_mm2": chosen["footprint_mm2"],
        "adopted_covered_pit_area_mm2": chosen["covered_pit_area_mm2"],
    }
    snapshot = {
        "mode": "repair-selection", "luma": cutoff, "minimum_pits": minimum,
        "adopted_mark_id": chosen["mark_id"], "adopted_title": chosen["title"],
        "adopted_points": chosen["points"],
        "reserve_points": marks["reserve"]["points"] if marks.get("reserve") is not None else None,
        "pits": snapshot_pits,
    }
    return summary, rows, shown, snapshot


def repair_tone(frame, marks, opts, reference):
    """Glaze tone measured only on untouched, fully observed original glaze."""
    if reference.get("mode") != "repair-selection":
        raise Invalid("Choose a saved Glaze repair selection reading as the reference.")
    window = region(frame, marks["window"])
    if not np.any(window):
        raise Invalid("The tone window needs a marked area on the calibrated tile plane.")
    snapshot = reference["snapshot"]
    footprint = region(frame, {"points": snapshot["adopted_points"]})
    # Re-detect compact pits with the threshold frozen in the adopted reading,
    # so residual pits outside the repair range are also excluded without
    # depending on any later Pinhole map.
    _candidates, _labels, fresh_pits, _pit_rows = compact_pits(frame, window & frame.useful, snapshot["luma"])
    known_pits = np.zeros(frame.support.shape, dtype=bool)
    for pit in snapshot["pits"]:
        known_pits |= region(frame, {"points": pit["bbox_points"]})
    residual_pits = fresh_pits & ~footprint
    all_pits = known_pits | fresh_pits

    missing = window & ~frame.support
    excluded = footprint | all_pits | ~frame.support
    measured = window & frame.useful & ~excluded
    if np.count_nonzero(measured) < 32:
        raise Invalid("Not enough untouched, observed original glaze remains in this window.")
    lab_pixels = lab(frame.rgb[measured])
    low, center, high = np.percentile(lab_pixels, [10, 50, 90], axis=0)
    pixel_area = frame.step[0] * frame.step[1]
    rows = [
        {"channel": name, "p10": round(float(low[i]), 4), "median": round(float(center[i]), 4),
         "p90": round(float(high[i]), 4)}
        for i, name in enumerate(("L*", "a*", "b*"))
    ]
    shown = image(frame)
    tint(shown, missing, (.45, .45, .45), .5)
    tint(shown, residual_pits & window, (.65, .36, .83), .5)
    tint(shown, footprint & window, (.95, .48, .24), .38)
    tint(shown, measured, (.38, .78, .86), .3)
    summary = {
        "reference_reading": snapshot["adopted_title"], "reference_mark_id": snapshot["adopted_mark_id"],
        "pixels": int(measured.sum()), "lab": rounded(center),
        "lightness_spread": round(float(high[0] - low[0]), 4),
        "window_mm2": round(float(window.sum() * pixel_area), 4),
        "excluded_repair_mm2": round(float((footprint & window).sum() * pixel_area), 4),
        "excluded_residual_pit_mm2": round(float((residual_pits & window).sum() * pixel_area), 4),
        "excluded_missing_coverage_mm2": round(float(missing.sum() * pixel_area), 4),
    }
    return summary, rows, shown


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
