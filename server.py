from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

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
            return jsonify(job_data)

        return jsonify({
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

        # אם הצליח – נחזיר גם את ה-JSON של האקורדים
        if status_data["status"] == "SUCCEEDED":

            chords_url = status_data["result"].get("chords")

            if chords_url:
                chords_res = requests.get(chords_url)
                chords_json = chords_res.json()

                return jsonify({
                    "status": "SUCCEEDED",
                    "chords": chords_json
                })

        return jsonify(status_data)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


print("REGISTERED ROUTES:")
print(app.url_map)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
