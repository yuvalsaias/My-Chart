from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import requests
import os
import re
from xml.etree.ElementTree import Element, SubElement, tostring

app = Flask(__name__)
CORS(app)

API_KEY = os.environ.get("API_KEY")
WORKFLOW = "my-chart-recognizer"


# ---------------------------
# TEST ROUTE
# ---------------------------
@app.route("/test")
def test():
    return "Server works!"


# ---------------------------
# PICK BEST CHORD VERSION
# ---------------------------
def pick_best_chord(c):

    chord = c.get("chord_complex_pop")

    if not chord:
        chord = c.get("chord_simple_pop")

    if not chord:
        chord = c.get("chord_basic_pop")

    return chord


# ---------------------------
# PROFESSIONAL CHORD PARSER
# ---------------------------
def parse_chord_for_xml(chord):

    # remove slash bass for harmony root
    if "/" in chord:
        chord = chord.split("/")[0]

    match = re.match(r"^([A-G])([#b]?)(.*)$", chord)

    if not match:
        raise ValueError(f"Cannot parse chord: {chord}")

    step, accidental, quality = match.groups()

    alter = None
    if accidental == "#":
        alter = 1
    elif accidental == "b":
        alter = -1

    quality = quality.lower()

    # ----- MusicXML mapping -----
    if "maj7" in quality:
        kind = "major-seventh"
    elif "m7" in quality:
        kind = "minor-seventh"
    elif quality.startswith("m"):
        kind = "minor"
    elif "7" in quality:
        kind = "dominant"
    elif "dim" in quality:
        kind = "diminished"
    elif "aug" in quality:
        kind = "augmented"
    else:
        kind = "major"

    return step, alter, kind


# ---------------------------
# BUILD CHORD GRID
# ---------------------------
def build_chord_grid(chords_list):

    grid = {}

    for c in chords_list:

        chord = pick_best_chord(c)
        bass = c.get("bass")

        if not chord:
            continue

        if bass:
            chord = f"{chord}/{bass}"

        bar = c.get("start_bar")
        beat = c.get("start_beat")

        if bar is None or beat is None:
            continue

        grid.setdefault(bar, {})
        grid[bar][beat] = chord

    return grid


# ---------------------------
# MUSICXML BUILDER
# ---------------------------
def chords_to_musicxml(grid):

    score = Element("score-partwise", version="3.1")

    part_list = SubElement(score, "part-list")
    score_part = SubElement(part_list, "score-part", id="P1")
    SubElement(score_part, "part-name").text = "Chords"

    part = SubElement(score, "part", id="P1")

    # iterate only existing bars
    for i, bar in enumerate(sorted(grid.keys())):

        measure = SubElement(part, "measure", number=str(bar + 1))

        # attributes only in first measure
        if i == 0:
            attributes = SubElement(measure, "attributes")

            SubElement(attributes, "divisions").text = "1"

            key = SubElement(attributes, "key")
            SubElement(key, "fifths").text = "0"

            time = SubElement(attributes, "time")
            SubElement(time, "beats").text = "4"
            SubElement(time, "beat-type").text = "4"

        last_chord = None

        for beat in sorted(grid[bar].keys()):

            chord = grid[bar][beat]

            # show only chord changes
            if chord == last_chord:
                continue

            last_chord = chord

            harmony = SubElement(measure, "harmony")

            step, alter, kind = parse_chord_for_xml(chord)

            root = SubElement(harmony, "root")
            SubElement(root, "root-step").text = step

            if alter:
                SubElement(root, "root-alter").text = str(alter)

            SubElement(harmony, "kind").text = kind

            offset = SubElement(harmony, "offset")
            offset.text = str(beat - 1)

    return tostring(score, encoding="utf-8", xml_declaration=True)


# ---------------------------
# CREATE JOB (UPLOAD REAL FILE)
# ---------------------------
@app.route("/analyze", methods=["POST"])
def analyze():

    if "file" not in request.files:
        return jsonify({"error": "No file received"}), 400

    file = request.files["file"]

    try:

        # get upload URL
        upload_res = requests.get(
            "https://api.music.ai/v1/upload",
            headers={"Authorization": API_KEY}
        )

        upload_data = upload_res.json()

        upload_url = upload_data["uploadUrl"]
        download_url = upload_data["downloadUrl"]

        # upload file bytes
        requests.put(
            upload_url,
            data=file.read(),
            headers={"Content-Type": file.content_type}
        )

        # create job
        job_res = requests.post(
            "https://api.music.ai/api/job",
            headers={
                "accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": API_KEY
            },
            json={
                "name": file.filename,
                "workflow": WORKFLOW,
                "params": {
                    "Input 1": download_url
                }
            }
        )

        job_data = job_res.json()

        return jsonify({
            "status": "CREATED",
            "job_id": job_data["id"]
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------
# FETCH CHORDS
# ---------------------------
def fetch_chords(job_id):

    status_res = requests.get(
        f"https://api.music.ai/api/job/{job_id}",
        headers={"Authorization": API_KEY}
    )

    status_data = status_res.json()

    if status_data.get("status") != "SUCCEEDED":
        return None

    chords_url = status_data.get("result", {}).get("chords")

    chords_json = requests.get(chords_url).json()

    if isinstance(chords_json, dict):
        return chords_json.get("chords", [])

    return chords_json


# ---------------------------
# STATUS
# ---------------------------
@app.route("/status/<job_id>")
def status(job_id):

    chords = fetch_chords(job_id)

    if chords is None:
        return jsonify({"status": "PROCESSING"})

    return jsonify({
        "status": "SUCCEEDED",
        "chords": chords
    })


# ---------------------------
# DOWNLOAD MUSICXML
# ---------------------------
@app.route("/musicxml/<job_id>")
def musicxml(job_id):

    chords = fetch_chords(job_id)

    if not chords:
        return jsonify({"error": "Job not finished"}), 400

    grid = build_chord_grid(chords)
    xml_data = chords_to_musicxml(grid)

    return Response(
        xml_data,
        mimetype="application/xml",
        headers={
            "Content-Disposition": "attachment; filename=chords.musicxml"
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
