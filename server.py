from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import requests
import os
from xml.etree.ElementTree import Element, SubElement, tostring

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

API_KEY = os.environ.get("API_KEY")
WORKFLOW = "my-chart-recognizer"


# ---------------------------
# TEST ROUTE
# ---------------------------
@app.route("/test")
def test():
    return "Server works!"


# ---------------------------
# MUSICXML BUILDER
# ---------------------------
def parse_chord_for_xml(chord_name):

    # root + accidental
    root = chord_name[0]
    alter = None

    if len(chord_name) > 1 and chord_name[1] == "#":
        alter = 1
    elif len(chord_name) > 1 and chord_name[1] == "b":
        alter = -1

    # זיהוי סוג האקורד
    if "min" in chord_name or "m" in chord_name:
        kind = "minor"
    elif "7" in chord_name:
        kind = "dominant"
    else:
        kind = "major"

    return root, alter, kind


def chords_to_musicxml(parsed_chords):

    score = Element("score-partwise", version="3.1")

    part_list = SubElement(score, "part-list")
    score_part = SubElement(part_list, "score-part", id="P1")
    SubElement(score_part, "part-name").text = "Chords"

    part = SubElement(score, "part", id="P1")

    current_bar = -1
    measure = None

    for chord_data in parsed_chords:

        bar = chord_data.get("bar", 0)
        chord_name = chord_data.get("chord")

        if not chord_name:
            continue

        # פתיחת measure חדשה
        if bar != current_bar:
            current_bar = bar
            measure = SubElement(part, "measure", number=str(bar + 1))

        harmony = SubElement(measure, "harmony")

        root_step, alter, kind = parse_chord_for_xml(chord_name)

        root = SubElement(harmony, "root")
        SubElement(root, "root-step").text = root_step

        if alter:
            SubElement(root, "root-alter").text = str(alter)

        SubElement(harmony, "kind").text = kind

    return tostring(score, encoding="utf-8", xml_declaration=True)


# ---------------------------
# CREATE JOB
# ---------------------------
@app.route("/analyze", methods=["POST"])
def analyze():

    if "file" not in request.files:
        return jsonify({"error": "No file received"}), 400

    # כרגע demo
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
# GET CHORD JSON
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

    parsed = []

    for c in chords_list:

        chord = c.get("chord_complex_pop")
        bass = c.get("bass")

        if chord:
            if bass:
                chord = f"{chord}/{bass}"

            parsed.append({
                "time": c.get("start"),
                "bar": c.get("start_bar"),
                "beat": c.get("start_beat"),
                "chord": chord
            })

    return parsed


# ---------------------------
# STATUS
# ---------------------------
@app.route("/status/<job_id>")
def status(job_id):

    parsed = fetch_parsed_chords(job_id)

    if parsed is None:
        return jsonify({"status": "PROCESSING"})

    return jsonify({
        "status": "SUCCEEDED",
        "chart": parsed
    })


# ---------------------------
# DOWNLOAD MUSICXML
# ---------------------------
@app.route("/musicxml/<job_id>")
def musicxml(job_id):

    parsed = fetch_parsed_chords(job_id)

    if not parsed:
        return jsonify({"error": "Job not finished"}), 400

    xml_data = chords_to_musicxml(parsed)

    return Response(
        xml_data,
        mimetype="application/xml",
        headers={
            "Content-Disposition": "attachment; filename=chords.musicxml"
        }
    )


print("REGISTERED ROUTES:")
print(app.url_map)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
