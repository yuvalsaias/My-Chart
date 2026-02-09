from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os

app = Flask(__name__)

# ✅ הפעלת CORS לכל הדומיינים
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

    # כרגע משתמשים ב-demo URL
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
        print("JOB CREATED:", job_data)

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

        # ✅ אם הסתיים בהצלחה
        if status_data.get("status") == "SUCCEEDED":

            chords_url = status_data.get("result", {}).get("chords")

            if chords_url:
                chords_res = requests.get(chords_url)
                chords_json = chords_res.json()

                return jsonify({
                    "status": "SUCCEEDED",
                    "chords": chords_json
                })

        # ✅ אם עדיין בתהליך
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
