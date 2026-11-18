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
FOCUS_NONE = "none"
FOCUS_CHOICES = ((FOCUS_OVERTAKER, "Car that overtakes"),
                 (FOCUS_PASSED, "Car that is passed"),
                 (FOCUS_NONE, "Do not write a focus plan"))

ACTIVE_NAME = "active.json"


def new_time(ranges, frame, interval_s):
    """Time in the new replay of an original frame that lies inside `ranges`."""
    done = 0
    for a, b in sorted(ranges):
        if a <= frame <= b:
            return (done + frame - a) * interval_s
        done += b - a + 1
    return None


def build(model, overtakes, ticked, ranges, target=FOCUS_OVERTAKER):
    """List of switches {t, car, name, note}, in time order."""
    cars = model.replay.cars
    step = model.replay.interval_ms / 1000.0
    switches = []
    for a, b in sorted(ranges):
        inside = sorted((o for i, o in enumerate(overtakes) if i in ticked and a <= o.frame <= b),
                        key=lambda o: o.frame)
        if not inside:
            continue
        for n, o in enumerate(inside):
            car = o.overtaker if target == FOCUS_OVERTAKER else o.passed
            if n == 0:
                t = new_time(ranges, a, step)                    # the clip starts on this car
            else:                                              # halfway between two overtakes
                mid = (inside[n - 1].frame + o.frame) // 2
                t = new_time(ranges, mid, step)
            other = o.passed if target == FOCUS_OVERTAKER else o.overtaker
            verb = "overtakes" if target == FOCUS_OVERTAKER else "is passed by"
            note = "%s %s %s" % (cars[car].driver, verb, cars[other].driver)
            switches.append({"t": round(t, 3), "car": car, "name": cars[car].driver, "note": note})
    # keep every clip start (a cut), drop a switch to the car that is already followed
    # inside the same clip
    out = []
    starts = {round(new_time(ranges, a, step), 3) for a, _b in ranges}
    for s in switches:
        if out and out[-1]["car"] == s["car"] and s["t"] not in starts:
            continue
        out.append(s)
    return out


def document(model, replay_file, switches, duration_s, target):
    return {"version": 1, "replay": os.path.basename(replay_file), "focus": target,
            "duration_s": round(duration_s, 3), "interval_ms": model.replay.interval_ms,
            "cars": [c.driver for c in model.replay.cars], "switches": switches}


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


def save(doc, replay_path, ac_folder=None):
    """Write the sidecar next to the replay and, when the game's Documents folder
    is known, the file the Lua app reads. Returns (sidecar_path, active_path or None)."""
    text = json.dumps(doc, indent=1, ensure_ascii=False)
    sidecar = os.path.splitext(replay_path)[0] + ".focus.json"
    with open(sidecar, "w", encoding="utf-8") as fh:
        fh.write(text)
    active = None
    if ac_folder:
        try:
            folder = os.path.join(ac_folder, "overtake_focus")
            os.makedirs(folder, exist_ok=True)
            active = os.path.join(folder, ACTIVE_NAME)
            with open(active, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError:
            active = None
    return sidecar, active
