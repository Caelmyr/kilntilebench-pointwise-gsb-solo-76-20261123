"""Routes for the fired tile bench and archived reading evidence."""
from io import BytesIO

from flask import Blueprint, Response, jsonify, render_template, request, send_file

from image_lab.validation import Conflict, Invalid

from . import analysis, demo, evidence, photos
from .model import MARKS, READINGS, ROLES, choice, identifier, locate, mark_fields, obj, run_fields, text
from .store import Library, identity, stamp

MEASUREMENTS = {
    "glaze-tone": (analysis.glaze_tone, {"region": "inspect"}, {}),
    "glaze-contrast": (analysis.glaze_contrast, {"baseglaze": "baseglaze", "accent": "accent"}, {}),
    "decal-coverage": (analysis.decal_coverage, {"region": "decor", "baseglaze": "baseglaze", "accent": "accent"}, {}),
    "brush-period": (analysis.brush_period, {"region": "repeat"}, {}),
    "pinhole-map": (analysis.pinhole_map, {"region": "pinhole"}, {}),
    "repair-selection": (analysis.repair_selection,
                         {"inspection": "pinhole", "candidates": "repair", "reserve": "reserve"}, {}),
    "repair-tone": (analysis.repair_tone, {"window": "inspect"}, {}),
    "rim_transition": (analysis.rim_transition, {"line": "stroke"}, {}),
    "decal-offset": (analysis.decal_offsets, {"reference": "register"}, {"comparison": "register"}),
    "grazing-response": (analysis.grazing_response, {"matte": "matte", "gloss": "gloss", "decor": "decor"}, {}),
    "paired-coverage": (analysis.paired_coverage, {"region": "inspect"}, {}),
    "surface-marks": (analysis.surface_marks, {"region": "inspect"}, {}),
    "color-drift": (analysis.color_drift, {"region": "inspect"}, {}),
}
LIST_SLOTS = {"candidates"}
OPTIONAL_SLOTS = {"reserve"}
PAIRED = {"decal-offset", "grazing-response", "paired-coverage", "color-drift"}
OPTIONS = {
    "decal-coverage": {"threshold": ["Decal likeness threshold", .5, .01]},
    "pinhole-map": {"luma": ["Dark-pit threshold", .31, .01]},
    "repair-selection": {"luma": ["Dark-pit threshold", .31, .01],
                         "minimum_pits": ["Minimum full observed pits", 2, 1]},
    "surface-marks": {"contrast": ["Spot contrast", .075, .005], "minimum_mm2": ["Minimum area (mm²)", .15, .01]},
    "color-drift": {"delta_e": ["Color difference threshold", 5, .1]},
}


def input_json():
    return obj(request.get_json(silent=True))


def sample_frame(library, run, photo):
    return photos.plane({**photo, "pixels": photos.decode(library.blob(photo["blob"]).read_bytes())}, run)


def measure(library, run, data):
    mode = choice(data.get("mode"), MEASUREMENTS, "reading")
    work, source_roles, other_roles = MEASUREMENTS[mode]
    source = locate(run["photos"], data.get("photo"), "The source photograph")
    source_frame = sample_frame(library, run, source)
    options = obj(data.get("options", {}))
    marked = obj(data.get("marks", {}))
    selected = {}

    def read_one(slot, purpose, capture):
        mark = locate(run["marks"], marked.get(slot), MARKS[purpose])
        if mark["photo"] != capture["id"] or mark["purpose"] != purpose:
            raise Invalid(f"Select a {MARKS[purpose].lower()} mark from the specified photograph.")
        selected[slot] = mark

    def read_marks(assigned, capture):
        for slot, purpose in assigned.items():
            if slot in LIST_SLOTS:
                values = marked.get(slot)
                if not isinstance(values, list) or not 1 <= len(values) <= 8:
                    raise Invalid("Choose between one and eight repair range marks.")
                if len(set(values)) != len(values):
                    raise Invalid("Choose each repair range mark only once.")
                chosen = []
                for value in values:
                    mark = locate(run["marks"], value, MARKS[purpose])
                    if mark["photo"] != capture["id"] or mark["purpose"] != purpose:
                        raise Invalid(f"Select a {MARKS[purpose].lower()} mark from the specified photograph.")
                    chosen.append(mark)
                selected[slot] = chosen
            elif slot in OPTIONAL_SLOTS and not marked.get(slot):
                selected[slot] = None
            else:
                read_one(slot, purpose, capture)

    read_marks(source_roles, source)
    used = [source]
    snapshot = None
    if mode in PAIRED:
        other = locate(run["photos"], data.get("other"), "The comparison photograph")
        if other["id"] == source["id"]:
            raise Invalid("Choose a different comparison photograph.")
        other_frame = sample_frame(library, run, other)
        read_marks(other_roles, other)
        used.append(other)
        summary, rows, pixels = work(source_frame, other_frame, selected, options)
    elif mode == "repair-tone":
        reference = library.reading(run, identifier(data.get("reference_reading")))
        if reference.get("mode") != "repair-selection":
            raise Invalid("Choose a saved Glaze repair selection reading as the reference.")
        summary, rows, pixels = work(source_frame, selected, options, reference)
    elif mode == "repair-selection":
        summary, rows, pixels, snapshot = work(source_frame, selected, options)
    else:
        summary, rows, pixels = work(source_frame, selected, options)
    result = {
        "mode": mode, "title": READINGS[mode][0], "summary": summary, "rows": rows, "photos": used,
        "options": options, "marks": selected, "sheet_mm": [run["width_mm"], run["height_mm"]],
        "source_revision": run["revision"], "overlay": library.keep(photos.png(pixels)),
    }
    if snapshot is not None:
        result["snapshot"] = snapshot
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
            definitions[mode] = {"title": title, "help": help_text, "reference": source_roles,
                                "comparison": other_roles, "paired": mode in PAIRED,
                                "options": OPTIONS.get(mode, {}), "list_slots": sorted(LIST_SLOTS),
                                "optional_slots": sorted(OPTIONAL_SLOTS),
                                "requires_reading": mode == "repair-tone"}
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

    app.register_blueprint(api)
    app.extensions["tile_library"] = library
