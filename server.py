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
# CHORD PARSER
# ---------------------------
def parse_chord_for_xml(chord):

    if "/" in chord:
        chord = chord.split("/")[0]

    match = re.match(r"^([A-G])([#b]?)(.*)$", chord)

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
    elif quality.startswith("m"):
        kind = "minor"
    elif "7" in quality:
        kind = "dominant"
    else:
        kind = "major"

    return step, alter, kind


# ---------------------------
# SEGMENTS FROM JSON
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

            harmony = SubElement(measure, "harmony")

            step, alter, kind = parse_chord_for_xml(seg["chord"])

            root = SubElement(harmony, "root")
            SubElement(root, "root-step").text = step

            if alter:
                SubElement(root, "root-alter").text = str(alter)

            SubElement(harmony, "kind").text = kind

            offset = SubElement(harmony, "offset")
            offset.text = str(seg["start_beat"] - 1)

    return tostring(score, encoding="utf-8", xml_declaration=True)


# ---------------------------
# CREATE JOB
# ---------------------------
@app.route("/analyze", methods=["POST"])
def analyze():

    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400

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

    return jsonify({
        "status": "CREATED",
        "job_id": job_data["id"]
    })


# ---------------------------
# FETCH CHORDS
# ---------------------------
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


# ---------------------------
# STATUS ROUTE (BASE44 NEEDS THIS)
# ---------------------------
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


# ---------------------------
# MUSICXML DOWNLOAD
# ---------------------------
@app.route("/musicxml/<job_id>")
def musicxml(job_id):

    chords, state = fetch_chords(job_id)

    if chords is None:
        return jsonify({"error": "Processing"}), 400

    segments = build_segments(chords)

    xml_data = chords_to_musicxml(segments)

    return Response(
        xml_data,
        mimetype="application/xml",
        headers={"Content-Disposition": "attachment; filename=chords.musicxml"}
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
