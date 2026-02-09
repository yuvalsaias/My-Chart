from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os

app = Flask(__name__)

# CORS
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
# CREATE JOB
# ---------------------------
@app.route("/analyze", methods=["POST"])
def analyze():

    print("=== ANALYZE STARTED ===")

    if "file" not in request.files:
        return jsonify({"error": "No file received"}), 400

    file = request.files["file"]
    print("Received file:", file.filename)

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
            return jsonify({"error": "Job creation failed", "data": job_data}), 500

        return jsonify({
            "status": "CREATED",
            "job_id": job_data["id"]
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------
# CHECK JOB STATUS
# ---------------------------
@app.route("/status/<job_id>")
def status(job_id):

    try:

        status_res = requests.get(
            f"https://api.music.ai/api/job/{job_id}",
            headers={"Authorization": API_KEY}
        )

        status_data = status_res.json()

        # ---------------------------
        # JOB FINISHED
        # ---------------------------
        if status_data.get("status") == "SUCCEEDED":

            chords_url = status_data.get("result", {}).get("chords")

            if not chords_url:
                return jsonify({"status": "SUCCEEDED", "chart": []})

            chords_res = requests.get(chords_url)
            chords_json = chords_res.json()

            # -------- FIX PARSER --------

            # Music.ai לפעמים מחזיר dict ולפעמים list
            if isinstance(chords_json, dict):
                chords_list = chords_json.get("chords", [])
            elif isinstance(chords_json, list):
                chords_list = chords_json
            else:
                chords_list = []

            parsed_chords = []

            for c in chords_list:

                chord = c.get("chord_complex_pop")
                bass = c.get("bass")

                if chord:
                    if bass:
                        chord = f"{chord}/{bass}"

                    parsed_chords.append({
                        "time": c.get("start"),
                        "bar": c.get("start_bar"),
                        "beat": c.get("start_beat"),
                        "chord": chord
                    })

            return jsonify({
                "status": "SUCCEEDED",
                "chart": parsed_chords,
                "raw": chords_json
            })

        # ---------------------------
        # עדיין בתהליך
        # ---------------------------
        return jsonify({
            "status": status_data.get("status", "UNKNOWN")
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


print("REGISTERED ROUTES:")
print(app.url_map)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
