"""Clips: the stretch of the replay kept around each ticked overtake."""

CLIP_BEFORE_S = 5       # default seconds kept before an overtake
CLIP_AFTER_S = 5        # default seconds kept after it
MAX_CLIP_S = 60         # longest stretch that can be set before or after


def clamp_seconds(value, fallback=CLIP_BEFORE_S):
    try:
        return max(0, min(MAX_CLIP_S, int(round(float(value)))))
    except (TypeError, ValueError):
        return fallback


def clip_ranges(model, overtakes, ticked, span=None):
    """Merged (first_frame, last_frame) ranges around the ticked overtakes.

    `span(index)` returns (seconds_before, seconds_after) for an overtake; the
    default values are used without it. Overlapping clips are joined."""
    step = model.replay.interval_ms / 1000.0
    last = model.frames - 1
    spans = []
    for i, o in enumerate(overtakes):
        if i not in ticked:
            continue
        before, after = span(i) if span else (CLIP_BEFORE_S, CLIP_AFTER_S)
        spans.append((max(0, int(round(o.frame - before / step))),
                      min(last, int(round(o.frame + after / step)))))
    spans.sort()
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged
