"""Clips: the stretch of the replay kept around each ticked overtake."""

CLIP_BEFORE_S = 5.0      # kept before the overtake
CLIP_AFTER_S = 5.0       # kept after it


def clip_ranges(model, overtakes, ticked, before=CLIP_BEFORE_S, after=CLIP_AFTER_S):
    """Merged (first_frame, last_frame) ranges around the ticked overtakes."""
    step = model.replay.interval_ms / 1000.0
    last = model.frames - 1
    spans = sorted((max(0, int(o.frame - before / step)), min(last, int(o.frame + after / step)))
                   for i, o in enumerate(overtakes) if i in ticked)
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged
