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
# PARSE CHORD FOR XML (RESTORED)
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

        return step, alter, kind, [], bass_note, original

    except Exception:
        return None


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
# BUILD TIMELINE SEGMENTS
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
# FETCH ANALYSIS (FIXED)
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

    # Normalize chords
    chords_json = requests.get(
        result.get("chords") or result.get("Chords")
    ).json()

    if isinstance(chords_json, dict):
        chords = chords_json.get("chords", [])
    else:
        chords = chords_json

    # Normalize beats
    beats_json = requests.get(
        result.get("Beats") or result.get("beats")
    ).json()

    if isinstance(beats_json, dict):
        beats = beats_json.get("beats", [])
    else:
        beats = beats_json

    # Normalize sections
    sections_json = requests.get(
        result.get("Sections") or result.get("sections")
    ).json()

    if isinstance(sections_json, dict):
        sections = sections_json.get("sections", [])
    else:
        sections = sections_json

    bpm = result.get("Bpm") or result.get("bpm")

    return chords, sections, beats, bpm, "SUCCEEDED"


# ---------------------------
# MUSICXML BUILDER (FIXED)
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

    for i, bar in enumerate(bars):

        measure = SubElement(part, "measure", number=str(bar + 1))

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

        starting_here = [s for s in segments if s["start_bar"] == bar]
        continuing_here = [
            s for s in segments if s["start_bar"] < bar <= s["end_bar"]
        ]

        bar_segments = starting_here if starting_here else continuing_here

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

            offset = SubElement(harmony, "offset")
            offset.text = str(seg["start_beat"] - 1 if bar == seg["start_bar"] else 0)

    return tostring(score, encoding="utf-8", xml_declaration=True)


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
