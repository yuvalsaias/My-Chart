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
# SECTION NAMING
# ---------------------------
section_counters = {
    "verse": 0,
    "chorus": 0,
    "pre": 0,
    "inst": 0
}


def map_section_name(name):

    name = name.lower()

    if "intro" in name:
        return "INTRO"

    if "outro" in name:
        return "OUTRO"

    if "verse" in name:
        section_counters["verse"] += 1
        return f"A{section_counters['verse']}"

    if "chorus" in name:
        section_counters["chorus"] += 1
        return f"B{section_counters['chorus']}"

    if "pre" in name or "bridge" in name:
        section_counters["pre"] += 1
        return f"PRE {section_counters['pre']}"

    if "inst" in name:
        section_counters["inst"] += 1
        return f"INST {section_counters['inst']}"

    return name.upper()


# ---------------------------
# PICK CHORD
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
# FIND CHORD
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
# PARSE CHORD XML
# ---------------------------
def parse_chord_for_xml(chord):

    chord = chord.replace("Δ", "maj").replace("-", "m")

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
# MUSICXML BUILDER
# ---------------------------
def chords_to_musicxml(segments, sections, beat_grid):

    divisions = 4

    score = Element("score-partwise", version="3.1")

    part_list = SubElement(score, "part-list")
    score_part = SubElement(part_list, "score-part", id="P1")
    SubElement(score_part, "part-name").text = "Chords"

    part = SubElement(score, "part", id="P1")

    bars = sorted(set(s["start_bar"] for s in segments))

    # ---- PICKUP ----
    first_beat = min(s["start_beat"] for s in segments)

    if first_beat > 1:
        pickup = SubElement(part, "measure", number="0")

        attributes = SubElement(pickup, "attributes")
        SubElement(attributes, "divisions").text = str(divisions)

        time = SubElement(attributes, "time")
        SubElement(time, "beats").text = str(4 - (first_beat - 1))
        SubElement(time, "beat-type").text = "4"

    # ---- NORMAL MEASURES ----
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

        # ----- SECTION TEXT -----
        for sec in sections:
            if sec["start_bar"] == bar:

                direction = SubElement(measure, "direction")
                dtype = SubElement(direction, "direction-type")

                words = SubElement(dtype, "words")
                words.set("enclosure", "rectangle")
                words.text = map_section_name(sec["label"])

        # ----- CHORDS -----
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
# FETCH MUSIC.AI DATA
# ---------------------------
def fetch_music_ai(job_id):

    status = requests.get(
        f"https://api.music.ai/api/job/{job_id}",
        headers={"Authorization": API_KEY}
    ).json()

    if status.get("status") != "SUCCEEDED":
        return None

    chords = requests.get(status["result"]["chords"]).json()
    beats = requests.get(status["result"]["beats"]).json()
    sections = requests.get(status["result"]["sections"]).json()

    if isinstance(chords, dict):
        chords = chords.get("chords", [])

    return chords, beats, sections


# ---------------------------
# ANALYZE
# ---------------------------
@app.route("/analyze", methods=["POST"])
def analyze():

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


# ---------------------------
# STATUS
# ---------------------------
@app.route("/status/<job_id>")
def status(job_id):

    data = fetch_music_ai(job_id)

    if not data:
        return jsonify({"status": "PROCESSING"})

    chords, beats, sections = data

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

    chords, beats, sections = data

    beat_grid = build_beat_grid(beats)
    segments = build_segments(chords, beat_grid)

    xml = chords_to_musicxml(segments, sections, beat_grid)

    return Response(
        xml,
        mimetype="application/xml",
        headers={"Content-Disposition": "attachment; filename=chart.musicxml"}
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
