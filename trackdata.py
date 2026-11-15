"""Track data from the Assetto Corsa installation: the AI racing line.

Every track that supports AI has a file `fast_lane.ai`. Layout (little endian,
taken from community tools; checked on two real files):

  int32 version (7), int32 point_count, int32, int32
  point_count * (float32 x, y, z, distance_along_line, int32 index)       20 bytes
  point_count * 18 * float32 "details"; index 6 = distance from the line to
                the left edge, index 7 = to the right edge (metres)
  (more data may follow in newer files; it is ignored)

x, y, z use the same world coordinates as the positions in a replay, so a car's
position can be projected onto the line. That gives the distance along the lap,
which is exact where "distance driven" is only an estimate, and it gives the
sideways offset from the line.

Some tracks have no AI line or a broken one. This module therefore validates
the file, and checks the line against the replay (fit_fraction) before it is used.
"""
import math
import os
import struct
from dataclasses import dataclass

# A car this close behind the start line when the recording starts is on the
# grid: its progress starts negative, so that lap 1 begins at the line.
GRID_ZONE_M = 250.0
# Maximum distance (m) to the line before a car is considered lost and the
# whole line is searched again.
RELOCATE_M = 40.0
# Which sign of the cross product means "left of the line" (checked on real data).
LEFT_SIGN = -1.0


class TrackDataError(Exception):
    """The AI line file is missing, damaged or unusable."""


@dataclass
class AiLine:
    path: str
    xs: list
    ys: list
    zs: list
    cum: list          # cumulative distance of each point along the line (m)
    left: list         # distance to the left edge per point (m)
    right: list
    has_widths: bool
    closed: bool
    length: float      # lap length for a closed line, else the line length
    spacing: float     # median distance between points

    @property
    def n(self):
        return len(self.xs)


@dataclass
class CarTrack:
    """A car followed along the line, one value per replay frame."""
    progress: list     # metres along the line, unwrapped over laps; <0 on the grid
    lateral: list      # metres from the line; positive = left of the line


# ------------------------------------------------------------------ loading
def load_ai_line(path):
    try:
        with open(path, "rb") as fh:
            buf = fh.read()
    except OSError as exc:
        raise TrackDataError("Cannot read %s: %s" % (path, exc)) from exc
    if len(buf) < 16:
        raise TrackDataError("The AI line file is too small.")
    version, n, _u1, _u2 = struct.unpack_from("<4i", buf, 0)
    if not (1 <= version <= 100) or not (50 <= n <= 200_000):
        raise TrackDataError("Unexpected AI line header (version %d, %d points)." % (version, n))
    if len(buf) < 16 + 20 * n:
        raise TrackDataError("The AI line file is cut off (%d points announced)." % n)

    xs, ys, zs = [], [], []
    for x, y, z, _d, _i in struct.iter_unpack("<4fi", buf[16:16 + 20 * n]):
        if any(v != v or abs(v) > 1e6 for v in (x, y, z)):
            raise TrackDataError("The AI line contains invalid coordinates.")
        xs.append(x)
        ys.append(y)
        zs.append(z)

    has_widths = len(buf) >= 16 + 20 * n + 72 * n
    left, right = [0.0] * n, [0.0] * n
    if has_widths:
        base = 16 + 20 * n
        for i in range(n):
            left[i], right[i] = struct.unpack_from("<2f", buf, base + 72 * i + 24)

    cum = [0.0]
    steps = []
    for i in range(1, n):
        d = math.sqrt((xs[i] - xs[i - 1]) ** 2 + (ys[i] - ys[i - 1]) ** 2 + (zs[i] - zs[i - 1]) ** 2)
        steps.append(d)
        cum.append(cum[-1] + d)
    ordered = sorted(steps)
    spacing = ordered[len(ordered) // 2]
    if spacing <= 0.05 or ordered[-1] > 80:
        raise TrackDataError("The AI line has holes or duplicate points (largest gap %.0f m)." % ordered[-1])
    closing = math.dist((xs[-1], ys[-1], zs[-1]), (xs[0], ys[0], zs[0]))
    closed = closing <= max(3 * spacing, 10.0)
    length = cum[-1] + (closing if closed else 0.0)
    if length < 200:
        raise TrackDataError("The AI line is only %.0f m long." % length)
    return AiLine(path, xs, ys, zs, cum, left, right, has_widths, closed, length, spacing)


# ----------------------------------------------------------------- tracking
def _global_nearest(line, x, y, z):
    xs, ys, zs = line.xs, line.ys, line.zs
    best, best_d = 0, 1e30
    for i in range(line.n):
        dx, dy, dz = x - xs[i], y - ys[i], z - zs[i]
        d = dx * dx + dy * dy + dz * dz
        if d < best_d:
            best, best_d = i, d
    return best, best_d


def _segment(line, i, j, x, z):
    """Project (x, z) on the segment from point i to j. Returns (s, lateral, d2)."""
    xs, zs = line.xs, line.zs
    ax, az = xs[i], zs[i]
    dx, dz = xs[j] - ax, zs[j] - az
    l2 = dx * dx + dz * dz
    t = 0.0 if l2 == 0.0 else ((x - ax) * dx + (z - az) * dz) / l2
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    ex, ez = x - (ax + t * dx), z - (az + t * dz)
    d2 = ex * ex + ez * ez
    side = LEFT_SIGN if dx * ez - dz * ex > 0 else -LEFT_SIGN
    if j > i:
        s = line.cum[i] + t * (line.cum[j] - line.cum[i])
    else:  # closing segment last -> first
        s = line.cum[i] + t * (line.length - line.cum[i])
    return s, side * math.sqrt(d2), d2


def track_car(line, positions, step=1, reach_m=None):
    """Follow one car along the line. `step` > 1 samples every step-th frame
    (faster) and interpolates the frames in between. `reach_m` is the furthest
    a car can move between two consecutive samples (default: from `step`)."""
    n_frames = len(positions)
    frames = list(range(0, n_frames, step))
    if frames[-1] != n_frames - 1:
        frames.append(n_frames - 1)
    n = line.n
    closed, length = line.closed, line.length
    if reach_m is None:
        reach_m = 3.5 * step          # 3.5 m per 30 ms frame is about 420 km/h
    fwd = int(reach_m / max(line.spacing, 0.5)) + 4
    back = fwd // 2 + 2
    xs, ys, zs = line.xs, line.ys, line.zs
    reloc2 = RELOCATE_M * RELOCATE_M

    idx = None
    s_unw = prev_within = 0.0
    out_s, out_l = [], []
    for k, f in enumerate(frames):
        x, y, z = positions[f]
        found = None
        if idx is not None:
            best, best_d = idx, 1e30
            for off in range(-back, fwd + 1):
                i = idx + off
                if closed:
                    if i >= n:
                        i -= n
                    elif i < 0:
                        i += n
                elif i < 0 or i >= n:
                    continue
                dx, dy, dz = x - xs[i], y - ys[i], z - zs[i]
                d = dx * dx + dy * dy + dz * dz
                if d < best_d:
                    best, best_d = i, d
            if best_d <= reloc2:
                found = best
        if found is None:
            found, _ = _global_nearest(line, x, y, z)
        idx = found
        # Refine on the two segments around the nearest point.
        cands = []
        if closed:
            cands.append(_segment(line, (idx - 1) % n, idx, x, z))
            cands.append(_segment(line, idx, (idx + 1) % n, x, z))
        else:
            if idx > 0:
                cands.append(_segment(line, idx - 1, idx, x, z))
            if idx < n - 1:
                cands.append(_segment(line, idx, idx + 1, x, z))
        s_within, lateral, _d2 = min(cands, key=lambda c: c[2])
        if k == 0:
            s_unw = prev_within = s_within
        elif closed:
            d = s_within - prev_within
            if d > length / 2:
                d -= length
            elif d < -length / 2:
                d += length
            s_unw += d
            prev_within = s_within
        else:
            s_unw = s_within
        out_s.append(s_unw)
        out_l.append(lateral)

    if closed and out_s and prev_within is not None:
        first_within = out_s[0]
        if first_within > length - GRID_ZONE_M:      # on the grid, behind the line
            out_s = [v - length for v in out_s]

    if step == 1:
        return CarTrack(out_s, out_l)
    prog, lat = [0.0] * n_frames, [0.0] * n_frames
    for k in range(len(frames) - 1):
        f0, f1 = frames[k], frames[k + 1]
        span = f1 - f0
        for f in range(f0, f1):
            a = (f - f0) / span
            prog[f] = out_s[k] + (out_s[k + 1] - out_s[k]) * a
            lat[f] = out_l[k] + (out_l[k + 1] - out_l[k]) * a
    prog[-1], lat[-1] = out_s[-1], out_l[-1]
    return CarTrack(prog, lat)


def quick_fit(line, replay, max_cars=5, samples=300, max_offset=30.0):
    """Cheap check whether `line` belongs to the track that was recorded.

    Looks at a few cars, at about `samples` moments each, and returns the share
    of moving samples that lie within `max_offset` metres of the line (None if
    nothing moves). A line of another track or layout, or a broken one, scores
    close to 0 and is rejected without tracking every car frame by frame."""
    near = total = 0
    cars = [c for c in replay.cars if c.has_positions]
    cars.sort(key=lambda c: -max(c.speed_kmh or [0.0]))
    for car in cars[:max_cars]:
        k = max(1, car.frame_count // samples)
        pts = car.positions[::k]
        trk = track_car(line, pts, 1, reach_m=3.5 * k)
        for i in range(1, len(pts)):
            if math.dist(pts[i], pts[i - 1]) > 1.0:
                total += 1
                if abs(trk.lateral[i]) <= max_offset:
                    near += 1
    return (near / total) if total else None


def fit_fraction(line, tracks, replay, max_offset=30.0):
    """Share of moving samples that lie within `max_offset` metres of the line.

    A line of another track or layout (or a broken one) scores close to 0."""
    near = total = 0
    for car, trk in zip(replay.cars, tracks):
        if trk is None:
            continue
        pos = car.positions
        for f in range(0, len(trk.lateral), 5):
            moving = (car.speed_kmh[f] > 20) if car.speed_kmh is not None else (
                f >= 5 and math.dist(pos[f], pos[f - 5]) > 0.5)
            if moving:
                total += 1
                if abs(trk.lateral[f]) <= max_offset:
                    near += 1
    return (near / total) if total else None
