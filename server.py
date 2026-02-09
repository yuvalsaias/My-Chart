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
# TEST
# ---------------------------
@app.route("/test")
def test():
    return "Server works!"


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
def build_beat_grid(beats_json):

    grid = []

    for i, beat in enumerate(beats_json):

        start = beat["time"]

        if i < len(beats_json) - 1:
            end = beats_json[i + 1]["time"]
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

    for chord in chords:
        if chord["start"] <= time < chord["end"]:
            return chord

    return None


# ---------------------------
# BUILD SEGMENTS (CORRECTED)
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
# SAFE CHORD PARSER
# ---------------------------
def parse_chord_for_xml(chord):

    chord = chord.replace("Δ", "maj")
    chord = chord.replace("-", "m")

    if "/" in chord:
        chord = chord.split("/")[0]

    match = re.match(r"^([A-G])([#b]?)(.*)$", chord)

    if not match:
        return "C", None, "major"

    step, accidental, quality = match.groups()

    alter = 1 if accidental == "#" else -1 if accidental == "b" else None

    quality = quality.lower()

    if "maj7" in quality:
        kind = "major-seventh"
    elif "m7" in quality:
        kind = "minor-seventh"
    elif quality.startswith("m"):
        kind = "minor"
    elif "7" in quality:
        kind = "dominant"
    else:
        kind = "major"

    return step, alter, kind


# ---------------------------
# MUSICXML BUILDER (FIXED)
# ---------------------------
def chords_to_musicxml(segments):

    divisions = 4  # quarter note resolution

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
            SubElement(key, "fifths").text = "0"

            time = SubElement(attributes, "time")
            SubElement(time, "beats").text = "4"
            SubElement(time, "beat-type").text = "4"

        bar_segments = [s for s in segments if s["start_bar"] == bar]

        for seg in bar_segments:

            harmony = SubElement(measure, "harmony")

            step, alter, kind = parse_chord_for_xml(seg["chord"])

            root = SubElement(harmony, "root")
            SubElement(root, "root-step").text = step

            if alter:
                SubElement(root, "root-alter").text = str(alter)

            SubElement(harmony, "kind").text = kind

            offset = SubElement(harmony, "offset")
            offset.text = str((seg["start_beat"] - 1) * divisions)

    return tostring(score, encoding="utf-8", xml_declaration=True)


# ---------------------------
# FETCH MUSIC.AI DATA (SAFE)
# ---------------------------
def fetch_music_ai(job_id):

    try:

        status_res = requests.get(
            f"https://api.music.ai/api/job/{job_id}",
            headers={"Authorization": API_KEY}
        )

        data = status_res.json()

        if data.get("status") != "SUCCEEDED":
            return None

        chords = requests.get(data["result"]["chords"]).json()
        beats = requests.get(data["result"]["beats"]).json()

        if isinstance(chords, dict):
            chords = chords.get("chords", [])

        return chords, beats

    except:
        return None


# ---------------------------
# ANALYZE (STABLE)
# ---------------------------
@app.route("/analyze", methods=["POST"])
def analyze():

    try:

        if not API_KEY:
            return jsonify({"error": "Missing API_KEY"}), 500

        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files["file"]

        upload = requests.get(
            "https://api.music.ai/v1/upload",
            headers={"Authorization": API_KEY}
        ).json()

        requests.put(
            upload["uploadUrl"],
            data=file.read(),
            headers={"Content-Type": file.content_type or "audio/mpeg"}
        )

        job = requests.post(
            "https://api.music.ai/api/job",
            headers={
                "Authorization": API_KEY,
                "Content-Type": "application/json"
            },
            json={
                "workflow": WORKFLOW,
                "params": {"Input 1": upload["downloadUrl"]}
            }
        ).json()

        return jsonify({"job_id": job["id"]})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------
# STATUS
# ---------------------------
@app.route("/status/<job_id>")
def status(job_id):

    data = fetch_music_ai(job_id)

    if not data:
        return jsonify({"status": "PROCESSING"})

    chords, beats = data

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

    chords, beats = data

    beat_grid = build_beat_grid(beats)
    segments = build_segments(chords, beat_grid)

    xml = chords_to_musicxml(segments)

    return Response(
        xml,
        mimetype="application/xml",
        headers={"Content-Disposition": "attachment; filename=chart.musicxml"}
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
