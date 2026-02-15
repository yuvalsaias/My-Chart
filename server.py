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

    # scale beats
    if beats:
        for b in beats:
            if "time" in b and b["time"] is not None:
                b["time"] = b["time"] / scale

    # scale chords
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
# CHORD PARSER FOR MUSICXML
# ---------------------------------------------------
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

    except:
        return None


# ---------------------------------------------------
# SEGMENTS
# ---------------------------------------------------
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


# ---------------------------------------------------
# QUANTIZE
# ---------------------------------------------------
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


# ---------------------------------------------------
# MAP SECTIONS
# ---------------------------------------------------
def map_sections_to_bars(sections, chords):
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

        label = sec.get("label") or "Section"

        mapped.append({
            "label": label,
            "start_bar": bar
        })

    filtered = []
    last_label = None

    for sec in mapped:
        if sec["label"] == last_label:
            continue
        filtered.append(sec)
        last_label = sec["label"]

    return filtered


# ---------------------------------------------------
# MUSICXML
# ---------------------------------------------------
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

        if sections:
            for sec in sections:
                if sec.get("start_bar") == bar:
                    label = sec.get("label") or "Section"
                    direction = SubElement(measure, "direction", placement="above")
                    direction_type = SubElement(direction, "direction-type")
                    rehearsal = SubElement(direction_type, "rehearsal")
                    rehearsal.text = label

        starting_here = [s for s in segments if s["start_bar"] == bar]

        for seg in starting_here:
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


# ---------------------------------------------------
# CREATE JOB
# ---------------------------------------------------
@app.route("/analyze", methods=["POST"])
def analyze():

    file = request.files["file"]
    manual_bpm = request.form.get("bpm_override")

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
        }
    )

    job_data = job_res.json()

    return jsonify({"job_id": job_data["id"]})


# ---------------------------------------------------
# FETCH ANALYSIS
# ---------------------------------------------------
def fetch_analysis(job_id):

    status_res = requests.get(
        f"https://api.music.ai/api/job/{job_id}",
        headers={"Authorization": API_KEY}
    )

    status_data = status_res.json()

    if status_data["status"] != "SUCCEEDED":
        return None, None, None, None, None, status_data["status"]

    result = status_data["result"]

    chords_url = result.get("chords") or result.get("Chords")
    beats_url = result.get("Beats") or result.get("beats")
    sections_url = result.get("Sections") or result.get("sections")

    detected_bpm = result.get("Bpm") or result.get("bpm")
    manual_bpm = result.get("manual_bpm") or None

    chords_json = requests.get(chords_url).json()

    if isinstance(chords_json, dict):
        chords = chords_json.get("chords", chords_json)
    else:
        chords = chords_json

    beats = requests.get(beats_url).json() if beats_url else None
    sections = requests.get(sections_url).json() if sections_url else None

    return chords, sections, beats, detected_bpm, manual_bpm, "SUCCEEDED"


# ---------------------------------------------------
# STATUS ROUTE
# ---------------------------------------------------
@app.route("/status/<job_id>")
def status(job_id):

    chords, sections, beats, detected_bpm, manual_bpm, state = fetch_analysis(job_id)

    if chords is None:
        return jsonify({"status": state})

    if manual_bpm:
        beats, chords = apply_bpm_scaling(beats, chords, detected_bpm, manual_bpm)
        bpm = float(manual_bpm)
    else:
        bpm = float(detected_bpm) if detected_bpm else None

    segments = build_segments(chords)
    timeline_segments = build_timeline_segments(chords)

    beats_per_bar, beat_type = detect_time_signature(beats)

    response = {
        "status": "SUCCEEDED",
        "chart": segments,
        "timeline_chords": timeline_segments,
        "beats": beats,
        "time_signature": {
            "beats_per_bar": beats_per_bar,
            "beat_type": beat_type
        },
        "bpm": bpm
    }

    if sections is not None:
        response["sections"] = sections

    return jsonify(response)


# ---------------------------------------------------
# MUSICXML ROUTE
# ---------------------------------------------------
@app.route("/musicxml/<job_id>")
def musicxml(job_id):

    chords, sections, beats, detected_bpm, manual_bpm, state = fetch_analysis(job_id)

    if chords is None:
        return jsonify({"error": "Processing"}), 400

    if manual_bpm:
        beats, chords = apply_bpm_scaling(beats, chords, detected_bpm, manual_bpm)
        bpm = float(manual_bpm)
    else:
        bpm = float(detected_bpm) if detected_bpm else None

    segments = build_segments(chords)
    segments = quantize_segments_to_beats(segments, beats)
    mapped_sections = map_sections_to_bars(sections, chords) if sections else None

    xml_data = chords_to_musicxml(segments, mapped_sections, bpm, beats)

    return Response(
        xml_data,
        mimetype="application/xml",
        headers={"Content-Disposition": "attachment; filename=chords.musicxml"}
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
