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
    chord = (
        c.get("chord_complex_pop")
        or c.get("chord_simple_pop")
        or c.get("chord_basic_pop")
    )

    # מתעלמים מ-N (no chord)
    if chord in (None, "N"):
        return None

    return chord


# ---------------------------
# PARSE CHORD FOR MUSICXML
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
            return None

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

    except Exception as e:
        print("CHORD PARSE ERROR:", chord, e)
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

        seg = {
            "chord": chord,
            "start_bar": c["start_bar"],
            "start_beat": c["start_beat"],
            "end_bar": c["end_bar"],
            "end_beat": c["end_beat"],
        }

        if "start" in c:
            seg["start_sec"] = c["start"]

        segments.append(seg)

    return segments


# ---------------------------
# DETECT PICKUP (קדמה)
# ---------------------------
def detect_pickup_beats(chords):
    """
    מזהה כמה פעמות חסרות בתחילת השיר (קדמה)
    לפי האקורד הראשון: pickup_beats = start_beat - 1
    """
    if not chords:
        return 0

    first = min(chords, key=lambda c: (c["start_bar"], c["start_beat"]))
    start_beat = first.get("start_beat", 1)
    pickup_beats = max(0, start_beat - 1)
    return pickup_beats


# ---------------------------
# QUANTIZE SEGMENTS TO BEATS (אחרי הקדמה)
# ---------------------------
def quantize_segments_to_beats(segments, beats, pickup_beats):
    """
    מיישר את תחילת האקורדים לפעמות לפי Beats.json,
    אבל לא נוגע באקורדים שבתיבת הקדמה (start_bar == 0).
    """
    if not beats:
        return segments

    for seg in segments:
        # לא נוגעים בקדמה
        if seg["start_bar"] == 0:
            continue

        t = seg.get("start_sec")
        if t is None:
            continue

        closest = min(beats, key=lambda b: abs((b.get("time") or 0) - t))
        seg["start_beat"] = closest["beatNum"]

    return segments


# ---------------------------
# MAP SECTIONS TO BARS
# ---------------------------
def map_sections_to_bars(sections, chords):
    """
    ממפה כל section (start בשניות) לתיבה (start_bar)
    על בסיס זמן ההתחלה של האקורדים ב-chords.json.
    """
    if not sections or not chords:
        return None

    mapped = []

    for sec in sections:
        sec_start = sec.get("start")
        if sec_start is None:
            continue

        candidates = [
            c for c in chords
            if c.get("start") is not None and c["start"] >= sec_start
        ]

        if not candidates:
            continue

        first = min(candidates, key=lambda c: c["start"])
        bar = first.get("start_bar")
        if bar is None:
            continue

        mapped.append({
            "label": sec.get("label") or "Section",
            "start_bar": bar
        })

    return mapped


# ---------------------------
# MUSICXML BUILDER
# ---------------------------
def chords_to_musicxml(segments, sections=None, bpm=None, pickup_beats=0):

    score = Element("score-partwise", version="3.1")

    part_list = SubElement(score, "part-list")
    score_part = SubElement(part_list, "score-part", id="P1")
    SubElement(score_part, "part-name").text = "Chords"

    part = SubElement(score, "part", id="P1")

    # כל התיבות שיש בהן אקורדים
    bars = sorted(set(s["start_bar"] for s in segments))

    # ---------------------------
    # MEASURE 0 — PICKUP
    # ---------------------------
    measure0 = SubElement(part, "measure", number="0")

    attributes0 = SubElement(measure0, "attributes")
    SubElement(attributes0, "divisions").text = "1"

    time0 = SubElement(attributes0, "time")
    SubElement(time0, "beats").text = "4"
    SubElement(time0, "beat-type").text = "4"

    ms = SubElement(attributes0, "measure-style")
    ms.set("type", "pickup")

    # טמפו בתיבת הקדמה
    if bpm is not None:
        direction = SubElement(measure0, "direction", placement="above")
        direction_type = SubElement(direction, "direction-type")
        metronome = SubElement(direction_type, "metronome")
        SubElement(metronome, "beat-unit").text = "quarter"
        SubElement(metronome, "per-minute").text = str(bpm)
        sound = SubElement(direction, "sound")
        sound.set("tempo", str(bpm))

    # ---------------------------
    # REGULAR MEASURES (1,2,3...)
    # ---------------------------
    for bar in bars:
        # את הקדמה כבר טיפלנו ב-measure 0
        if bar == 0:
            continue

        # מספר התיבה ב-MusicXML = bar (כי bar 0 → measure 0, bar 1 → measure 1 וכו')
        measure = SubElement(part, "measure", number=str(bar))

        # כותרות Sections
        if sections:
            for sec in sections:
                if sec["start_bar"] == bar:
                    direction = SubElement(measure, "direction", placement="above")
                    direction_type = SubElement(direction, "direction-type")
                    rehearsal = SubElement(direction_type, "rehearsal")
                    rehearsal.text = sec["label"]

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


# ---------------------------
# CREATE JOB (/analyze)
# ---------------------------
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


# ---------------------------
# FETCH ANALYSIS (CHORDS + SECTIONS + BEATS + BPM)
# ---------------------------
def fetch_analysis(job_id):

    status_res = requests.get(
        f"https://api.music.ai/api/job/{job_id}",
        headers={"Authorization": API_KEY}
    )

    status_data = status_res.json()

    if status_data["status"] != "SUCCEEDED":
        return None, None, None, None, status_data["status"]

    result = status_data["result"]

    chords_url = result.get("chords") or result.get("Chords")
    beats_url = result.get("Beats") or result.get("beats")
    sections_url = result.get("Sections") or result.get("sections")
    bpm_val = result.get("Bpm") or result.get("bpm")

    # --- chords ---
    chords_json = requests.get(chords_url).json()
    if isinstance(chords_json, dict):
        chords = chords_json.get("chords", chords_json)
    else:
        chords = chords_json

    # --- beats ---
    beats = requests.get(beats_url).json() if beats_url else None

    # --- sections ---
    sections = requests.get(sections_url).json() if sections_url else None

    # --- bpm ---
    bpm = None
    if bpm_val is not None:
        try:
            bpm = float(bpm_val)
        except Exception:
            bpm = None

    return chords, sections, beats, bpm, "SUCCEEDED"


# ---------------------------
# STATUS ROUTE (/status/<job_id>)
# ---------------------------
@app.route("/status/<job_id>")
def status(job_id):

    chords, sections, beats, bpm, state = fetch_analysis(job_id)

    if chords is None:
        return jsonify({"status": state})

    segments = build_segments(chords)

    response = {
        "status": "SUCCEEDED",
        "chart": segments
    }

    if sections is not None:
        response["sections"] = sections

    if bpm is not None:
        response["bpm"] = bpm

    return jsonify(response)


# ---------------------------
# MUSICXML DOWNLOAD (/musicxml/<job_id>)
# ---------------------------
@app.route("/musicxml/<job_id>")
def musicxml(job_id):

    chords, sections, beats, bpm, state = fetch_analysis(job_id)

    if chords is None:
        return jsonify({"error": "Processing"}), 400

    segments = build_segments(chords)

    pickup_beats = detect_pickup_beats(chords)

    segments = quantize_segments_to_beats(segments, beats, pickup_beats)

    mapped_sections = map_sections_to_bars(sections, chords) if sections else None

    xml_data = chords_to_musicxml(segments, mapped_sections, bpm, pickup_beats)

    return Response(
        xml_data,
        mimetype="application/xml",
        headers={"Content-Disposition": "attachment; filename=chords.musicxml"}
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
