"""Which car the camera should follow, at which moment of the new replay.

A replay file does not store the camera or the followed car; the game decides
that while playing. So the plan is written next to the new replay, as a small
JSON file, and the Lua app in lua_app/OvertakeFocus reads it and switches the
focused car while the replay plays.

Time in the plan is the time in the NEW replay (seconds from its start).
"""
import json
import os

FOCUS_OVERTAKER = "overtaker"
FOCUS_PASSED = "passed"
FOCUS_SAME = "same"
FOCUS_NONE = "none"
FOCUS_CHOICES = ((FOCUS_OVERTAKER, "Car that overtakes / chases"),
                 (FOCUS_PASSED, "Car that is passed / in front"),
                 (FOCUS_SAME, "Chaser, same car all through a battle"),
                 (FOCUS_NONE, "Do not write a focus plan"))

# In a battle the camera follows the chaser (or the front car) of the closest
# pair. A new car must be the one to follow for HOLD_S before the camera
# switches, and switches are at least MIN_SWITCH_S apart, so it does not flicker.
HOLD_S = 1.0
MIN_SWITCH_S = 3.0
BATTLE_STEP_S = 0.3


def new_time(ranges, frame, interval_s):
    """Time in the new replay of an original frame that lies inside `ranges`."""
    done = 0
    for a, b in sorted(ranges):
        if a <= frame <= b:
            return (done + frame - a) * interval_s
        done += b - a + 1
    return None


def battle_events(model, item, target, gaps):
    """[(frame, car, note)] for one ticked battle or phase.

    `item` is a dict: first, last (frames of its clip), cars (front to back at
    its start), pairs (car pairs of the battle; empty for the start)."""
    import battles      # here, so the module also loads where battles is not needed
    cars = model.replay.cars
    step = model.replay.interval_ms / 1000.0
    first, last = item["first"], item["last"]
    if not item["pairs"]:                       # the start: follow the leader
        car = item["cars"][0]
        return [(first, car, "start, %s leads" % cars[car].driver)]
    chase = target in (FOCUS_OVERTAKER, FOCUS_SAME)
    every = max(1, int(round(BATTLE_STEP_S / step)))
    out = []
    cur = pending = None
    pending_since = last_switch = None
    for f in range(first, last + 1, every):
        cf = battles.chaser_and_front(gaps, item["pairs"], f)
        if cf is None:
            continue
        car, other = cf if chase else (cf[1], cf[0])
        note = "%s %s %s" % (cars[car].driver, "chases" if chase else "is in front of", cars[other].driver)
        if cur is None:
            out.append((first, car, note))
            cur, last_switch = car, first
            if target == FOCUS_SAME:
                break
            continue
        if car == cur:
            pending = None
            continue
        if pending != car:
            pending, pending_since = car, f
            continue
        if (f - pending_since) * step >= HOLD_S and (pending_since - last_switch) * step >= MIN_SWITCH_S:
            out.append((pending_since, car, note))
            cur, last_switch, pending = car, pending_since, None
    return out


def build(model, overtakes, ticked, ranges, target=FOCUS_OVERTAKER, battle_items=(), gaps=None):
    """List of switches {t, car, name, note}, in time order.

    Overtakes: the camera switches halfway between two overtakes. Battles
    (`battle_items`, see battle_events; needs `gaps`): it follows the chaser or
    the front car and switches when that changes."""
    cars = model.replay.cars
    step = model.replay.interval_ms / 1000.0
    switches = []
    for a, b in sorted(ranges):
        events = []          # (frame, car, note, is_overtake)
        for i, o in enumerate(overtakes):
            if i in ticked and a <= o.frame <= b:
                car = o.passed if target == FOCUS_PASSED else o.overtaker
                other = o.overtaker if target == FOCUS_PASSED else o.passed
                verb = "is passed by" if target == FOCUS_PASSED else "overtakes"
                events.append((o.frame, car, "%s %s %s" % (cars[car].driver, verb, cars[other].driver), True))
        if gaps is not None:
            for item in battle_items:
                if item["last"] < a or item["first"] > b:
                    continue
                for f, car, note in battle_events(model, item, target, gaps):
                    if f <= b:
                        events.append((max(f, a), car, note, False))
        events.sort(key=lambda e: e[0])
        for n, (f, car, note, is_overtake) in enumerate(events):
            if n == 0:
                t = new_time(ranges, a, step)                  # the clip starts on this car
            elif is_overtake:                                  # halfway since the event before
                t = new_time(ranges, (events[n - 1][0] + f) // 2, step)
            else:
                t = new_time(ranges, f, step)
            switches.append({"t": round(t, 3), "car": car, "name": cars[car].driver, "note": note})
    # keep every clip start (a cut), drop a switch to the car that is already followed
    # inside the same clip
    switches.sort(key=lambda s: s["t"])
    out = []
    starts = {round(new_time(ranges, a, step), 3) for a, _b in ranges}
    for s in switches:
        if out and out[-1]["car"] == s["car"] and s["t"] not in starts:
            continue
        if out and out[-1]["t"] == s["t"]:
            out[-1] = s                    # two switches at the same moment: the last wins
            continue
        out.append(s)
    return out


SAMPLE_STEP_S = 1.0


def original_frame(ranges, t, interval_s):
    """Frame of the original replay shown at time `t` of the new replay (or None)."""
    want = int(round(t / interval_s))
    done = 0
    for a, b in sorted(ranges):
        n = b - a + 1
        if want < done + n:
            return a + want - done
        done += n
    return None


def samples(model, ranges, duration_s, step_s=SAMPLE_STEP_S):
    """Where every car is (x, z in metres, 0.1 m) at every `step_s` of the new replay.

    The in-game app compares these with the cars it sees to recognise which replay is
    playing, since the game does not tell the app the replay's file name."""
    interval_s = model.replay.interval_ms / 1000.0
    out = []
    k = 0
    while k * step_s <= duration_s + 1e-6:
        f = original_frame(ranges, k * step_s, interval_s)
        row = []
        for car in model.replay.cars:
            p = car.positions[f] if (f is not None and car.has_positions and f < len(car.positions)) else None
            if p is None or p == (0.0, 0.0, 0.0):
                row.append(None)
            else:
                row.append([round(p[0], 1), round(p[2], 1)])
        out.append(row)
        k += 1
    return out


def document(model, replay_file, switches, duration_s, target, ranges=None):
    doc = {"version": 2, "replay": os.path.basename(replay_file), "focus": target,
           "duration_s": round(duration_s, 3), "interval_ms": model.replay.interval_ms,
           "cars": [c.driver for c in model.replay.cars], "switches": switches}
    if ranges:
        doc["sample_step_s"] = SAMPLE_STEP_S
        doc["samples"] = samples(model, ranges, duration_s)
    return doc


def documents_ac_folder():
    """Documents\\Assetto Corsa of this user, or None."""
    candidates = []
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        # CSIDL_PERSONAL (5) is the real "Documents", also when OneDrive moved it
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:
            candidates.append(buf.value)
    except (ImportError, AttributeError, OSError):
        pass
    home = os.path.expanduser("~")
    candidates += [os.path.join(home, "Documents"), os.path.join(home, "OneDrive", "Documents")]
    for base in candidates:
        folder = os.path.join(base, "Assetto Corsa")
        if os.path.isdir(folder):
            return folder
    return None


def save(doc, replay_path):
    """Write the plan next to the new replay as <name>.focus.json; the Overtake Focus app
    in the game finds it by the replay's name. Returns the path written."""
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    sidecar = os.path.splitext(replay_path)[0] + ".focus.json"
    with open(sidecar, "w", encoding="utf-8") as fh:
        fh.write(text)
    return sidecar
