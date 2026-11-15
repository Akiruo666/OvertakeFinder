"""Race overview calculations built on parsed replay data.

Without the track centreline we cannot know exactly how far along the lap a car
is, so "progress" is measured as the distance a car has driven since the start,
shifted by its grid slot. For cars that stay mostly on the racing line this is
accurate to within a percent or two; sideways movement adds a little error.
"""
import math
import os
from dataclasses import dataclass

import gamefolder
import trackdata

# Smallest share of the recorded driving that must lie close to a racing line
# for the line to be used (see trackdata.quick_fit).
FIT_MIN = 0.6

# A car cannot move this far in one recording step (30 ms -> 500 m/s); larger
# jumps are teleports (pit reset, replay glitch) and are not counted as driving.
MAX_STEP_M = 15.0


@dataclass
class Standing:
    position: int
    car_index: int
    driver: str
    progress_m: float
    gap_ahead_m: float
    gap_leader_m: float
    lap: int = None          # 1-based, None in limited mode
    speed_kmh: float = None


def driven_distance(positions):
    """Cumulative distance driven at every frame (list of metres)."""
    total = 0.0
    out = [0.0]
    for a, b in zip(positions, positions[1:]):
        d = math.dist(a, b)
        if d <= MAX_STEP_M:
            total += d
        out.append(total)
    return out


def grid_offsets(replay):
    """How far behind the front-most car each car starts (metres, <= 0).

    The start is assumed to be a straight, so the offset is the distance from
    each car's first position to the front-most one projected onto the
    direction the field first moves in.
    """
    n = replay.frame_count
    cars = [c for c in replay.cars if c.has_positions]
    if not cars:
        return [0.0] * len(replay.cars)
    direction = (0.0, 0.0)
    for f in range(1, n):
        dx = sum(c.positions[f][0] - c.positions[0][0] for c in cars)
        dz = sum(c.positions[f][2] - c.positions[0][2] for c in cars)
        if math.hypot(dx, dz) > 10 * len(cars):
            norm = math.hypot(dx, dz)
            direction = (dx / norm, dz / norm)
            break
    if direction == (0.0, 0.0):
        return [0.0] * len(replay.cars)
    along = {c.index: c.positions[0][0] * direction[0] + c.positions[0][2] * direction[1]
             for c in cars}
    front = max(along.values())
    return [along[c.index] - front if c.index in along else 0.0 for c in replay.cars]


def choose_line(replay, game_folder):
    """Find the AI racing line of the replay's track in the game folder.

    Returns (line, note). `line` is None when there is no usable one (no game
    folder set, track not installed, track without AI line, damaged file, or a
    line that does not match the recorded driving); `note` says why, or which
    file was used."""
    fallback = " Gaps are estimated from the distance driven."
    if not game_folder:
        return None, "No game folder set." + fallback + " Use 'Game folder...' to set it."
    if not replay.track or replay.track == "?":
        return None, "The replay does not name its track." + fallback
    paths, message = gamefolder.find_ai_lines(game_folder, replay.track, replay.layout)
    if not paths:
        return None, message + fallback
    base = os.path.join(game_folder, "content", "tracks")
    problems = []
    for path in paths[:6]:
        name = os.path.relpath(path, base)
        try:
            line = trackdata.load_ai_line(path)
        except trackdata.TrackDataError as exc:
            problems.append("%s: %s" % (name, exc))
            continue
        fit = trackdata.quick_fit(line, replay)
        if fit is not None and fit < FIT_MIN:
            problems.append("%s does not match the recorded driving (%.0f%% fits)" % (name, 100 * fit))
            continue
        return line, "Racing line: %s (%.0f m)." % (name, line.length)
    return None, "No usable racing line for this track (%s)." % "; ".join(problems) + fallback


class RaceModel:
    """Pre-computed progress for each car at every frame.

    With a racing line (`line`), progress is the exact distance along the lap
    (negative on the starting grid, growing over laps) and laps are counted
    from the start line. Without one it is the distance driven, corrected for
    the starting grid.

    Cars without any position data (every frame empty) are not part of the
    model: `progress` and the gap series hold None for them.
    """

    def __init__(self, replay, line=None):
        self.replay = replay
        self.frames = replay.frame_count
        self.race_laps = max((len(c.lap_times_ms) for c in replay.cars), default=0)
        self.active = [c.index for c in replay.cars if c.has_positions]
        self.line = line
        self.method = "racing line" if line is not None else "distance driven"
        # Do not show laps above what the game counted, when it counted for everybody.
        all_counted = all(replay.cars[i].lap_index is not None for i in self.active)
        self.lap_cap = self.race_laps if (self.race_laps and all_counted) else 0
        self.lateral = [None] * len(replay.cars)
        self.progress = []
        if line is not None:
            step = 1 if len(self.active) * self.frames <= 300_000 else 3
            for car in replay.cars:
                if not car.has_positions:
                    self.progress.append(None)
                    continue
                track = trackdata.track_car(line, car.positions[:self.frames], step)
                self.progress.append(track.progress)
                self.lateral[car.index] = track.lateral
        else:
            offsets = grid_offsets(replay)
            for car, off in zip(replay.cars, offsets):
                if not car.has_positions:
                    self.progress.append(None)
                    continue
                dist = driven_distance(car.positions[:self.frames])
                self.progress.append([d + off for d in dist])

    def time_s(self, frame):
        return self.replay.time_s(frame)

    def lap_at(self, car_index, frame):
        car = self.replay.cars[car_index]
        if self.line is not None and self.line.closed and self.progress[car_index] is not None:
            lap = max(1, int(self.progress[car_index][frame] // self.line.length) + 1)
            return min(lap, self.lap_cap) if self.lap_cap else lap
        if car.lap_index is None:      # no lap information recorded for this car
            return None
        lap = car.lap_index[frame] + 1
        return min(lap, self.race_laps) if self.race_laps else lap

    def standings(self, frame):
        frame = max(0, min(frame, self.frames - 1))
        order = sorted(self.active, key=lambda i: self.progress[i][frame], reverse=True)
        leader = self.progress[order[0]][frame]
        rows = []
        prev = leader
        for rank, i in enumerate(order, start=1):
            car = self.replay.cars[i]
            p = self.progress[i][frame]
            speed = car.speed_kmh[frame] if car.speed_kmh is not None else None
            rows.append(Standing(rank, i, car.driver, p,
                                 0.0 if rank == 1 else prev - p, leader - p,
                                 self.lap_at(i, frame), speed))
            prev = p
        return rows

    def gap_to_leader_series(self):
        """Per car a list with its gap to the leader (metres) per frame.

        Cars without position data get None instead of a list.
        """
        series = [[] if p is not None else None for p in self.progress]
        for f in range(self.frames):
            lead = max(self.progress[i][f] for i in self.active)
            for i in self.active:
                series[i].append(lead - self.progress[i][f])
        return series


def classification(replay, final_progress=None):
    """Cars sorted by laps completed (desc), total time (asc), then distance driven."""
    def key(c):
        has = final_progress is not None and final_progress[c.index] is not None
        dist = -final_progress[c.index] if has else 0
        return (not c.has_positions,       # cars without data go last
                -len(c.lap_times_ms),
                c.total_time_ms if c.lap_times_ms else 10**12,
                dist)
    return sorted(replay.cars, key=key)


# ------------------------------------------------------------------- units
METRIC = "metric"
IMPERIAL = "imperial"
FT_PER_M = 3.280839895
FT_PER_MILE = 5280.0
MPH_PER_KMH = 0.621371192
# (short length, long length, speed)
UNIT_NAMES = {METRIC: ("m", "km", "km/h"), IMPERIAL: ("ft", "mi", "mph")}


def short_length(metres, system):
    """Metres, or feet for the imperial system (gaps, short distances)."""
    return metres if system == METRIC else metres * FT_PER_M


def long_length(metres, system):
    """Kilometres, or miles for the imperial system."""
    return metres / 1000.0 if system == METRIC else metres * FT_PER_M / FT_PER_MILE


def speed_value(kmh, system):
    """km/h, or mph for the imperial system."""
    return kmh if system == METRIC else kmh * MPH_PER_KMH


def fmt_lap(ms):
    if ms is None:
        return "-"
    minutes, rest = divmod(int(ms), 60_000)
    return "%d:%06.3f" % (minutes, rest / 1000)


def fmt_clock(seconds):
    """Replay clock, e.g. 1:23.4 (hours only when needed)."""
    tenths = int(round(seconds * 10))
    minutes, rest = divmod(tenths, 600)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return "%d:%02d:%04.1f" % (hours, minutes, rest / 10)
    return "%d:%04.1f" % (minutes, rest / 10)
