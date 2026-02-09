from flask import Flask, request, jsonify
import requests
import time

app = Flask(__name__)

API_KEY = "5bbe2a00-20ff-44b6-833d-12adefcfc8ef"
WORKFLOW = "my-chart-recognizer"


# ---------------------------
# TEST ROUTE
# ---------------------------
@app.route("/test")
def test():
    return "Server works!"


# ---------------------------
# ANALYZE ROUTE
# ---------------------------
@app.route("/analyze", methods=["POST"])
def analyze():

    print("=== ANALYZE STARTED ===")

    if "file" not in request.files:
        return jsonify({"error": "No file received"}), 400

    file = request.files["file"]
    print("Received file:", file.filename)

    # ⚠️ כרגע משתמשים ב-demo URL כדי לוודא שה-workflow עובד
    audio_url = "https://music.ai/demo.ogg"

    try:

        # ---------------------------
        # STEP 1 - create job
        # ---------------------------
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

        print("JOB RESPONSE:", job_res.text)

        job_data = job_res.json()

        if "id" not in job_data:
            return jsonify(job_data)

        job_id = job_data["id"]
        print("JOB CREATED:", job_id)

        # ---------------------------
        # STEP 2 - polling
        # ---------------------------
        while True:

            status_res = requests.get(
                f"https://api.music.ai/api/job/{job_id}",
                headers={"Authorization": API_KEY}
            )

            status_data = status_res.json()
            print("JOB STATUS:", status_data)

            if status_data["status"] == "completed":
                return jsonify(status_data)

            if status_data["status"] == "failed":
                return jsonify(status_data)

            time.sleep(3)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------
# DEBUG ROUTES
# ---------------------------
print("REGISTERED ROUTES:")
print(app.url_map)


if __name__ == "__main__":
    app.run(port=5000, debug=True)
