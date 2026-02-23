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

# ---------------------------------------------------
# HEALTH CHECK (CRITICAL FOR RENDER)
# ---------------------------------------------------
@app.route("/")
def health():
    return "OK", 200


# ---------------------------------------------------
# BPM SCALING
# ---------------------------------------------------
def apply_bpm_scaling(beats, chords, detected_bpm, manual_bpm):
    try:
        detected_bpm = float(detected_bpm)
        manual_bpm = float(manual_bpm)
    except:
        return beats, chords

    if detected_bpm <= 0 or manual_bpm <= 0:
        return beats, chords

    scale = detected_bpm / manual_bpm

    if beats:
        for b in beats:
            if "time" in b and b["time"] is not None:
                b["time"] = b["time"] / scale

    if chords:
        for c in chords:
            if "start" in c and c["start"] is not None:
                c["start"] = c["start"] / scale
            if "end" in c and c["end"] is not None:
                c["end"] = c["end"] / scale

    return beats, chords


# ---------------------------------------------------
# TIME SIGNATURE DETECTION
# ---------------------------------------------------
def detect_time_signature(beats):
    if not beats:
        return 4, 4

    counts = []
    current = 0

    for b in beats:
        if b["beatNum"] == 1:
            if current > 0:
                counts.append(current)
            current = 1
        else:
            current += 1

    if current > 0:
        counts.append(current)

    if not counts:
        return 4, 4

    beats_per_bar = max(set(counts), key=counts.count)

    if beats_per_bar in (6, 9, 12):
        return beats_per_bar, 8

    return beats_per_bar, 4


# ---------------------------------------------------
# CHORD PICKER
# ---------------------------------------------------
def pick_best_chord(c):
    chord = (
        c.get("chord_complex_pop")
        or c.get("chord_simple_pop")
        or c.get("chord_basic_pop")
    )
    if chord in (None, "N"):
        return None
    return chord


# ---------------------------------------------------
# CREATE JOB
# ---------------------------------------------------
@app.route("/analyze", methods=["POST"])
def analyze():

    try:
        if "file" not in request.files:
            return jsonify({"error": "No file field named 'file' in form-data"}), 400

        file = request.files["file"]
        manual_bpm = request.form.get("bpm_override")

        if not API_KEY:
            return jsonify({"error": "API_KEY environment variable is not set"}), 500

        upload_res = requests.get(
            "https://api.music.ai/v1/upload",
            headers={"Authorization": API_KEY},
            timeout=20
        )

        if upload_res.status_code != 200:
            return jsonify({
                "error": "Failed to get upload URL from music.ai",
                "status_code": upload_res.status_code,
                "response": upload_res.text
            }), 502

        upload_data = upload_res.json()
        upload_url = upload_data.get("uploadUrl")
        download_url = upload_data.get("downloadUrl")

        put_res = requests.put(
            upload_url,
            data=file.read(),
            headers={"Content-Type": file.content_type},
            timeout=60
        )

        if put_res.status_code not in (200, 201):
            return jsonify({
                "error": "Failed to upload file to music.ai storage",
                "status_code": put_res.status_code,
                "response": put_res.text
            }), 502

        params = {"Input 1": download_url}
        if manual_bpm:
            params["manual_bpm"] = manual_bpm

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
                "params": params
            },
            timeout=20
        )

        if job_res.status_code != 200:
            return jsonify({
                "error": "Failed to create job in music.ai",
                "status_code": job_res.status_code,
                "response": job_res.text
            }), 502

        return jsonify({"job_id": job_res.json().get("id")})

    except Exception as e:
        return jsonify({"error": "Unexpected server error", "details": str(e)}), 500


# ---------------------------------------------------
# FETCH ANALYSIS
# ---------------------------------------------------
def fetch_analysis(job_id):

    status_res = requests.get(
        f"https://api.music.ai/api/job/{job_id}",
        headers={"Authorization": API_KEY},
        timeout=20
    )

    status_data = status_res.json()

    if status_data["status"] != "SUCCEEDED":
        return None, None, None, None, None, None, status_data["status"]

    result = status_data["result"]

    chords_url = result.get("chords") or result.get("Chords")
    beats_url = result.get("Beats") or result.get("beats")

    chords_json = requests.get(chords_url, timeout=20).json()
    beats = requests.get(beats_url, timeout=20).json() if beats_url else None

    return chords_json, None, beats, None, None, None, "SUCCEEDED"


# ---------------------------------------------------
# STATUS ROUTE
# ---------------------------------------------------
@app.route("/status/<job_id>")
def status(job_id):

    chords, _, beats, _, _, _, state = fetch_analysis(job_id)

    if chords is None:
        return jsonify({"status": state})

    return jsonify({
        "status": "SUCCEEDED",
        "chart": chords,
        "beats": beats
    })


# ---------------------------------------------------
# DEV SERVER
# ---------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)