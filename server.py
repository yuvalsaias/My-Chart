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
# TIME SIGNATURE PER BAR
# ---------------------------------------------------
def detect_time_signature_per_bar(beats):

    if not beats:
        return {}

    bar_counts = {}
    bar_index = -1
    current_count = 0

    for b in beats:
        if b["beatNum"] == 1:
            if current_count > 0:
                bar_counts[bar_index] = current_count
            bar_index += 1
            current_count = 1
        else:
            current_count += 1

    if current_count > 0:
        bar_counts[bar_index] = current_count

    return bar_counts
def detect_empty_bars_before_first_chord(beats, first_chord_time):
    bar_index = -1
    for b in beats:
        if b["time"] >= first_chord_time:
            break
        if b["beatNum"] == 1:
            bar_index += 1
    return bar_index


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
def map_sections_to_bars(sections, beats):
    if not sections or not beats:
        return None

    # Build bar index from beats
    bar_index = -1
    beat_to_bar = []

    for b in beats:
        if b["beatNum"] == 1:
            bar_index += 1
        beat_to_bar.append((b["time"], bar_index))

    mapped = []

    for sec in sections:
        sec_start = sec.get("start")
        if sec_start is None:
            continue

        # Find closest beat to section start
        closest = min(beat_to_bar, key=lambda x: abs(x[0] - sec_start))
        bar = closest[1]

        label = sec.get("label") or "Section"

        # Avoid duplicate consecutive labels
        if mapped and mapped[-1]["label"] == label:
            continue

        mapped.append({
            "label": label,
            "start_bar": bar
        })

    return mapped


# ---------------------------------------------------
# KEY PARSING FOR MUSICXML
# ---------------------------------------------------
def parse_key_to_musicxml(key_str):
    if not key_str:
        return 0, "major"

    parts = key_str.strip().split()
    tonic = parts[0]
    mode = parts[1].lower() if len(parts) > 1 else "major"

    fifths_map = {
        "C": 0,
        "G": 1,
        "D": 2,
        "A": 3,
        "E": 4,
        "B": 5,
        "F#": 6,
        "C#": 7,
        "F": -1,
        "Bb": -2,
        "Eb": -3,
        "Ab": -4,
        "Db": -5,
        "Gb": -6,
        "Cb": -7,
    }

    fifths = fifths_map.get(tonic, 0)
    if mode not in ("major", "minor"):
        mode = "major"

    return fifths, mode


# ---------------------------------------------------
# MUSICXML
# ---------------------------------------------------
def chords_to_musicxml(segments, sections=None, bpm=None, beats=None, key_str=None):

    score = Element("score-partwise", version="3.1")

    part_list = SubElement(score, "part-list")
    score_part = SubElement(part_list, "score-part", id="P1")
    SubElement(score_part, "part-name").text = "Chords"

    part = SubElement(score, "part", id="P1")

    if not segments:
        return tostring(score, encoding="utf-8", xml_declaration=True)

    bar_time_map = detect_time_signature_per_bar(beats)

    beats_bars = len(bar_time_map) if bar_time_map else 0
    segments_bars = max(s["end_bar"] for s in segments) + 1 if segments else 0

    total_bars = max(beats_bars, segments_bars)

    bars = list(range(total_bars))

    key_fifths, key_mode = parse_key_to_musicxml(key_str)

    divisions = 480

    bar_time_map = detect_time_signature_per_bar(beats)
    previous_beats = None

    for i, bar in enumerate(bars):

        measure = SubElement(part, "measure", number=str(bar + 1))

        beats_in_this_bar = bar_time_map.get(i, 4)

        if beats_in_this_bar in (6, 9, 12):
            beat_type_local = 8
        else:
            beat_type_local = 4

        units_per_beat = int(divisions * 4 / beat_type_local)

        # אם יש שינוי משקל – מוסיפים attributes
        if beats_in_this_bar != previous_beats:

            attributes = SubElement(measure, "attributes")

            if i == 0:
                SubElement(attributes, "divisions").text = str(divisions)

                key = SubElement(attributes, "key")
                SubElement(key, "fifths").text = str(key_fifths)
                SubElement(key, "mode").text = key_mode

            time_el = SubElement(attributes, "time")
            SubElement(time_el, "beats").text = str(beats_in_this_bar)
            SubElement(time_el, "beat-type").text = str(beat_type_local)

        previous_beats = beats_in_this_bar

        # BPM רק בתיבה הראשונה
        if i == 0 and bpm is not None:
            direction = SubElement(measure, "direction", placement="above")
            direction_type = SubElement(direction, "direction-type")
            metronome = SubElement(direction_type, "metronome")
            SubElement(metronome, "beat-unit").text = "quarter"
            SubElement(metronome, "per-minute").text = str(bpm)
            sound = SubElement(direction, "sound")
            sound.set("tempo", str(bpm))

        # Sections
        if sections:
            for sec in sections:
                if sec.get("start_bar") == bar:
                    direction = SubElement(measure, "direction", placement="above")
                    direction_type = SubElement(direction, "direction-type")
                    rehearsal = SubElement(direction_type, "rehearsal")
                    rehearsal.text = sec.get("label") or "Section"

        starting_here = [s for s in segments if s["start_bar"] == bar]
        starting_here.sort(key=lambda s: s.get("start_beat", 1))

        current_beat = 1

        def add_rest(beats_len):
            if beats_len <= 0:
                return
            dur = int(beats_len * units_per_beat)
            note = SubElement(measure, "note")
            SubElement(note, "rest")
            SubElement(note, "duration").text = str(dur)

        for idx, seg in enumerate(starting_here):

            seg_start_beat = seg.get("start_beat", 1)

            gap_beats = seg_start_beat - current_beat
            if gap_beats > 0:
                add_rest(gap_beats)
                current_beat += gap_beats

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
                bass_step = bass_note[0]
                SubElement(bass, "bass-step").text = bass_step

                if len(bass_note) > 1:
                    if bass_note[1] == "#":
                        SubElement(bass, "bass-alter").text = "1"
                    elif bass_note[1] == "b":
                        SubElement(bass, "bass-alter").text = "-1"

            for value, dtype, alter_val in degrees:
                degree = SubElement(harmony, "degree")
                SubElement(degree, "degree-value").text = value
                SubElement(degree, "degree-type").text = dtype
                if alter_val is not None:
                    SubElement(degree, "degree-alter").text = alter_val

            if idx < len(starting_here) - 1:
                next_beat = starting_here[idx + 1].get("start_beat", beats_in_this_bar + 1)
                dur_beats = max(0, next_beat - seg_start_beat)
            else:
                dur_beats = max(0, beats_in_this_bar - seg_start_beat + 1)

            if dur_beats > 0:
                add_rest(dur_beats)
                current_beat = seg_start_beat + dur_beats
            else:
                current_beat = seg_start_beat

        if current_beat <= beats_in_this_bar:
            tail_beats = beats_in_this_bar - current_beat + 1
            add_rest(tail_beats)

    return tostring(score, encoding="utf-8", xml_declaration=True)


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
            headers={"Authorization": API_KEY}
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

        if not upload_url or not download_url:
            return jsonify({
                "error": "music.ai upload response missing URLs",
                "response": upload_data
            }), 502

        put_res = requests.put(
            upload_url,
            data=file.read(),
            headers={"Content-Type": file.content_type}
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
            }
        )

        if job_res.status_code != 200:
            return jsonify({
                "error": "Failed to create job in music.ai",
                "status_code": job_res.status_code,
                "response": job_res.text
            }), 502

        job_data = job_res.json()

        return jsonify({"job_id": job_data.get("id")})

    except Exception as e:
        return jsonify({"error": "Unexpected server error", "details": str(e)}), 500


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
        return None, None, None, None, None, None, status_data["status"]

    result = status_data["result"]

    chords_url = result.get("chords") or result.get("Chords")
    beats_url = result.get("Beats") or result.get("beats")
    sections_url = result.get("Sections") or result.get("sections")

    detected_bpm = result.get("Bpm") or result.get("bpm")
    manual_bpm = result.get("manual_bpm") or None
    root_key = result.get("root key") or result.get("root_key")

    chords_json = requests.get(chords_url).json()

    if isinstance(chords_json, dict):
        chords = chords_json.get("chords", chords_json)
    else:
        chords = chords_json

    beats = requests.get(beats_url).json() if beats_url else None
    sections = requests.get(sections_url).json() if sections_url else None

    return chords, sections, beats, detected_bpm, manual_bpm, root_key, "SUCCEEDED"


# ---------------------------------------------------
# STATUS ROUTE
# ---------------------------------------------------
@app.route("/status/<job_id>")
def status(job_id):

    chords, sections, beats, detected_bpm, manual_bpm, root_key, state = fetch_analysis(job_id)

    if chords is None:
        return jsonify({"status": state})

    if manual_bpm:
        beats, chords = apply_bpm_scaling(beats, chords, detected_bpm, manual_bpm)
        bpm = float(manual_bpm)
    else:
        bpm = float(detected_bpm) if detected_bpm else None

    segments = build_segments(chords)
    timeline_segments = build_timeline_segments(chords)

    beats_in_this_bar, beat_type = detect_time_signature(beats)

    response = {
        "status": "SUCCEEDED",
        "chart": segments,
        "timeline_chords": timeline_segments,
        "beats": beats,
        "time_signature": {
            "beats_in_this_bar": beats_in_this_bar,
            "beat_type": beat_type
        },
        "bpm": bpm,
        "key": root_key
    }

    if sections is not None:
        response["sections"] = sections

    return jsonify(response)


# ---------------------------------------------------
# MUSICXML ROUTE
# ---------------------------------------------------
@app.route("/musicxml/<job_id>")
def musicxml(job_id):

    chords, sections, beats, detected_bpm, manual_bpm, root_key, state = fetch_analysis(job_id)

    if chords is None:
        return jsonify({"error": "Processing"}), 400

    if manual_bpm:
        beats, chords = apply_bpm_scaling(beats, chords, detected_bpm, manual_bpm)
        bpm = float(manual_bpm)
    else:
        bpm = float(detected_bpm) if detected_bpm else None

    segments = build_segments(chords)
    # --- FIX: detect empty bars before first chord ---
    if segments and beats:
        first_chord_time = segments[0].get("start_sec")
        empty_bars = detect_empty_bars_before_first_chord(beats, first_chord_time)

        if empty_bars > 0:
            for s in segments:
                s["start_bar"] += empty_bars
                s["end_bar"] += empty_bars

    segments = quantize_segments_to_beats(segments, beats)
    mapped_sections = map_sections_to_bars(sections, beats) if sections else None

    xml_data = chords_to_musicxml(segments, mapped_sections, bpm, beats, key_str=root_key)

    return Response(
        xml_data,
        mimetype="application/xml",
        headers={"Content-Disposition": "attachment; filename=chords.musicxml"}
    )


@app.route("/")
def home():
    return jsonify({
        "status": "Server is running",
        "routes": [
            "/analyze (POST)",
            "/status/<job_id>",
            "/musicxml/<job_id>"
        ]
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)