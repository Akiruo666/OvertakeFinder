"""Finding battles: cars that drive close together for a while.

The gap between two cars is a TIME gap, measured the way race timing does it:
how long ago the car in front was at the spot where the car behind is now. It
is found from the recorded positions themselves (the front car's path is
searched for the point nearest to the other car), so it is accurate with and
without a racing line. The progress of the race model is only used to know
roughly where to search and, with a racing line, how many laps apart the two
cars are (lapping).

From the same search come two distances:
  along    metres between the two cars along the front car's path
  lateral  metres the car behind is beside the front car's path
Two cars are side by side when `along` is shorter than a car (they overlap).

A battle between two cars:
  - starts at the first moment the gap is "close" (min_s .. close_s),
  - goes on while the gap stays within battle_s; time outside close_s is a
    pause, and a pause may last at most max_pause_s,
  - ends when the gap goes over battle_s, the pause is too long, the gap is
    unknown for too long, or one of the cars is in the pit lane or stopped,
  - counts only with at least min_close_s close time in total.
It runs from its first to its last close moment and is split into phases
(close / pause). Battles of pairs that share a car and run at the same time
are joined into one battle of a group of cars (a train).

The start is a battle of its own: it lasts until the field has broken up
(no group of START_GROUP or more cars within battle_s) or the leader finished
lap 1; normal battles are only searched after it.

Run `python battles.py replay.acreplay [game folder]` for a text listing.
"""
import bisect
import math
from dataclasses import dataclass, field

import analysis
import overtakes as overtakes_mod
import surface


@dataclass
class Settings:
    close_s: float = 1.0          # gap that counts as a real fight
    battle_s: float = 2.0         # gap within which the battle goes on (pause)
    max_pause_s: float = 20.0     # longest pause before the battle has ended
    min_close_s: float = 5.0      # least close time (all phases together)
    min_s: float = 0.0            # smallest gap that counts (0: side by side counts)
    show_lapping: bool = True     # keep battles with a lapped car (marked)


# Colour levels of a gap (also for the map and the standings later).
YELLOW_S = 1.0
ORANGE_S = 0.5
RED_S = 0.2

SAMPLE = 3                # every 3rd frame (90 ms at 30 ms per frame)
CAR_OVERLAP_M = 4.0       # centres closer than this along the path: side by side
MATCH_MAX_M = 12.0        # the car behind must be this close to the front car's path
PREFILTER_LINE_M = 250.0  # with racing line: progress difference worth a search
PREFILTER_EST_M = 450.0   # without: distance driven drifts, search wider
CLOSING_MS = 60.0         # two cars never close in faster than this (skip ahead)
MOVING_KMH = 15.0         # slower than this: stopped / pit stop, no battle
DROPOUT_S = 1.0           # unknown gap tolerated for this long inside a battle
MIN_PAUSE_S = 1.5         # shorter pauses are part of the close phase around them
START_GROUP = 4           # the start ends when no group of this many cars is left
START_MAX_S = 120.0       # without lap data: the start lasts at most this long
PASS_DEADBAND_M = 3.0     # a car must be this far in front before it leads
PASS_MARGIN = 100         # frames (3 s): a lead change just before/after the battle counts
PASS_MATCH_S = 3.0        # an entry of the Overtakes list this close to a lead change is the same pass
GROUP_SHARE = 0.5         # pairs form one group when they run together this share of the shorter one


# Strong colours (lines on the map and the chart) and light ones (table rows).
LEVEL_COLORS = {"red": "#e53935", "orange": "#fb8c00", "yellow": "#fdd835", "grey": "#9e9e9e"}
LEVEL_LIGHT = {"red": "#ffcdd2", "orange": "#ffe0b2", "yellow": "#fff9c4", "grey": "#eeeeee"}


def level(gap_s, side=False, close_s=YELLOW_S, battle_s=None):
    """Colour level of a gap: 'red', 'orange', 'yellow', 'grey' (only with
    battle_s: still in battle) or None (no colour). Yellow goes up to close_s."""
    if gap_s is None:
        return None
    g = abs(gap_s)
    if side or g < RED_S:
        return "red"
    if g < ORANGE_S:
        return "orange"
    if g <= close_s:
        return "yellow"
    if battle_s is not None and g <= battle_s:
        return "grey"
    return None


@dataclass
class Phase:
    kind: str           # "close" or "pause"
    start: int          # frames
    end: int
    min_gap_s: float = None
    max_gap_s: float = None
    side_s: float = 0.0


@dataclass
class Battle:
    kind: str                     # "battle" or "start"
    cars: list                    # car indexes, front to back at the start
    start: int                    # frames
    end: int
    start_s: float
    end_s: float
    laps: tuple = (None, None)    # first and last lap (of the front car)
    phases: list = field(default_factory=list)
    close_s: float = 0.0          # close time, all close phases together
    red_s: float = 0.0
    side_s: float = 0.0
    min_gap_s: float = None
    mean_gap_s: float = None      # over the close moments
    lead_changes: list = field(default_factory=list)  # (frame, new leader, other car), measured
    passes: list = field(default_factory=list)    # entries of the Overtakes list that match one
    best_frame: int = None        # first side by side moment, else the smallest gap
    red_frames: list = field(default_factory=list)  # start of every red moment
    pairs: list = field(default_factory=list)     # (a, b) pairs that make up a group
    lapping: bool = False
    contact: bool = False
    off_track: list = field(default_factory=list)  # car indexes that went off
    score: float = 0.0
    flags: list = field(default_factory=list)

    @property
    def duration_s(self):
        return self.end_s - self.start_s


class PairGaps:
    """Time gap of every pair of cars at every sampled frame.

    gap(a, b, frame) is positive when a is ahead of b, None when unknown or far
    apart. side(a, b, frame) tells whether they are side by side."""

    def __init__(self, sample, interval_s):
        self.sample = sample
        self.interval_s = interval_s
        self.gaps = {}      # (a, b) with a < b -> list per sample (float or None)
        self.sides = {}     # (a, b) -> list of bool
        self.laps = {}      # (a, b) -> list of lap difference q (0 = same lap)
        self.alongs = {}    # (a, b) -> metres a is ahead of b along the path (or None)
        self.lead_changes = {}  # (a, b) -> [(frame, new leader, other car)]

    def _get(self, table, a, b, frame):
        k = frame // self.sample
        key = (a, b) if a < b else (b, a)
        series = table.get(key)
        if series is None or not 0 <= k < len(series):
            return None, key
        return series[k], key

    def gap(self, a, b, frame):
        v, key = self._get(self.gaps, a, b, frame)
        if v is None:
            return None
        return v if key == (a, b) else -v

    def side(self, a, b, frame):
        v, _ = self._get(self.sides, a, b, frame)
        return bool(v)

    def along(self, a, b, frame):
        """Metres a is ahead of b along the road (None when unknown)."""
        v, key = self._get(self.alongs, a, b, frame)
        if v is None:
            return None
        return v if key == (a, b) else -v

    def lap(self, a, b, frame):
        """Laps between the two cars (0 = same lap), None when unknown."""
        v, key = self._get(self.laps, a, b, frame)
        if v is None or self.gap(a, b, frame) is None:
            return None
        return v if key == (a, b) else -v

    def nearest_ahead(self, frame, limit_s=YELLOW_S):
        """{car behind: (car ahead, gap_s, side)} - for every car the nearest car
        in front of it on the road within limit_s (the pairs to draw)."""
        out = {}
        for a, b, g, s in self.close_pairs(frame, limit_s):
            if b not in out or g < out[b][1]:
                out[b] = (a, g, s)
        return out

    def close_pairs(self, frame, limit_s=YELLOW_S):
        """[(a, b, gap_s, side)] of all pairs within limit_s at frame (a ahead)."""
        k = frame // self.sample
        out = []
        for (a, b), series in self.gaps.items():
            if 0 <= k < len(series) and series[k] is not None and abs(series[k]) <= limit_s:
                g, s = series[k], bool(self.sides[(a, b)][k])
                out.append((a, b, g, s) if g >= 0 else (b, a, -g, s))
        return out


@dataclass
class Result:
    battles: list
    gaps: PairGaps
    start_end: int          # frame where the start is over
    settings: Settings
    notes: list = field(default_factory=list)


# ------------------------------------------------------------ gap search
class _Car:
    """Per car data needed for the search."""

    def __init__(self, model, index, sample, pit_check):
        car = model.replay.cars[index]
        n = model.frames
        self.pos = car.positions[:n]
        self.driven = analysis.driven_distance(self.pos)
        prog = model.progress[index]
        self.prog = prog
        run, top = [], -1e18
        for v in prog:
            top = max(top, v)
            run.append(top)
        self.pmax = run                     # monotonic, for bisect
        dt = model.replay.interval_ms / 1000.0
        self.moving = []
        self.pit = []
        for f in range(0, n, sample):
            lo, hi = max(0, f - 5), min(n - 1, f + 5)
            v = (self.driven[hi] - self.driven[lo]) / max(1, hi - lo) / dt * 3.6
            self.moving.append(v >= MOVING_KMH)
            self.pit.append(pit_check(self.pos[f]) if pit_check else False)


def _nearest(pos, p, lo, hi):
    """Index in pos[lo..hi] nearest to point p (3D)."""
    best, best_d = lo, float("inf")
    px, py, pz = p
    for t in range(lo, hi + 1):
        q = pos[t]
        d = (q[0] - px) ** 2 + (q[1] - py) ** 2 + (q[2] - pz) ** 2
        if d < best_d:
            best, best_d = t, d
    return best


def _refine(pos, t, p):
    """Fractional index and distance of p to the path around pos[t]."""
    best = (float(t), math.dist(pos[t], p))
    for i in (t - 1, t):
        if i < 0 or i + 1 >= len(pos):
            continue
        a, b = pos[i], pos[i + 1]
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        seg = dx * dx + dy * dy + dz * dz
        if seg <= 1e-9:
            continue
        u = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy + (p[2] - a[2]) * dz) / seg
        if 0.0 <= u <= 1.0:
            d = math.dist((a[0] + u * dx, a[1] + u * dy, a[2] + u * dz), p)
            if d < best[1]:
                best = (i + u, d)
    return best


def _interp(values, t):
    i = int(t)
    if i + 1 >= len(values):
        return values[-1]
    return values[i] + (values[i + 1] - values[i]) * (t - i)


def _match(ca, p, guess, half, n):
    """Where was car `ca` closest to point p, searching guess +- half?
    Returns (t_frac, lateral) or None (no true minimum inside the data)."""
    lo, hi = max(0, guess - half), min(n - 1, guess + half)
    if lo > hi:
        return None
    t = _nearest(ca.pos, p, lo, hi)
    if (t == lo and lo > 0) or (t == hi and hi < n - 1):
        lo2, hi2 = max(0, t - 4 * half), min(n - 1, t + 4 * half)
        t = _nearest(ca.pos, p, lo2, hi2)
        if (t == lo2 and lo2 > 0) or (t == hi2 and hi2 < n - 1):
            return None
    if t == 0 or t == n - 1:
        return None          # the car was never (or not yet) there
    return _refine(ca.pos, t, p)


def _pair_series(model, a, b, ca, cb, frames, length, dt, prefilter):
    n = model.frames
    gaps = [None] * len(frames)
    sides = [False] * len(frames)
    laps = [0] * len(frames)
    alongs = [None] * len(frames)
    lock = None
    k = 0
    while k < len(frames):
        f = frames[k]
        if not (ca.moving[k] and cb.moving[k]) or ca.pit[k] or cb.pit[k]:
            lock = None
            k += 1
            continue
        d = ca.prog[f] - cb.prog[f]
        q = round(d / length) if length else 0
        e = d - q * length if length else d
        if abs(e) > prefilter:
            lock = None
            skip = int((abs(e) - prefilter) / (CLOSING_MS * SAMPLE * dt))
            k += 1 + max(0, skip)
            continue
        p = cb.pos[f]
        if lock is not None:
            res = _match(ca, p, lock + SAMPLE, 8, n)
        else:
            target = cb.prog[f] + (q * length if length else 0.0)
            guess = min(n - 1, bisect.bisect_left(ca.pmax, target))
            half = 50 if model.line is not None else 130
            res = _match(ca, p, guess, half, n)
        if res is None or res[1] > MATCH_MAX_M:
            lock = None
            k += 1
            continue
        t, lateral = res
        lock = int(round(t))
        gaps[k] = (f - t) * dt                        # > 0: a was there first, a ahead
        along = ca.driven[f] - _interp(ca.driven, t)
        alongs[k] = along
        sides[k] = abs(along) < CAR_OVERLAP_M
        laps[k] = q
        k += 1
    return gaps, sides, laps, alongs


def _lead_changes(alongs, frames, a, b):
    """Moments one car gets in front of the other, measured on the road: the
    lead counts once a car is PASS_DEADBAND_M in front. The moment is the last
    crossing of zero before that."""
    out = []
    state = 0
    cross = None
    prev = None
    for k, v in enumerate(alongs):
        if v is None:
            state, cross, prev = 0, None, None    # lost track: start again
            continue
        if prev is not None and (prev < 0) != (v < 0):
            cross = k
        prev = v
        new = 1 if v > PASS_DEADBAND_M else -1 if v < -PASS_DEADBAND_M else 0
        if new == 0 or new == state:
            continue
        if state != 0 and cross is not None:
            out.append((frames[cross], a, b) if new == 1 else (frames[cross], b, a))
        state, cross = new, None
    return out


def pair_gaps(model, sample=SAMPLE, pit_check=None):
    dt = model.replay.interval_ms / 1000.0
    frames = list(range(0, model.frames, sample))
    length = model.line.length if (model.line is not None and model.line.closed) else None
    prefilter = PREFILTER_LINE_M if model.line is not None else PREFILTER_EST_M
    cars = {i: _Car(model, i, sample, pit_check) for i in model.active}
    out = PairGaps(sample, dt)
    act = sorted(model.active)
    for x in range(len(act)):
        for y in range(x + 1, len(act)):
            a, b = act[x], act[y]
            g, s, q, al = _pair_series(model, a, b, cars[a], cars[b], frames, length, dt, prefilter)
            out.gaps[(a, b)], out.sides[(a, b)], out.laps[(a, b)] = g, s, q
            out.alongs[(a, b)] = al
            out.lead_changes[(a, b)] = _lead_changes(al, frames, a, b)
    return out, frames, cars


# ------------------------------------------------------------ battles
def _phases(close, side, absgap, k0, k1, frames, sdt):
    """Close / pause phases between samples k0..k1 (both close)."""
    runs = []
    k = k0
    while k <= k1:
        c = close[k]
        j = k
        while j + 1 <= k1 and close[j + 1] == c:
            j += 1
        runs.append([("close" if c else "pause"), k, j])
        k = j + 1
    merged = []
    for r in runs:      # short pauses belong to the close phase around them
        if r[0] == "pause" and (r[2] - r[1] + 1) * sdt < MIN_PAUSE_S and merged:
            merged[-1][2] = r[2]
            continue
        if merged and merged[-1][0] == r[0]:
            merged[-1][2] = r[2]
        else:
            merged.append(r)
    out = []
    for kind, i, j in merged:
        vals = [absgap[x] for x in range(i, j + 1) if absgap[x] is not None]
        out.append(Phase(kind, frames[i], frames[j] + SAMPLE - 1,
                         min(vals) if vals else None, max(vals) if vals else None,
                         sum(1 for x in range(i, j + 1) if side[x]) * sdt))
    return out


def _pair_battles(series, k_start, st, sdt):
    """[(k0, k1)] sample ranges of the battles of one pair."""
    out = []
    n = len(series)
    k = k_start
    while k < n:
        g = series[k]
        if g is None or not st.min_s <= abs(g) <= st.close_s:
            k += 1
            continue
        k0 = last_close = last_known = k
        close_total = 0
        j = k
        while j < n:
            g = series[j]
            if g is None:
                if (j - last_known) * sdt > DROPOUT_S:
                    break
            else:
                last_known = j
                ag = abs(g)
                if ag > st.battle_s:
                    break
                if st.min_s <= ag <= st.close_s:
                    last_close = j
                    close_total += 1
                elif (j - last_close) * sdt > st.max_pause_s:
                    break
            j += 1
        if close_total * sdt >= st.min_close_s:
            out.append((k0, last_close))
        k = max(j, last_close + 1)
    return out


def _flags(b, model, offs, contacts):
    replay = model.replay
    lo, hi = b.start, b.end
    for i in b.cars:
        if any(lo <= c <= hi for c in contacts.get(i, ())):
            b.contact = True
        if i in offs:
            if sum(1 for fr in offs[i] if lo <= fr <= hi) >= overtakes_mod.OFF_SAMPLES:
                b.off_track.append(i)
        else:
            dirt = replay.cars[i].tire_dirt_min
            if dirt is not None and overtakes_mod._run([v > 0 for v in dirt], lo, hi + 1,
                                                       overtakes_mod.OFF_FRAMES):
                b.off_track.append(i)
    if b.lapping:
        b.flags.append("lapping")
    if b.contact:
        b.flags.append("contact")
    for i in b.off_track:
        b.flags.append("%s off track" % replay.cars[i].driver)


def _summarise(b, pairs, gaps, frames, k0, k1, st, sdt, passes):
    """Fill phases and numbers of battle b from its pairs over samples k0..k1."""
    close = [False] * len(frames)
    side = [False] * len(frames)
    absgap = [None] * len(frames)
    lap = False
    for (a, c) in pairs:
        g, s, q = gaps.gaps[(a, c)], gaps.sides[(a, c)], gaps.laps[(a, c)]
        for k in range(k0, k1 + 1):
            if g[k] is None:
                continue
            ag = abs(g[k])
            if ag > st.battle_s:
                continue
            if absgap[k] is None or ag < absgap[k]:
                absgap[k] = ag
            if st.min_s <= ag <= st.close_s:
                close[k] = True
                if q[k] != 0:
                    lap = True
            if s[k]:
                side[k] = True
    b.lapping = lap
    b.phases = _phases(close, side, absgap, k0, k1, frames, sdt)
    closes = [k for k in range(k0, k1 + 1) if close[k]]
    b.close_s = len(closes) * sdt
    vals = [absgap[k] for k in closes]
    b.min_gap_s = min(vals) if vals else None
    b.mean_gap_s = sum(vals) / len(vals) if vals else None
    b.side_s = sum(1 for k in range(k0, k1 + 1) if side[k]) * sdt
    reds = [k for k in range(k0, k1 + 1) if close[k] and (side[k] or absgap[k] < RED_S)]
    b.red_s = len(reds) * sdt
    b.red_frames = [frames[k] for i, k in enumerate(reds) if i == 0 or reds[i - 1] != k - 1]
    sides = [k for k in range(k0, k1 + 1) if side[k]]
    if sides:
        b.best_frame = frames[sides[0]]
    elif closes:
        b.best_frame = frames[min(closes, key=lambda k: absgap[k])]
    lo, hi = frames[k0] - PASS_MARGIN, frames[k1] + PASS_MARGIN
    b.lead_changes = sorted(lc for pair in pairs for lc in gaps.lead_changes[pair]
                            if lo <= lc[0] <= hi)
    near = PASS_MATCH_S / sdt * SAMPLE
    b.passes = [o for o in passes
                if any(o.overtaker == w and o.passed == x and abs(o.frame - f) <= near
                       for f, w, x in b.lead_changes)]
    b.score = b.close_s + 2 * b.red_s + 3 * b.side_s + 10 * len(b.lead_changes)


def _start_end(model, cars, frames, st, dt):
    """Sample index where the start is over, and the frame the field started moving."""
    n = len(frames)
    k_move = next((k for k in range(n) if any(c.moving[k] for c in cars.values())), None)
    if k_move is None:
        return n, None
    # cap: leader finished lap 1
    cap_frame = None
    if model.line is not None and model.line.closed:
        for f in frames[k_move:]:
            if max(model.progress[i][f] for i in model.active) >= model.line.length:
                cap_frame = f
                break
    else:
        laps = [model.replay.cars[i].lap_index for i in model.active]
        if all(lp is not None for lp in laps):
            for f in frames[k_move:]:
                if any(lp[f] >= 1 for lp in laps):
                    cap_frame = f
                    break
    if cap_frame is None:
        cap_frame = min(model.frames - 1, frames[k_move] + int(START_MAX_S / dt))
    k_cap = min(n - 1, cap_frame // SAMPLE)
    act = list(model.active)
    for k in range(k_move + int(5.0 / (SAMPLE * dt)), k_cap + 1):
        f = frames[k]
        order = sorted(act, key=lambda i: -model.progress[i][f])
        group = best = 1
        for x, y in zip(order, order[1:]):
            gap_m = model.progress[x][f] - model.progress[y][f]
            lo, hi = max(0, f - 5), min(model.frames - 1, f + 5)
            speed = (cars[y].driven[hi] - cars[y].driven[lo]) / (max(1, hi - lo) * dt)
            if gap_m / max(speed, 10.0) <= st.battle_s:
                group += 1
                best = max(best, group)
            else:
                group = 1
        if best < min(START_GROUP, len(act)):
            return k, frames[k_move]
    return k_cap, frames[k_move]


def _train_notes(found, replay, sdt, st):
    """Battles that share a car and overlap in time without being one group."""
    fc = analysis.fmt_clock
    for x in found:
        for y in found:
            if x is y or not set(x.cars) & set(y.cars):
                continue
            lo, hi = max(x.start, y.start), min(x.end, y.end)
            if replay.time_s(hi) - replay.time_s(lo) < st.min_close_s:
                continue
            others = [replay.cars[i].driver for i in y.cars if i not in x.cars]
            if not others:
                continue
            x.flags.append("train with %s %s-%s" % (", ".join(others), fc(replay.time_s(lo)),
                                                    fc(replay.time_s(hi))))


@dataclass
class Measurement:
    """Everything that does not depend on the settings (the slow part)."""
    gaps: PairGaps
    frames: list
    cars: dict
    offs: dict
    contacts: dict
    overtakes: list
    notes: list


def measure(model, found_overtakes=None):
    replay = model.replay
    notes = []
    pit_check = None
    roadmap = model.roadmap
    if roadmap is not None and roadmap.pit:
        pit_check = lambda p: roadmap.on_pit(p[0], p[2])     # noqa: E731
    else:
        notes.append("No pit lane in the track model: battles end at a pit stop, not at the pit entry.")
    if model.line is None:
        notes.append("No racing line: battles with a lapped car are not found.")
    if found_overtakes is None:
        found_overtakes = overtakes_mod.find_overtakes(model)
    gaps, frames, cars = pair_gaps(model, SAMPLE, pit_check)
    offs = {}
    if roadmap is not None:
        offs = {i: surface.off_track_frames(roadmap, replay.cars[i], step=2) for i in model.active}
    contacts = {i: overtakes_mod.contact_frames(replay.cars[i]) for i in model.active}
    return Measurement(gaps, frames, cars, offs, contacts, found_overtakes, notes)


def find_battles(model, found_overtakes=None, settings=None, measured=None):
    """Battles with the given settings. Pass `measured` (from measure()) to
    try other settings without measuring again."""
    st = settings or Settings()
    replay = model.replay
    dt = replay.interval_ms / 1000.0
    sdt = SAMPLE * dt
    m = measured or measure(model, found_overtakes)
    gaps, frames, cars = m.gaps, m.frames, m.cars
    offs, contacts, found_overtakes = m.offs, m.contacts, m.overtakes
    notes = list(m.notes)
    k_start, move_frame = _start_end(model, cars, frames, st, dt)

    # battles per pair
    pair_ranges = []      # (k0, k1, (a, b))
    for key in gaps.gaps:
        for k0, k1 in _pair_battles(gaps.gaps[key], k_start, st, sdt):
            pair_ranges.append((k0, k1, key))

    # join pairs that share a car and overlap for at least min_close_s into groups
    parent = list(range(len(pair_ranges)))

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    need = st.min_close_s / sdt
    for x in range(len(pair_ranges)):
        for y in range(x + 1, len(pair_ranges)):
            a0, a1, ka = pair_ranges[x]
            b0, b1, kb = pair_ranges[y]
            both = min(a1, b1) - max(a0, b0)
            shorter = min(a1 - a0, b1 - b0)
            if set(ka) & set(kb) and both >= need and both >= GROUP_SHARE * shorter:
                parent[root(x)] = root(y)
    groups = {}
    for x in range(len(pair_ranges)):
        groups.setdefault(root(x), []).append(pair_ranges[x])

    out = []
    for members in groups.values():
        k0 = min(m[0] for m in members)
        k1 = max(m[1] for m in members)
        pairs = sorted({m[2] for m in members})
        f0 = frames[k0]
        cars_in = sorted({i for p in pairs for i in p}, key=lambda i: -model.progress[i][f0])
        f1 = frames[k1] + SAMPLE - 1
        f1 = min(f1, model.frames - 1)
        b = Battle("battle", cars_in, f0, f1, replay.time_s(f0), replay.time_s(f1),
                   (model.lap_at(cars_in[0], f0), model.lap_at(cars_in[0], f1)), pairs=pairs)
        _summarise(b, pairs, gaps, frames, k0, k1, st, sdt, found_overtakes)
        _flags(b, model, offs, contacts)
        if b.lapping and not st.show_lapping:
            continue
        out.append(b)
    _train_notes(out, replay, sdt, st)
    out.sort(key=lambda b: -b.score)

    result_battles = []
    if move_frame is not None and k_start > 0:
        f0, f1 = move_frame, frames[min(k_start, len(frames) - 1)]
        start = Battle("start", model.order_at(f0), f0, f1, replay.time_s(f0), replay.time_s(f1),
                       (1, model.lap_at(model.order_at(f1)[0], f1)))
        start.phases = [Phase("close", f0, f1)]
        start.lead_changes = sorted(lc for lcs in gaps.lead_changes.values() for lc in lcs
                                    if f0 <= lc[0] <= f1)
        start.passes = [o for o in found_overtakes if f0 <= o.frame <= f1]
        start.best_frame = f0
        _flags(start, model, offs, contacts)
        result_battles.append(start)
    result_battles.extend(out)
    return Result(result_battles, gaps, frames[min(k_start, len(frames) - 1)], st, notes)


# ------------------------------------------------------------ camera
def chaser_and_front(gaps, pairs, frame):
    """(car behind, car in front) of the pair of `pairs` that is closest at
    frame, or None when no gap is known."""
    best = None
    for a, b in pairs:
        g = gaps.gap(a, b, frame)
        if g is None:
            continue
        if best is None or abs(g) < best[0]:
            best = (abs(g), (b, a) if g >= 0 else (a, b))
    return best[1] if best else None


# ------------------------------------------------------------ text listing
def describe(result, model):
    replay = model.replay
    name = lambda i: replay.cars[i].driver      # noqa: E731
    fc = analysis.fmt_clock
    lines = []
    for b in result.battles:
        laps = "-" if b.laps[0] is None else (
            "lap %d" % b.laps[0] if b.laps[0] == b.laps[1] else "laps %d-%d" % b.laps)
        if b.kind == "start":
            lines.append("START  %s-%s  %s  %d cars, %d lead changes%s" % (
                fc(b.start_s), fc(b.end_s), laps, len(b.cars), len(b.lead_changes),
                ("  [" + ", ".join(b.flags) + "]") if b.flags else ""))
            continue
        lines.append("%-6.0f %s-%s  %5.1fs  %s  %s  close %.1fs  min %.2fs  mean %.2fs  "
                     "side %.1fs  red %.1fs  %d lead changes%s" % (
                         b.score, fc(b.start_s), fc(b.end_s), b.duration_s, " vs ".join(map(name, b.cars)),
                         laps, b.close_s, b.min_gap_s, b.mean_gap_s, b.side_s, b.red_s, len(b.lead_changes),
                         ("  [" + ", ".join(b.flags) + "]") if b.flags else ""))
        for p in b.phases:
            gap = "" if p.min_gap_s is None else "  %.2f-%.2fs" % (p.min_gap_s, p.max_gap_s)
            lines.append("         %-5s %s-%s  %5.1fs%s%s" % (
                p.kind, fc(replay.time_s(p.start)), fc(replay.time_s(p.end)),
                replay.time_s(p.end) - replay.time_s(p.start), gap,
                "  side %.1fs" % p.side_s if p.side_s else ""))
        listed = {(o.overtaker, o.passed) for o in b.passes}
        for f, w, x in b.lead_changes:
            lines.append("         lead change %s  %s gets past %s%s" % (
                fc(replay.time_s(f)), name(w), name(x),
                "" if (w, x) in listed else "  (not in the Overtakes list)"))
        if b.best_frame is not None:
            lines.append("         best moment %s, red moments: %s" % (
                fc(replay.time_s(b.best_frame)),
                ", ".join(fc(replay.time_s(f)) for f in b.red_frames[:12]) or "-"))
    return "\n".join(lines)


def main(argv):
    import acreplay
    if len(argv) < 2:
        print("usage: python battles.py replay.acreplay [game folder]")
        return 2
    replay = acreplay.load(argv[1])
    folder = argv[2] if len(argv) > 2 else None
    line, note = analysis.choose_line(replay, folder)
    roadmap, road_note = analysis.choose_roadmap(replay, folder)
    model = analysis.RaceModel(replay, line, roadmap)
    result = find_battles(model)
    print(note, road_note)
    for n in result.notes:
        print("note:", n)
    print(describe(result, model))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv))
