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
# CHORD PARSER
# ---------------------------
def parse_chord_for_xml(chord):

    try:
        original = chord

        bass_note = None
        if "/" in chord:
            chord, bass_note = chord.split("/")

        chord = chord.replace("-", "m")

        match = re.match(r"^([A-G])([#b]?)(.*)$", chord)

        if not match:
            return None   # ⭐ אין fallback ל-C

        step, accidental, quality = match.groups()

        alter = None
        if accidental == "#":
            alter = 1
        elif accidental == "b":
            alter = -1

        q = quality.lower()

        if "m7b5" in q or "ø" in q:
            kind = "half-diminished"
        elif "dim" in q:
            kind = "diminished"
        elif "aug" in q or "+" in q:
            kind = "augmented"
        elif "maj7" in q or "Δ" in quality:
            kind = "major-seventh"
        elif "m7" in q:
            kind = "minor-seventh"
        elif "7" in q:
            kind = "dominant"
        elif q.startswith("m"):
            kind = "minor"
        elif "sus2" in q:
            kind = "suspended-second"
        elif "sus" in q:
            kind = "suspended-fourth"
        else:
            kind = "major"

        degrees = []

        def add_degree(val, dtype="add", alter_val=None):
            degrees.append((str(val), dtype, alter_val))

        if "b5" in q:
            add_degree(5, "alter", "-1")

        if "#5" in q:
            add_degree(5, "alter", "1")

        if "b9" in q:
            add_degree(9, "alter", "-1")

        if "#9" in q:
            add_degree(9, "alter", "1")

        if "11" in q:
            add_degree(11)

        if "13" in q:
            add_degree(13)

        if "9" in q and "b9" not in q and "#9" not in q:
            add_degree(9)

        return step, alter, kind, degrees, bass_note, original

    except:
        return None


# ---------------------------
# BUILD SEGMENTS
# ---------------------------
def build_segments(chords_list):

    segments = []

    for c in chords_list:

        chord = pick_best_chord(c)
        bass = c.get("bass")

        if not chord:
            continue

        if bass:
            chord = f"{chord}/{bass}"

        segments.append({
            "chord": chord,
            "start_bar": c["start_bar"],
            "start_beat": c["start_beat"],
            "end_bar": c["end_bar"],
            "end_beat": c["end_beat"]
        })

    return segments


# ---------------------------
# BUILD HARMONIC TIMELINE
# ---------------------------
def build_harmonic_timeline(segments):

    segments = sorted(
        segments,
        key=lambda s: (s["start_bar"], s["start_beat"])
    )

    timeline = []

    for i, seg in enumerate(segments):

        new_seg = seg.copy()

        if i < len(segments) - 1:
            next_seg = segments[i + 1]
            new_seg["end_bar"] = next_seg["start_bar"]
            new_seg["end_beat"] = next_seg["start_beat"]

        timeline.append(new_seg)

    return timeline


# ---------------------------
# EXPAND SEGMENTS ACROSS BARS
# ---------------------------
def expand_segments_across_bars(segments):

    expanded = []

    for seg in segments:

        for bar in range(seg["start_bar"], seg["end_bar"] + 1):

            new_seg = seg.copy()
            new_seg["start_bar"] = bar

            if bar == seg["start_bar"]:
                new_seg["start_beat"] = seg["start_beat"]
            else:
                new_seg["start_beat"] = 1

            # מניעת כפילויות
            if not any(
                s["start_bar"] == new_seg["start_bar"]
                and s["start_beat"] == new_seg["start_beat"]
                for s in expanded
            ):
                expanded.append(new_seg)

    return expanded


# ---------------------------
# MUSICXML BUILDER
# ---------------------------
def chords_to_musicxml(segments):

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

            SubElement(attributes, "divisions").text = "1"

            key = SubElement(attributes, "key")
            SubElement(key, "fifths").text = "0"

            time = SubElement(attributes, "time")
            SubElement(time, "beats").text = "4"
            SubElement(time, "beat-type").text = "4"

        bar_segments = [s for s in segments if s["start_bar"] == bar]

        for seg in bar_segments:

            parsed = parse_chord_for_xml(seg["chord"])
            if not parsed:
                continue

            step, alter, kind, degrees, bass_note, original = parsed

            harmony = SubElement(measure, "harmony")

            root = SubElement(harmony, "root")
            SubElement(root, "root-step").text = step

            if alter is not None:
                SubElement(root, "root-alter").text = str(alter)

            kind_el = SubElement(harmony, "kind")
            kind_el.text = kind
            kind_el.set("text", original)

            if bass_note:
                bass = SubElement(harmony, "bass")
                SubElement(bass, "bass-step").text = bass_note[0]

            for value, dtype, alter_val in degrees:

                degree = SubElement(harmony, "degree")
                SubElement(degree, "degree-value").text = value
                SubElement(degree, "degree-type").text = dtype

                if alter_val is not None:
                    SubElement(degree, "degree-alter").text = alter_val

            offset = SubElement(harmony, "offset")
            offset.text = str(seg["start_beat"] - 1)

    return tostring(score, encoding="utf-8", xml_declaration=True)


@app.route("/analyze", methods=["POST"])
def analyze():

    file = request.files["file"]

    upload_res = requests.get(
        "https://api.music.ai/v1/upload",
        headers={"Authorization": API_KEY}
    )

    upload_data = upload_res.json()

    upload_url = upload_data["uploadUrl"]
    download_url = upload_data["downloadUrl"]

    requests.put(
        upload_url,
        data=file.read(),
        headers={"Content-Type": file.content_type}
    )

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
            "params": {"Input 1": download_url}
        }
    )

    job_data = job_res.json()

    return jsonify({"job_id": job_data["id"]})


def fetch_chords(job_id):

    status_res = requests.get(
        f"https://api.music.ai/api/job/{job_id}",
        headers={"Authorization": API_KEY}
    )

    status_data = status_res.json()

    if status_data["status"] != "SUCCEEDED":
        return None, status_data["status"]

    chords_url = status_data["result"]["chords"]

    chords_json = requests.get(chords_url).json()

    if isinstance(chords_json, dict):
        return chords_json["chords"], "SUCCEEDED"

    return chords_json, "SUCCEEDED"


@app.route("/status/<job_id>")
def status(job_id):

    chords, state = fetch_chords(job_id)

    if chords is None:
        return jsonify({"status": state})

    segments = build_segments(chords)

    return jsonify({
        "status": "SUCCEEDED",
        "chart": segments
    })


@app.route("/musicxml/<job_id>")
def musicxml(job_id):

    chords, state = fetch_chords(job_id)

    if chords is None:
        return jsonify({"error": "Processing"}), 400

    segments = build_segments(chords)
    segments = build_harmonic_timeline(segments)
    segments = expand_segments_across_bars(segments)

    xml_data = chords_to_musicxml(segments)

    return Response(
        xml_data,
        mimetype="application/xml",
        headers={"Content-Disposition": "attachment; filename=chords.musicxml"}
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
