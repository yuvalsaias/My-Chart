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
# DETECT TIME SIGNATURE
# ---------------------------
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


# ---------------------------
# PICK BEST CHORD
# ---------------------------
def pick_best_chord(c):

    chord = (
        c.get("chord_complex_pop")
        or c.get("chord_simple_pop")
        or c.get("chord_basic_pop")
    )

    if chord in (None, "N"):
        return None

    return chord


# ---------------------------
# BUILD SEGMENTS (FOR XML)
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
# BUILD TIMELINE SEGMENTS (NEW)
# ---------------------------
def build_timeline_segments(chords_list):

    timeline = []

    for c in chords_list:

        chord = pick_best_chord(c)
        bass = c.get("bass")

        if not chord:
            continue

        if bass:
            chord = f"{chord}/{bass}"

        start = c.get("start")
        end = c.get("end")

        if start is None or end is None:
            continue

        timeline.append({
            "chord": chord,
            "start": start,
            "end": end
        })

    return timeline


# ---------------------------
# QUANTIZE SEGMENTS TO BEATS
# ---------------------------
def quantize_segments_to_beats(segments, beats):

    if not beats:
        return segments

    for seg in segments:
        t = seg.get("start_sec")
        if t is None:
            continue

        closest = min(
            beats,
            key=lambda b: abs((b.get("time") or 0) - t)
        )

        seg["start_beat"] = closest["beatNum"]

    return segments


# ---------------------------
# MAP SECTIONS TO BARS
# ---------------------------
def map_sections_to_bars(sections, chords):
    if not sections or not chords:
        return []

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
            "label": sec.get("label", "Section"),
            "start_bar": bar
        })

    # מניעת כפילויות רצופות
    filtered = []
    last_label = None
    for sec in mapped:
        if sec["label"] != last_label:
            filtered.append(sec)
            last_label = sec["label"]

    return filtered



# ---------------------------
# MUSICXML BUILDER (UNCHANGED)
# ---------------------------
def chords_to_musicxml(segments, sections=None, bpm=None, beats=None):
    score = Element("score-partwise", version="3.1")

    part_list = SubElement(score, "part-list")
    score_part = SubElement(part_list, "score-part", id="P1")
    SubElement(score_part, "part-name").text = "Chords"

    part = SubElement(score, "part", id="P1")

    min_bar = min(s["start_bar"] for s in segments)
    max_bar = max(s["end_bar"] for s in segments)
    bars = list(range(min_bar, max_bar + 1))

    beats_per_bar, beat_type = detect_time_signature(beats)

    # --- מיפוי sections לפי תיבה ---
    sections_by_bar = {}
    if sections:
        for sec in sections:
            bar = sec["start_bar"]
            label = sec["label"]
            sections_by_bar.setdefault(bar, []).append(label)

    for i, bar in enumerate(bars):

        measure = SubElement(part, "measure", number=str(bar))

        # --- attributes בתחילת היצירה ---
        if i == 0:
            attributes = SubElement(measure, "attributes")

            SubElement(attributes, "divisions").text = "1"

            key = SubElement(attributes, "key")
            SubElement(key, "fifths").text = "0"

            time = SubElement(attributes, "time")
            SubElement(time, "beats").text = str(beats_per_bar)
            SubElement(time, "beat-type").text = str(beat_type)

            if bpm is not None:
                direction = SubElement(measure, "direction", placement="above")
                direction_type = SubElement(direction, "direction-type")
                metronome = SubElement(direction_type, "metronome")
                SubElement(metronome, "beat-unit").text = "quarter"
                SubElement(metronome, "per-minute").text = str(bpm)
                sound = SubElement(direction, "sound")
                sound.set("tempo", str(bpm))

        # --- הוספת Section אם מתחיל בתיבה הזו ---
        if bar in sections_by_bar:
            for label in sections_by_bar[bar]:
                direction = SubElement(measure, "direction", placement="above")
                direction_type = SubElement(direction, "direction-type")
                words = SubElement(direction_type, "words")
                words.text = label

        # --- אקורדים בתיבה ---
        starting_here = [s for s in segments if s["start_bar"] == bar]
        continuing_here = [s for s in segments if s["start_bar"] < bar <= s["end_bar"]]

        bar_segments = starting_here if starting_here else continuing_here

        for seg in bar_segments:
            harmony = SubElement(measure, "harmony")

            kind_el = SubElement(harmony, "kind")
            kind_el.text = "major"
            kind_el.set("text", seg["chord"])

            offset = SubElement(harmony, "offset")
            offset.text = str(seg["start_beat"] - 1 if bar == seg["start_bar"] else 0)

    return tostring(score, encoding="utf-8", xml_declaration=True)



# ---------------------------
# CREATE JOB
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

    return jsonify({"job_id": job_res.json()["id"]})


# ---------------------------
# FETCH ANALYSIS
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

    chords = requests.get(result.get("chords") or result.get("Chords")).json()
    beats = requests.get(result.get("Beats") or result.get("beats")).json()
    sections = requests.get(result.get("Sections") or result.get("sections")).json()

    bpm = result.get("Bpm") or result.get("bpm")

    return chords, sections, beats, bpm, "SUCCEEDED"


# ---------------------------
# STATUS ROUTE (UPDATED)
# ---------------------------
@app.route("/status/<job_id>")
def status(job_id):

    chords, sections, beats, bpm, state = fetch_analysis(job_id)

    if chords is None:
        return jsonify({"status": state})

    xml_segments = build_segments(chords)
    timeline_segments = build_timeline_segments(chords)

    beats_per_bar, beat_type = detect_time_signature(beats)

    response = {
        "status": "SUCCEEDED",
        "chart": xml_segments,
        "timeline_chords": timeline_segments,
        "beats": beats,
        "time_signature": {
            "beats_per_bar": beats_per_bar,
            "beat_type": beat_type
        },
        "bpm": bpm
    }

    if sections:
        response["sections"] = sections

    return jsonify(response)


# ---------------------------
# MUSICXML DOWNLOAD
# ---------------------------
@app.route("/musicxml/<job_id>")
def musicxml(job_id):

    chords, sections, beats, bpm, state = fetch_analysis(job_id)

    if chords is None:
        return jsonify({"error": "Processing"}), 400

    segments = build_segments(chords)
    segments = quantize_segments_to_beats(segments, beats)

    xml_data = chords_to_musicxml(segments, sections, bpm, beats)

    return Response(
        xml_data,
        mimetype="application/xml",
        headers={"Content-Disposition": "attachment; filename=chords.musicxml"}
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
