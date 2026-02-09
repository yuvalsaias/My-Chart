from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os

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
# CREATE JOB
# ---------------------------
@app.route("/analyze", methods=["POST"])
def analyze():

    if "file" not in request.files:
        return jsonify({"error": "No file received"}), 400

    file = request.files["file"]

    try:
        # ---------------------------
        # STEP 1 - get upload URL
        # ---------------------------
        upload_res = requests.get(
            "https://api.music.ai/v1/upload",
            headers={"Authorization": API_KEY}
        )

        upload_data = upload_res.json()

        upload_url = upload_data["uploadUrl"]
        download_url = upload_data["downloadUrl"]

        # ---------------------------
        # STEP 2 - upload file bytes
        # ---------------------------
        file_bytes = file.read()

        requests.put(
            upload_url,
            data=file_bytes,
            headers={"Content-Type": file.content_type}
        )

        # ---------------------------
        # STEP 3 - create job
        # ---------------------------
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
                "params": {
                    "Input 1": download_url
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
# STATUS
# ---------------------------
@app.route("/status/<job_id>")
def status(job_id):

    try:
        status_res = requests.get(
            f"https://api.music.ai/api/job/{job_id}",
            headers={"Authorization": API_KEY}
        )

        status_data = status_res.json()

        if status_data.get("status") == "SUCCEEDED":

            chords_url = status_data.get("result", {}).get("chords")

            chords_res = requests.get(chords_url)
            chords_json = chords_res.json()

            return jsonify({
                "status": "SUCCEEDED",
                "chords": chords_json
            })

        return jsonify({
            "status": status_data.get("status")
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
