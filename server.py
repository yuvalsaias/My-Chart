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
# ANALYZE
# ---------------------------
@app.route("/analyze", methods=["POST"])
def analyze():

    try:

        if not API_KEY:
            return jsonify({"error": "Missing API_KEY"}), 500

        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files["file"]

        file.stream.seek(0)
        file_data = file.read()

        if len(file_data) == 0:
            return jsonify({"error": "Empty file"}), 400

        # Upload URL
        upload_res = requests.get(
            "https://api.music.ai/v1/upload",
            headers={"Authorization": API_KEY}
        )

        upload_data = upload_res.json()

        if "uploadUrl" not in upload_data:
            return jsonify(upload_data), 500

        # Upload file
        put_res = requests.put(
            upload_data["uploadUrl"],
            data=file_data,
            headers={"Content-Type": file.content_type or "audio/mpeg"}
        )

        if put_res.status_code not in [200, 201]:
            return jsonify({"error": "Upload failed"}), 500

        # Create job
        job_res = requests.post(
            "https://api.music.ai/v1/job",
            headers={
                "Authorization": API_KEY,
                "Content-Type": "application/json"
            },
            json={
                "workflow": WORKFLOW,
                "params": {"Input 1": upload_data["downloadUrl"]}
            }
        )

        job_data = job_res.json()

        if "id" not in job_data:
            return jsonify(job_data), 500

        return jsonify({"job_id": job_data["id"]})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------
# SAFE FETCH
# ---------------------------
def safe_fetch(url):

    try:
        if not url:
            return None
        return requests.get(url).json()
    except:
        return None


# ---------------------------
# FETCH MUSIC.AI
# ---------------------------
def fetch_music_ai(job_id):

    try:

        status = requests.get(
            f"https://api.music.ai/v1/job/{job_id}",
            headers={"Authorization": API_KEY}
        ).json()

        if status.get("status") != "SUCCEEDED":
            return None

        result = status.get("result", {})

        chords = safe_fetch(result.get("chords"))
        beats = safe_fetch(result.get("beats"))

        if isinstance(chords, dict):
            chords = chords.get("chords", [])

        if isinstance(beats, dict):
            beats = beats.get("beats", [])

        return chords or [], beats or []

    except Exception as e:
        print("FETCH ERROR:", e)
        return None


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
        end = beats[i + 1]["time"] if i < len(beats) - 1 else start + 0.5

        grid.append({
            "start": start,
            "end": end,
            "beat": beat.get("beatNum", 1),
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
# CHORD PARSER
# ---------------------------
def parse_chord_for_xml(chord):

    chord = chord.replace("Δ", "maj").replace("-", "m")

    if "/" in chord:
        chord = chord.split("/")[0]

    m = re.match(r"^([A-G])([#b]?)(.*)$", chord)

    if not m:
        return "C", None, "major"

    step, accidental, quality = m.groups()

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
def chords_to_musicxml(segments):

    divisions = 4

    score = Element("score-partwise", version="3.1")

    part_list = SubElement(score, "part-list")
    score_part = SubElement(part_list, "score-part", id="P1")
    SubElement(score_part, "part-name").text = "Chords"

    part = SubElement(score, "part", id="P1")

    bars = sorted(set(s["start_bar"] for s in segments))

    for i, bar in enumerate(bars):

        measure = SubElement(part, "measure", number=str(bar + 1))

        if i == 0:

            attr = SubElement(measure, "attributes")
            SubElement(attr, "divisions").text = str(divisions)

            key = SubElement(attr, "key")
            SubElement(key, "fifths").text = "0"

            time = SubElement(attr, "time")
            SubElement(time, "beats").text = "4"
            SubElement(time, "beat-type").text = "4"

        for seg in [s for s in segments if s["start_bar"] == bar]:

            harmony = SubElement(measure, "harmony")

            step, alter, kind = parse_chord_for_xml(seg["chord"])

            root = SubElement(harmony, "root")
            SubElement(root, "root-step").text = step

            if alter is not None:
                SubElement(root, "root-alter").text = str(alter)

            SubElement(harmony, "kind").text = kind

            offset = SubElement(harmony, "offset")
            offset.text = str((seg["start_beat"] - 1) * divisions)

    return tostring(score, encoding="utf-8", xml_declaration=True)


# ---------------------------
# STATUS
# ---------------------------
@app.route("/status/<job_id>")
def status(job_id):

    try:

        data = fetch_music_ai(job_id)

        if not data:
            return jsonify({"status": "PROCESSING", "chart": []})

        chords, beats = data

        if not beats or not chords:
            return jsonify({"status": "PROCESSING", "chart": []})

        beat_grid = build_beat_grid(beats)
        segments = build_segments(chords, beat_grid)

        return jsonify({
            "status": "SUCCEEDED",
            "chart": segments or []
        })

    except Exception as e:
        print("STATUS ERROR:", e)
        return jsonify({"status": "ERROR", "chart": []})


# ---------------------------
# MUSICXML
# ---------------------------
@app.route("/musicxml/<job_id>")
def musicxml(job_id):

    try:

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

    except Exception as e:
        print("XML ERROR:", e)
        return jsonify({"error": "XML generation failed"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
