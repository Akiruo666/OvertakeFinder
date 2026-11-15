"""Finding overtakes from the progress of every car.

Two cars are side by side when their progress difference d is a multiple of
the lap length (0 = same lap; +-1 = one car is a lap ahead). An overtake is
the moment d - q*L (q = nearest multiple) goes from clearly negative to
clearly positive, i.e. one car gets in front of another on the road. With
q != 0 one of the cars is lapped (needs the lap length, so a racing line).

Every overtake is kept and marked, not filtered, because what counts is up to
the viewer:
  lapping            one of the two cars is a lap or more behind the other
  overtaker off      the overtaker had all four wheels off the tarmac
  overtaken off      the overtaken car had all four wheels off the tarmac
  limit              a car was at the edge of the track (wall/boundary) -
                     only with a racing line
One or two wheels over the white line is legal and is not marked.
"""
from dataclasses import dataclass, field

DEADBAND_M = 3.0        # a car must be this far in front before it counts as passed
MAX_GAP_M = 60.0        # ignore "passes" between cars that were never this close
WINDOW_S = 3.0          # off-track events within this time of the pass count
OFF_FRAMES = 5          # all four wheels off for at least this many frames (150 ms)
LIMIT_MARGIN_M = 0.3    # distance to the edge that counts as "at the limit"
LIMIT_FRAMES = 10


@dataclass
class Overtake:
    frame: int
    time_s: float
    lap: int                 # lap of the overtaker (None if unknown)
    overtaker: int           # car index
    passed: int
    lapping: bool = False
    overtaker_off: bool = False
    passed_off: bool = False
    at_limit: bool = False
    flags: list = field(default_factory=list)

    @property
    def clean(self):
        return not (self.lapping or self.overtaker_off or self.passed_off or self.at_limit)


def _run(values, lo, hi, minimum):
    """True if `values[lo:hi]` has a run of >= `minimum` truthy items."""
    run = 0
    for v in values[lo:hi]:
        run = run + 1 if v else 0
        if run >= minimum:
            return True
    return False


def find_overtakes(model, sample=3):
    replay = model.replay
    length = model.line.length if (model.line is not None and model.line.closed) else None
    active = model.active
    n = model.frames
    frames = list(range(0, n, sample))
    window = int(WINDOW_S / (replay.interval_ms / 1000.0))
    found = []
    for ai in range(len(active)):
        for bi in range(ai + 1, len(active)):
            a, b = active[ai], active[bi]
            pa, pb = model.progress[a], model.progress[b]
            state = 0            # +1: a ahead of b, -1: behind, 0: not yet known
            q_state = None
            last_cross = None
            prev_e = None
            for f in frames:
                d = pa[f] - pb[f]
                q = round(d / length) if length else 0
                e = d - q * length if length else d
                if q_state is not None and q != q_state:
                    # moved to another multiple without crossing: reset
                    state, last_cross = 0, None
                q_state = q
                if prev_e is not None and (prev_e < 0) != (e < 0) and abs(e) < MAX_GAP_M:
                    last_cross = f
                prev_e = e
                new = 1 if e > DEADBAND_M else -1 if e < -DEADBAND_M else 0
                if new == 0 or new == state:
                    continue
                if state != 0 and last_cross is not None and abs(e) < MAX_GAP_M * 2:
                    if new == 1:
                        found.append((last_cross, a, b, q))
                    else:
                        found.append((last_cross, b, a, -q))
                state = new
                last_cross = None
    found.sort()
    out = []
    for f, over, under, q in found:
        f = min(f, n - 1)
        lo, hi = max(0, f - window), min(n, f + window)
        o = Overtake(f, replay.time_s(f), model.lap_at(over, f), over, under, lapping=(q != 0))
        for who, attr in ((over, "overtaker_off"), (under, "passed_off")):
            dirt = replay.cars[who].tire_dirt_min
            if dirt is not None and _run([v > 0 for v in dirt], lo, hi, OFF_FRAMES):
                setattr(o, attr, True)
        if model.line is not None and model.line.has_widths:
            for who in (over, under):
                if _at_limit(model, who, lo, hi):
                    o.at_limit = True
        if o.lapping:
            o.flags.append("lapping")
        if o.overtaker_off:
            o.flags.append("overtaker off track")
        if o.passed_off:
            o.flags.append("passed car off track")
        if o.at_limit:
            o.flags.append("car at track limit")
        out.append(o)
    return out


def _at_limit(model, car, lo, hi):
    line = model.line
    lat = model.lateral[car]
    prog = model.progress[car]
    if lat is None:
        return False
    flags = []
    for f in range(lo, hi):
        i = int(((prog[f] % line.length) / line.length) * line.n) % line.n
        edge = line.left[i] if lat[f] > 0 else line.right[i]
        flags.append(edge > 0 and abs(lat[f]) >= edge - LIMIT_MARGIN_M)
    return _run(flags, 0, len(flags), LIMIT_FRAMES)
