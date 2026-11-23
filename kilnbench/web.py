"""Routes for the fired tile bench and archived reading evidence."""
from io import BytesIO

from flask import Blueprint, Response, jsonify, render_template, request, send_file

from image_lab.validation import Conflict, Invalid

from . import analysis, demo, evidence, photos, scales
from .model import MARKS, READINGS, ROLES, choice, locate, mark_fields, obj, run_fields, text
from .store import Library, identity, stamp

MEASUREMENTS = {
    "glaze-tone": (analysis.glaze_tone, {"region": "inspect"}, {}),
    "glaze-contrast": (analysis.glaze_contrast, {"baseglaze": "baseglaze", "accent": "accent"}, {}),
    "decal-coverage": (analysis.decal_coverage, {"region": "decor", "baseglaze": "baseglaze", "accent": "accent"}, {}),
    "brush-period": (analysis.brush_period, {"region": "repeat"}, {}),
    "pinhole-map": (analysis.pinhole_map, {"region": "pinhole"}, {}),
    "rim_transition": (analysis.rim_transition, {"line": "stroke"}, {}),
    "decal-offset": (analysis.decal_offsets, {"reference": "register"}, {"comparison": "register"}),
    "grazing-response": (analysis.grazing_response, {"matte": "matte", "gloss": "gloss", "decor": "decor"}, {}),
    "gloss-scale": (analysis.gloss_scale, {
        "matte_a": "matte", "gloss_a": "gloss", "check_a": "check",
        "matte_b": "matte", "gloss_b": "gloss", "check_b": "check",
    }, {}),
    "paired-coverage": (analysis.paired_coverage, {"region": "inspect"}, {}),
    "surface-marks": (analysis.surface_marks, {"region": "inspect"}, {}),
    "color-drift": (analysis.color_drift, {"region": "inspect"}, {}),
}
PAIRED = {"decal-offset", "grazing-response", "gloss-scale", "paired-coverage", "color-drift"}
SCALE_MODES = {"grazing-response", "surface-marks"}
SCALE_SIDES = {"grazing-response": ("diffuse", "raking"), "surface-marks": ("raking", "diffuse")}
OPTIONS = {
    "decal-coverage": {"threshold": ["Decal likeness threshold", .5, .01]},
    "pinhole-map": {"luma": ["Dark-pit threshold", .31, .01]},
    "gloss-scale": {
        "expected": ["Expected check reading", .22, .01],
        "low": ["Check interval low", .1, .01],
        "high": ["Check interval high", .35, .01],
        "tie_tolerance": ["Near-tie fraction", .1, .01],
    },
    "surface-marks": {"contrast": ["Spot contrast", .075, .005], "minimum_mm2": ["Minimum area (mm²)", .15, .01]},
    "color-drift": {"delta_e": ["Color difference threshold", 5, .1]},
}
SCALED_OPTIONS = {
    "surface-marks": {
        "scale_contrast": ["Spot contrast (scale units)", .5, .05],
        "minimum_mm2": ["Minimum area (mm²)", .15, .01],
    },
}
CHOICES = {
    "gloss-scale": {"rule": ["Selection rule", "separation", {
        "separation": "Wider matte–gloss separation",
        "closeness": "Reading closest to the expected value",
    }]},
}
SCALE_CONTRAST = 0.5
NO_SCALE = "independent"


def input_json():
    return obj(request.get_json(silent=True))


def sample_frame(library, run, photo):
    return photos.plane({**photo, "pixels": photos.decode(library.blob(photo["blob"]).read_bytes())}, run)


def measure(library, run, data):
    mode = choice(data.get("mode"), MEASUREMENTS, "reading")
    work, source_roles, other_roles = MEASUREMENTS[mode]
    options = obj(data.get("options", {}))
    for key, value in CHOICES.get(mode, {}).items():
        options.setdefault(key, value[1])
    source = locate(run["photos"], data.get("photo"), "The source photograph")
    marked = obj(data.get("marks", {}))
    selected = {}
    scale = None
    chosen = data.get("scale")
    scaled = mode in SCALE_MODES and bool(chosen) and chosen != NO_SCALE
    other = None
    if scaled:
        scale = scales.require(run, chosen)
        first, second = SCALE_SIDES[mode]
        frozen = {"diffuse": scale["photos"]["diffuse"], "raking": scale["photos"]["raking"]}
        if source["id"] != frozen[first]["id"]:
            raise Invalid(f"The adopted gloss scale is frozen to its own {first} photograph; use that one as the source.")
        other = locate(run["photos"], data.get("other"), "The comparison photograph")
        if other["id"] != frozen[second]["id"]:
            raise Invalid(f"The adopted gloss scale is frozen to its own {second} photograph; use that one as the comparison.")
        source, other = frozen[first], frozen[second]
    elif mode in PAIRED:
        other = locate(run["photos"], data.get("other"), "The comparison photograph")
        if other["id"] == source["id"]:
            raise Invalid("Choose a different comparison photograph.")
    source_frame = sample_frame(library, run, source)
    used = [source]

    def read_marks(assigned, capture, allow_frozen=False):
        for slot, purpose in assigned.items():
            if allow_frozen and slot in ("matte", "gloss"):
                selected[slot] = scale["marks"][slot]
                continue
            mark = locate(run["marks"], marked.get(slot), MARKS[purpose])
            if mark["photo"] != capture["id"] or mark["purpose"] != purpose:
                raise Invalid(f"Select a {MARKS[purpose].lower()} mark from the specified photograph.")
            selected[slot] = mark

    if mode == "surface-marks" and not scaled:
        read_marks(source_roles, source)
        summary, rows, pixels = analysis.surface_marks(source_frame, selected, options)
    elif mode == "surface-marks":
        window = locate(run["marks"], marked.get("region"), MARKS["inspect"])
        if window["photo"] != source["id"] or window["purpose"] != "inspect":
            raise Invalid("Select an inspection patch from the grazing-light photograph.")
        if window["id"] in {scale["marks"][slot]["id"] for slot in ("matte", "gloss", "check")}:
            raise Invalid("Mark a separate inspection window instead of one of the adopted scale patches.")
        selected["region"] = window
        other_frame = sample_frame(library, run, other)
        used.append(other)
        options = {"scale_contrast": SCALE_CONTRAST, **options}
        summary, rows, pixels = analysis.raking_spots(source_frame, other_frame, selected, options, scale)
    elif mode in PAIRED:
        other_frame = sample_frame(library, run, other)
        read_marks(source_roles, source, allow_frozen=scaled)
        read_marks(other_roles, other, allow_frozen=scaled)
        used.append(other)
        summary, rows, pixels = work(source_frame, other_frame, selected, options)
    else:
        read_marks(source_roles, source)
        summary, rows, pixels = work(source_frame, selected, options)
    result = {
        "mode": mode, "title": READINGS[mode][0], "summary": summary, "rows": rows, "photos": used,
        "options": options, "marks": selected, "sheet_mm": [run["width_mm"], run["height_mm"]],
        "source_revision": run["revision"], "overlay": library.keep(photos.png(pixels)),
    }
    if mode in SCALE_MODES:
        result["scale"] = None if scale is None else {
            "id": scale["id"], "title": scale["title"], "label": scale["label"],
            "separation": scale["witnesses"]["separation"], "reading_id": scale["reading_id"],
        }
    return result


def install(app, root):
    library = Library(root)
    api = Blueprint("kilnbench", __name__, url_prefix="/kiln")

    @app.get("/")
    def kiln_desk():
        return render_template("kiln.html")

    @api.get("/health")
    def health():
        return jsonify(service="kilnbench", status="ready")

    @api.get("/config")
    def config():
        definitions = {}
        for mode, (title, help_text) in READINGS.items():
            _, source_roles, other_roles = MEASUREMENTS[mode]
            definitions[mode] = {"title": title, "help": help_text, "reference": source_roles, "comparison": other_roles,
                                 "paired": mode in PAIRED, "options": OPTIONS.get(mode, {}),
                                 "choices": CHOICES.get(mode, {}), "scale": mode in SCALE_MODES,
                                 "scaled_options": SCALED_OPTIONS.get(mode, {})}
        return jsonify(readings=definitions, marks=MARKS, roles=ROLES)

    @api.get("/runs")
    def runs():
        return jsonify(library.list())

    @api.post("/runs")
    def new_run():
        return jsonify(library.new_run(run_fields(input_json()))), 201

    @api.post("/demo")
    def new_demo():
        return jsonify(demo.create(library)), 201

    @api.get("/runs/<key>")
    def run(key):
        return jsonify(library.run(key))

    @api.post("/runs/<key>/photos")
    def add_photo(key):
        incoming = request.files.get("file")
        if incoming is None:
            raise Invalid("Choose a photograph to upload.")
        data = incoming.stream.read(photos.MAX_PAYLOAD + 1)
        if not data or len(data) > photos.MAX_PAYLOAD:
            raise Invalid("Use a nonempty photograph of at most 20 MiB.")
        photos.decode(data)
        title = text(request.form.get("title"), "a photograph title")
        role = choice(request.form.get("role"), ROLES, "photograph role")
        try:
            revision = int(request.form.get("revision"))
        except (TypeError, ValueError) as error:
            raise Invalid("The run revision is required.") from error

        def append(run):
            if len(run["photos"]) >= 12:
                raise Invalid("This run already holds 12 sample photographs.")
            run["photos"].append({"id": identity(), "title": title, "name": title, "role": role,
                                  "filename": photos.basename(incoming.filename), "blob": library.keep(data),
                                  "corners": None, "synthetic": False, "uploaded": stamp()})

        return jsonify(library.change(key, revision, append)), 201

    @api.put("/runs/<key>/photos/<photo_key>/calibration")
    def calibrate(key, photo_key):
        data = input_json()
        corners = photos.corners(data.get("corners"))

        def update(run):
            locate(run["photos"], photo_key, "The photograph")["corners"] = corners

        return jsonify(library.change(key, data.get("revision"), update))

    @api.get("/runs/<key>/photos/<photo_key>/image")
    def image_preview(key, photo_key):
        run = library.run(key)
        photo = locate(run["photos"], photo_key, "The photograph")
        if request.args.get("view") == "source" or not photo["corners"]:
            pixels = photos.decode(library.blob(photo["blob"]).read_bytes())
        else:
            pixels = photos.image(sample_frame(library, run, photo))
        return Response(photos.png(pixels), mimetype="image/png")

    @api.get("/runs/<key>/photos/<photo_key>/source")
    def original(key, photo_key):
        run = library.run(key)
        photo = locate(run["photos"], photo_key, "The photograph")
        return send_file(library.blob(photo["blob"]), download_name=photo["filename"], as_attachment=True)

    @api.post("/runs/<key>/marks")
    def create_mark(key):
        data = input_json()
        mark = mark_fields(data)

        def change(run):
            photo = locate(run["photos"], data.get("photo"), "The photograph")
            if not photo["corners"]:
                raise Invalid("Register the sheet before marking its calibrated plane.")
            if len(run["marks"]) >= 100:
                raise Invalid("This run already holds 100 marks.")
            run["marks"].append({**mark, "id": identity(), "photo": photo["id"]})

        return jsonify(library.change(key, data.get("revision"), change)), 201

    @api.delete("/runs/<key>/marks/<mark_key>")
    def delete_mark(key, mark_key):
        data = input_json()

        def change(run):
            run["marks"].remove(locate(run["marks"], mark_key, "The mark"))

        return jsonify(library.change(key, data.get("revision"), change))

    @api.post("/runs/<key>/readings")
    def create_reading(key):
        data = input_json()
        run = library.run(key)
        if run["revision"] != data.get("revision"):
            raise Conflict("The run changed. Reload it before saving.")
        result = measure(library, run, data)
        updated, reading = library.save_reading(key, data["revision"], result)
        return jsonify(run=updated, reading=reading), 201

    @api.get("/runs/<key>/readings/<reading_id>")
    def reading_detail(key, reading_id):
        return jsonify(library.reading(library.run(key), reading_id))

    @api.get("/runs/<key>/readings/<reading_id>/overlay")
    def reading_overlay(key, reading_id):
        result = library.reading(library.run(key), reading_id)
        return send_file(library.blob(result["overlay"]), mimetype="image/png")

    @api.get("/runs/<key>/readings/<reading_id>/csv")
    def reading_csv(key, reading_id):
        result = library.reading(library.run(key), reading_id)
        return send_file(BytesIO(evidence.rows_csv(result)), download_name="measured-values.csv",
                         as_attachment=True, mimetype="text/csv")

    @api.get("/runs/<key>/readings/<reading_id>/packet")
    def reading_packet(key, reading_id):
        result = library.reading(library.run(key), reading_id)
        return send_file(BytesIO(evidence.packet(library, result)), download_name="sample-reading.zip",
                         as_attachment=True, mimetype="application/zip")

    @api.get("/runs/<key>/scales")
    def scale_list(key):
        run = library.run(key)
        adopted = scales.active(run)
        adopted_id = None if adopted is None else adopted["id"]
        return jsonify({"adopted": adopted_id, "entries": [
            {field: scale[field] for field in (
                "id", "title", "label", "created", "reading_id", "witnesses", "check", "selection_rule",
            )} | {"active": scale["id"] == adopted_id} for scale in run["scales"]
        ]})

    @api.post("/runs/<key>/scales")
    def adopt_scale(key):
        data = input_json()
        run = library.run(key)
        if run["revision"] != data.get("revision"):
            raise Conflict("The run changed. Reload it before adopting.")
        reading = library.reading(run, data.get("reading"))
        title = text(data.get("title") or "", "a gloss scale title", 100, optional=True)
        existing = next((scale for scale in run["scales"] if scale["reading_id"] == reading["id"]), None)
        if existing is not None:
            def reactivate(current):
                current["adopted_scale"] = existing["id"]

            return jsonify(run=library.change(key, data["revision"], reactivate), scale=existing), 200
        scale = scales.snapshot(run, reading, title)

        def change(current):
            if len(current["scales"]) >= 24:
                raise Invalid("This run already holds 24 adopted gloss scales.")
            current["scales"].append(scale)
            current["adopted_scale"] = scale["id"]

        return jsonify(run=library.change(key, data["revision"], change), scale=scale), 201

    @api.get("/runs/<key>/scales/<scale_key>")
    def scale_detail(key, scale_key):
        run = library.run(key)
        found = scales.require(run, scale_key)
        adopted = scales.active(run)
        return jsonify({**found, "active": adopted is not None and adopted["id"] == found["id"]})

    @api.put("/runs/<key>/scales/<scale_key>")
    def activate_scale(key, scale_key):
        data = input_json()
        found = scales.require(library.run(key), scale_key)
        if data.get("active") is not True:
            raise Invalid("Adopt another scale to replace this one; the adopted references stay kept.")

        def change(current):
            current["adopted_scale"] = found["id"]

        return jsonify(run=library.change(key, data.get("revision"), change))

    @api.get("/runs/<key>/scales/<scale_key>/photos/<side>")
    def scale_photo(key, scale_key, side):
        if side not in ("diffuse", "raking"):
            raise Invalid("The scale side is invalid.")
        scale = scales.require(library.run(key), scale_key)
        photo = scale["photos"][side]
        return send_file(library.blob(photo["blob"]), download_name=photo["filename"], mimetype="image/png")

    @api.get("/runs/<key>/scales/<scale_key>/packet")
    def scale_packet(key, scale_key):
        scale = scales.require(library.run(key), scale_key)
        return send_file(BytesIO(evidence.scale_packet(library, scale)), download_name="gloss-scale.zip",
                         as_attachment=True, mimetype="application/zip")

    app.register_blueprint(api)
    app.extensions["tile_library"] = library
