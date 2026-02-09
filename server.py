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
# PICK BEST CHORD
# ---------------------------
def pick_best_chord(c):
    return (
        c.get("chord_complex_pop")
        or c.get("chord_simple_pop")
        or c.get("chord_basic_pop")
    )


# ---------------------------
# SAFE CHORD PARSER
# ---------------------------
def parse_chord_for_xml(chord):
    try:
        chord = chord.replace("Δ", "maj")
        chord = chord.replace("-", "m")

        if "/" in chord:
            chord = chord.split("/")[0]

        match = re.match(r"^([A-G])([#b]?)(.*)$", chord)
        if not match:
            return "C", None, "major"

        step, accidental, quality = match.groups()

        alter = None
        if accidental == "#":
            alter = 1
        elif accidental == "b":
            alter = -1

        quality = quality.lower()

        if "maj7" in quality:
            kind = "major-seventh"
        elif "m7" in quality:
            kind = "minor-seventh"
        elif "sus" in quality:
            kind = "suspended-fourth"
        elif quality.startswith("m"):
            kind = "minor"
        elif "7" in quality:
            kind = "dominant"
        else:
            kind = "major"

        return step, alter, kind

    except:
        return "C", None, "major"


# ---------------------------
# SECTION MAPPING
# ---------------------------
def map_section_name(name, counters):
    name = name.lower()

    if "intro" in name:
        return "INTRO"

    if "outro" in name:
        return "OUTRO"

    if "verse" in name:
        counters["A"] += 1
        return f"A{counters['A']}"

    if "chorus" in name:
        counters["B"] += 1
        return f"B{counters['B']}"

    if "pre" in name or "bridge" in name:
        counters["PRE"] += 1
        return f"PRE {counters['PRE']}"

    if "instrumental" in name:
        counters["INST"] += 1
        return f"INST {counters['INST']}"

    return name.upper()


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
            "beat": beat["beatNum"]
        })

    return grid


# ---------------------------
# FIND ACTIVE CHORD
# ---------------------------
def find_chord_at_time(chords, time):
    for chord in chords:
        if chord["start"] <= time < chord["end"]:
            return chord
    return None


# ---------------------------
# BUILD SEGMENTS FROM BEATS
# ---------------------------
def build_segments(chords, beats):

    segments = []

    last_chord = None
    seg_start = None
    seg_bar = None
    seg_beat = None

    for beat in beats:

        chord = find_chord_at_time(chords, beat["start"])

        chord_name = None
        if chord:
            chord_name = pick_best_chord(chord)
            if chord.get("bass"):
                chord_name += "/" + chord["bass"]

        if chord_name != last_chord:

            if last_chord:
                segments.append({
                    "chord": last_chord,
                    "start_bar": seg_bar,
                    "start_beat": seg_beat
                })

            last_chord = chord_name
            seg_start = beat["start"]
            seg_bar = chord["start_bar"] if chord else 0
            seg_beat = beat["beat"]

    if last_chord:
        segments.append({
            "chord": last_chord,
            "start_bar": seg_bar,
            "start_beat": seg_beat
        })

    return segments


# ---------------------------
# BUILD SECTIONS
# ---------------------------
def build_sections(section_json):

    counters = {"A":0,"B":0,"PRE":0,"INST":0}

    result = []

    for s in section_json:
        label = map_section_name(s["label"], counters)

        result.append({
            "time": s["start"],
            "label": label
        })

    return result


# ---------------------------
# ADD SECTION TEXT
# ---------------------------
def add_section_text(measure, label):

    direction = SubElement(measure, "direction", placement="above")
    dt = SubElement(direction, "direction-type")
    words = SubElement(dt, "words", enclosure="rectangle")
    words.text = label


# ---------------------------
# MUSICXML BUILDER
# ---------------------------
def chords_to_musicxml(segments, sections, beats):

    score = Element("score-partwise", version="3.1")

    part_list = SubElement(score, "part-list")
    score_part = SubElement(part_list, "score-part", id="P1")
    SubElement(score_part, "part-name").text = "Chords"

    part = SubElement(score, "part", id="P1")

    first_beat = beats[0]["beat"]

    pickup_beats = first_beat - 1 if first_beat != 1 else 0

    measures = {}
    section_lookup = {s["time"]: s["label"] for s in sections}

    current_measure = None
    measure_num = 0

    for beat in beats:

        if beat["beat"] == 1 or current_measure is None:
            measure_num += 1
            current_measure = SubElement(part, "measure", number=str(measure_num))

            if measure_num == 1:
                attr = SubElement(current_measure, "attributes")

                SubElement(attr, "divisions").text = "1"

                key = SubElement(attr, "key")
                SubElement(key, "fifths").text = "0"

                time_el = SubElement(attr, "time")

                if pickup_beats:
                    SubElement(time_el, "beats").text = str(4 - pickup_beats)
                else:
                    SubElement(time_el, "beats").text = "4"

                SubElement(time_el, "beat-type").text = "4"

        # sections
        for sec_time, sec_label in section_lookup.items():
            if abs(sec_time - beat["start"]) < 0.2:
                add_section_text(current_measure, sec_label)

        # chords
        for seg in segments:
            if seg["start_beat"] == beat["beat"]:

                harmony = SubElement(current_measure, "harmony")

                step, alter, kind = parse_chord_for_xml(seg["chord"])

                root = SubElement(harmony, "root")
                SubElement(root, "root-step").text = step

                if alter:
                    SubElement(root, "root-alter").text = str(alter)

                SubElement(harmony, "kind").text = kind

                offset = SubElement(harmony, "offset")
                offset.text = str(beat["beat"] - 1)

    return tostring(score, encoding="utf-8", xml_declaration=True)


# ---------------------------
# FETCH ALL MUSIC.AI DATA
# ---------------------------
def fetch_music_ai(job_id):

    status_res = requests.get(
        f"https://api.music.ai/api/job/{job_id}",
        headers={"Authorization": API_KEY}
    )

    data = status_res.json()

    if data["status"] != "SUCCEEDED":
        return None

    chords = requests.get(data["result"]["chords"]).json()
    beats = requests.get(data["result"]["beats"]).json()
    sections = requests.get(data["result"]["sections"]).json()

    if isinstance(chords, dict):
        chords = chords["chords"]

    return chords, beats, sections


# ---------------------------
# ANALYZE
# ---------------------------
@app.route("/analyze", methods=["POST"])
def analyze():

    file = request.files["file"]

    upload_res = requests.get(
        "https://api.music.ai/v1/upload",
        headers={"Authorization": API_KEY}
    )

    up = upload_res.json()

    requests.put(
        up["uploadUrl"],
        data=file.read(),
        headers={"Content-Type": file.content_type}
    )

    job = requests.post(
        "https://api.music.ai/api/job",
        headers={
            "Authorization": API_KEY,
            "Content-Type": "application/json"
        },
        json={
            "workflow": WORKFLOW,
            "params": {"Input 1": up["downloadUrl"]}
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

    segments = build_segments(chords, beats)

    return jsonify({
        "status": "SUCCEEDED",
        "chart": segments
    })


# ---------------------------
# MUSICXML DOWNLOAD
# ---------------------------
@app.route("/musicxml/<job_id>")
def musicxml(job_id):

    data = fetch_music_ai(job_id)

    if not data:
        return jsonify({"error": "Processing"}), 400

    chords, beats, sections = data

    beat_grid = build_beat_grid(beats)
    segments = build_segments(chords, beat_grid)
    mapped_sections = build_sections(sections)

    xml = chords_to_musicxml(segments, mapped_sections, beat_grid)

    return Response(
        xml,
        mimetype="application/xml",
        headers={"Content-Disposition": "attachment; filename=chart.musicxml"}
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
