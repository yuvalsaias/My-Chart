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

    if chord in (None, "N"):
        return None

    return chord


# ---------------------------
# ADVANCED CHORD PARSER
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
# EXPAND SEGMENTS ACROSS BARS
# ---------------------------
def expand_segments_across_bars(segments):

    expanded = []

    for seg in segments:
        for bar in range(seg["start_bar"], seg["end_bar"] + 1):

            new_seg = seg.copy()
            new_seg["start_bar"] = bar
            new_seg["start_beat"] = seg["start_beat"] if bar == seg["start_bar"] else 1

            if not any(
                s["start_bar"] == new_seg["start_bar"]
                and s["start_beat"] == new_seg["start_beat"]
                for s in expanded
            ):
                expanded.append(new_seg)

    return expanded


# ---------------------------
# MAP SECTIONS (SECONDS) TO BARS
# ---------------------------
def map_sections_to_bars(sections, chords):

    if not sections or not chords:
        return None

    mapped = []
    last_label = None

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

        if label == last_label:
            continue

        mapped.append({
            "label": label,
            "start_bar": bar
        })

        last_label = label

    return mapped


# ---------------------------
# MUSICXML BUILDER
# ---------------------------
def chords_to_musicxml(segments, sections=None, bpm=None):

    score = Element("score-partwise", version="3.1")

    part_list = SubElement(score, "part-list")
    score_part = SubElement(part_list, "score-part", id="P1")
    SubElement(score_part, "part-name").text = "Chords"

    part = SubElement(score, "part", id="P1")

    bars = sorted(set(s["start_bar"] for s in segments))

    for i, bar in enumerate(bars):

        measure = SubElement(part, "measure", number=str(bar + 1))

        if i == 0:
            attributes = SubElement(measure, "attributes")
            SubElement(attributes, "divisions").text = "1"

            key = SubElement(attributes, "key")
            SubElement(key, "fifths").text = "0"

            time = SubElement(attributes, "time")
            SubElement(time, "beats").text = "4"
            SubElement(time, "beat-type").text = "4"

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
# MUSICXML DOWNLOAD
# ---------------------------
@app.route("/musicxml/<job_id>")
def musicxml(job_id):

    chords, sections, beats, bpm, state = fetch_analysis(job_id)
    if chords is None:
        return jsonify({"error": "Processing"}), 400

    segments = build_segments(chords)
    segments = quantize_segments_to_beats(segments, beats)
    segments = expand_segments_across_bars(segments)

    mapped_sections = map_sections_to_bars(sections, chords) if sections else None
    xml_data = chords_to_musicxml(segments, mapped_sections, bpm)

    return Response(
        xml_data,
        mimetype="application/xml",
        headers={"Content-Disposition": "attachment; filename=chords.musicxml"}
    )
