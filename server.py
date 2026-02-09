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
# PARSE CHORD TYPE
# ---------------------------
def parse_chord_for_xml(chord):

    match = re.match(r"([A-G])([#b]?)(.*)", chord)

    if not match:
        return "C", None, "major"

    step, accidental, rest = match.groups()

    alter = None
    if accidental == "#":
        alter = 1
    elif accidental == "b":
        alter = -1

    if "min" in rest or rest.startswith("m"):
        kind = "minor"
    elif "maj7" in rest:
        kind = "major-seventh"
    elif "7" in rest:
        kind = "dominant"
    else:
        kind = "major"

    return step, alter, kind


# ---------------------------
# BUILD BEAT GRID
# ---------------------------
def build_chord_grid(chords_list):

    grid = {}

    for c in chords_list:

        chord = c.get("chord_complex_pop")
        bass = c.get("bass")

        if not chord:
            continue

        if bass:
            chord = f"{chord}/{bass}"

        start_bar = c.get("start_bar")
        start_beat = c.get("start_beat")
        end_bar = c.get("end_bar")
        end_beat = c.get("end_beat")

        bar = start_bar
        beat = start_beat

        # ממלא את כל הביטים עד סוף האקורד
        while True:

            grid.setdefault(bar, {})
            grid[bar][beat] = chord

            if bar == end_bar and beat == end_beat:
                break

            beat += 1

            if beat > 4:  # הנחה 4/4 כרגע
                beat = 1
                bar += 1

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

    max_bar = max(grid.keys())

    for bar in range(max_bar + 1):

        measure = SubElement(part, "measure", number=str(bar + 1))

        if bar == 0:
            attributes = SubElement(measure, "attributes")
            SubElement(attributes, "divisions").text = "1"

            time = SubElement(attributes, "time")
            SubElement(time, "beats").text = "4"
            SubElement(time, "beat-type").text = "4"

            key = SubElement(attributes, "key")
            SubElement(key, "fifths").text = "0"

        if bar not in grid:
            continue

        beats = sorted(grid[bar].keys())

        last_chord = None

        for beat in beats:

            chord = grid[bar][beat]

            # מציג רק שינוי אקורד
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

            # מיקום בתוך התיבה
            offset = SubElement(harmony, "offset")
            offset.text = str(beat - 1)

    return tostring(score, encoding="utf-8", xml_declaration=True)


# ---------------------------
# CREATE JOB
# ---------------------------
@app.route("/analyze", methods=["POST"])
def analyze():

    if "file" not in request.files:
        return jsonify({"error": "No file received"}), 400

    audio_url = "https://music.ai/demo.ogg"

    try:

        job_res = requests.post(
            "https://api.music.ai/api/job",
            headers={
                "accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": API_KEY
            },
            json={
                "name": "Chord Recognition",
                "workflow": WORKFLOW,
                "params": {
                    "Input 1": audio_url
                }
            }
        )

        job_data = job_res.json()

        if "id" not in job_data:
            return jsonify(job_data), 500

        return jsonify({
            "status": "CREATED",
            "job_id": job_data["id"]
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------
# FETCH + PARSE CHORDS
# ---------------------------
def fetch_parsed_chords(job_id):

    status_res = requests.get(
        f"https://api.music.ai/api/job/{job_id}",
        headers={"Authorization": API_KEY}
    )

    status_data = status_res.json()

    if status_data.get("status") != "SUCCEEDED":
        return None

    chords_url = status_data.get("result", {}).get("chords")

    chords_res = requests.get(chords_url)
    chords_json = chords_res.json()

    if isinstance(chords_json, dict):
        chords_list = chords_json.get("chords", [])
    else:
        chords_list = chords_json

    return build_chord_grid(chords_list)


# ---------------------------
# STATUS
# ---------------------------
@app.route("/status/<job_id>")
def status(job_id):

    grid = fetch_parsed_chords(job_id)

    if grid is None:
        return jsonify({"status": "PROCESSING"})

    return jsonify({
        "status": "SUCCEEDED",
        "grid": grid
    })


# ---------------------------
# DOWNLOAD MUSICXML
# ---------------------------
@app.route("/musicxml/<job_id>")
def musicxml(job_id):

    grid = fetch_parsed_chords(job_id)

    if not grid:
        return jsonify({"error": "Job not finished"}), 400

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
