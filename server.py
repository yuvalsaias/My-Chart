# ---------------------------
# CHORD PARSER
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
            return None   # ⭐ אין fallback ל-C

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


# ---------------------------
# BUILD HARMONIC TIMELINE (FIXED)
# ---------------------------
def build_harmonic_timeline(segments):

    # מיון לפי זמן
    segments = sorted(
        segments,
        key=lambda s: (s["start_bar"], s["start_beat"])
    )

    timeline = []

    for i, seg in enumerate(segments):

        new_seg = seg.copy()

        # קבע סוף לפי האקורד הבא
        if i < len(segments) - 1:

            next_seg = segments[i + 1]

            new_seg["end_bar"] = next_seg["start_bar"]

            # אם האקורד הבא באותו בר → עצור לפני הביט שלו
            if next_seg["start_bar"] == seg["start_bar"]:
                new_seg["end_beat"] = max(1, next_seg["start_beat"] - 0.001)
            else:
                new_seg["end_beat"] = next_seg["start_beat"]

        timeline.append(new_seg)

    return timeline


# ---------------------------
# EXPAND SEGMENTS ACROSS BARS (SAFE)
# ---------------------------
def expand_segments_across_bars(segments):

    expanded = []

    for seg in segments:

        for bar in range(seg["start_bar"], seg["end_bar"] + 1):

            new_seg = seg.copy()
            new_seg["start_bar"] = bar

            # שמור ביט התחלה אמיתי
            if bar == seg["start_bar"]:
                new_seg["start_beat"] = seg["start_beat"]
            else:
                new_seg["start_beat"] = 1

            # ⭐ מניעת כפילויות
            if not any(
                s["start_bar"] == new_seg["start_bar"]
                and s["start_beat"] == new_seg["start_beat"]
                for s in expanded
            ):
                expanded.append(new_seg)

    return expanded
