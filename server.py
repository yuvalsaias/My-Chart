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
# SAFE ANALYZE
# ---------------------------
@app.route("/analyze", methods=["POST"])
def analyze():

    try:

        if not API_KEY:
            return jsonify({"error": "Missing API_KEY"}), 500

        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files["file"]

        # -------- REQUEST UPLOAD URL --------
        upload_res = requests.get(
            "https://api.music.ai/v1/upload",
            headers={"Authorization": API_KEY}
        )

        upload_data = upload_res.json()
        print("UPLOAD RESPONSE:", upload_data)

        if "uploadUrl" not in upload_data:
            return jsonify({
                "error": "Upload URL failed",
                "music_ai_response": upload_data
            }), 500

        # -------- UPLOAD FILE --------
        put_res = requests.put(
            upload_data["uploadUrl"],
            data=file.read(),
            headers={"Content-Type": file.content_type or "audio/mpeg"}
        )

        if put_res.status_code not in [200, 201]:
            return jsonify({
                "error": "File upload failed",
                "status": put_res.status_code,
                "response": put_res.text
            }), 500

        # -------- CREATE JOB --------
        job_res = requests.post(
            "https://api.music.ai/v1/job",
            headers={
                "Authorization": API_KEY,
                "Content-Type": "application/json"
            },
            json={
                "name": "Chord Job",
                "workflow": WORKFLOW,
                "params": {
                    "Input 1": upload_data["downloadUrl"]
                }
            }
        )

        print("JOB RESPONSE:", job_res.text)

        job_data = job_res.json()

        if "id" not in job_data:
            return jsonify({
                "error": "Job creation failed",
                "music_ai_response": job_data
            }), 500

        return jsonify({"job_id": job_data["id"]})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------
# PICK BEST CHORD
# ---------------------------
def pick_best_chord(c):
    return (
        c.get("chord_complex_pop")
        or c.get("chord_simple_pop")
        or c.get("chord_basic_pop")
    )


# ---------------------------
# BUILD BEAT GRID
# ---------------------------
def build_beat_grid(beats):

    grid = []

    for i, beat in enumerate(beats):

        start = beat["time"]

        if i < len(beats) - 1:
            end = beats[i + 1]["time"]
        else:
            end = start + 0.5

        grid.append({
            "start": start,
            "end": end,
            "beat": beat["beatNum"],
            "bar": beat.get("barNum", 0)
        })

    return grid


# ---------------------------
# FIND CHORD AT TIME
# ---------------------------
def find_chord_at_time(chords, time):

    for c in chords:
        if c["start"] <= time < c["end"]:
            return c
    return None


# ---------------------------
# BUILD SEGMENTS
# ---------------------------
def build_segments(chords, beat_grid):

    segments = []
    last = None

    for beat in beat_grid:

        chord = find_chord_at_time(chords, beat["start"])

        if not chord:
            continue

        name = pick_best_chord(chord)

        if not name:
            continue

        if chord.get("bass"):
            name += "/" + chord["bass"]

        marker = (name, beat["bar"], beat["beat"])

        if marker != last:

            segments.append({
                "chord": name,
                "start_bar": beat["bar"],
                "start_beat": beat["beat"]
            })

            last = marker

    return segments


# ---------------------------
# SECTION NAMING
# ---------------------------
section_counter = {"verse": 0, "chorus": 0, "pre": 0, "inst": 0}


def map_section(label):

    name = label.lower()

    if "intro" in name:
        return "INTRO"

    if "outro" in name:
        return "OUTRO"

    if "verse" in name:
        section_counter["verse"] += 1
        return f"A{section_counter['verse']}"

    if "chorus" in name:
        section_counter["chorus"] += 1
        return f"B{section_counter['chorus']}"

    if "pre" in name or "bridge" in name:
        section_counter["pre"] += 1
        return f"PRE {section_counter['pre']}"

    if "inst" in name:
        section_counter["inst"] += 1
        return f"INST {section_counter['inst']}"

    return label.upper()


# ---------------------------
# KEY PARSER
# ---------------------------
def parse_key(key_json):

    if not key_json:
        return 0, "major"

    tonic = key_json.get("tonic", "C")
    mode = key_json.get("mode", "major")

    fifths_map = {
        "C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F#": 6, "C#": 7,
        "F": -1, "Bb": -2, "Eb": -3, "Ab": -4, "Db": -5, "Gb": -6, "Cb": -7
    }

    return fifths_map.get(tonic, 0), mode


# ---------------------------
# MUSICXML BUILDER
# ---------------------------
def chords_to_musicxml(segments, sections, key_json):

    divisions = 4
    fifths, mode = parse_key(key_json)

    score = Element("score-partwise", version="3.1")

    part_list = SubElement(score, "part-list")
    score_part = SubElement(part_list, "score-part", id="P1")
    SubElement(score_part, "part-name").text = "Chords"

    part = SubElement(score, "part", id="P1")

    bars = sorted(set(s["start_bar"] for s in segments))

    for i, bar in enumerate(bars):

        measure = SubElement(part, "measure", number=str(bar + 1))

        if i == 0:

            attributes = SubElement(measure, "attributes")

            SubElement(attributes, "divisions").text = str(divisions)

            key = SubElement(attributes, "key")
            SubElement(key, "fifths").text = str(fifths)
            SubElement(key, "mode").text = mode

            time = SubElement(attributes, "time")
            SubElement(time, "beats").text = "4"
            SubElement(time, "beat-type").text = "4"

        # Sections
        for sec in sections:
            if sec["start_bar"] == bar:

                direction = SubElement(measure, "direction")
                dtype = SubElement(direction, "direction-type")

                words = SubElement(dtype, "words")
                words.set("enclosure", "rectangle")
                words.text = map_section(sec["label"])

        # Chords
        for seg in [s for s in segments if s["start_bar"] == bar]:

            harmony = SubElement(measure, "harmony")

            root = SubElement(harmony, "root")
            SubElement(root, "root-step").text = seg["chord"][0]

            SubElement(harmony, "kind").text = "major"

            offset = SubElement(harmony, "offset")
            offset.text = str((seg["start_beat"] - 1) * divisions)

    return tostring(score, encoding="utf-8", xml_declaration=True)


# ---------------------------
# FETCH MUSIC.AI
# ---------------------------
def fetch_music_ai(job_id):

    status = requests.get(
        f"https://api.music.ai/v1/job/{job_id}",
        headers={"Authorization": API_KEY}
    ).json()

    if status.get("status") != "SUCCEEDED":
        return None

    chords = requests.get(status["result"]["chords"]).json()
    beats = requests.get(status["result"]["beats"]).json()
    sections = requests.get(status["result"]["sections"]).json()
    key = requests.get(status["result"]["key"]).json()

    if isinstance(chords, dict):
        chords = chords.get("chords", [])

    return chords, beats, sections, key


# ---------------------------
# STATUS
# ---------------------------
@app.route("/status/<job_id>")
def status(job_id):

    data = fetch_music_ai(job_id)

    if not data:
        return jsonify({"status": "PROCESSING"})

    chords, beats, sections, key = data

    beat_grid = build_beat_grid(beats)
    segments = build_segments(chords, beat_grid)

    return jsonify({
        "status": "SUCCEEDED",
        "chart": segments
    })


# ---------------------------
# MUSICXML
# ---------------------------
@app.route("/musicxml/<job_id>")
def musicxml(job_id):

    data = fetch_music_ai(job_id)

    if not data:
        return jsonify({"error": "Processing"}), 400

    chords, beats, sections, key = data

    beat_grid = build_beat_grid(beats)
    segments = build_segments(chords, beat_grid)

    xml = chords_to_musicxml(segments, sections, key)

    return Response(
        xml,
        mimetype="application/xml",
        headers={"Content-Disposition": "attachment; filename=chart.musicxml"}
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
