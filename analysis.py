"""Race overview calculations built on parsed replay data.

Without the track centreline we cannot know exactly how far along the lap a car
is, so "progress" is measured as the distance a car has driven since the start,
shifted by its grid slot. For cars that stay mostly on the racing line this is
accurate to within a percent or two; sideways movement adds a little error.
"""
import math
from dataclasses import dataclass


@dataclass
class Standing:
    position: int
    car_index: int
    driver: str
    progress_m: float
    gap_ahead_m: float
    gap_leader_m: float


def driven_distance(positions):
    """Cumulative distance driven at every frame (list of metres)."""
    total = 0.0
    out = [0.0]
    for a, b in zip(positions, positions[1:]):
        total += math.dist(a, b)
        out.append(total)
    return out


def grid_offsets(replay):
    """How far behind the front-most car each car starts (metres, <= 0).

    The start is assumed to be a straight, so the offset is the distance from
    each car's first position to the front-most one projected onto the
    direction the field first moves in.
    """
    n = replay.frame_count
    starts = [c.positions[0] for c in replay.cars]
    # Direction of travel: average movement of all cars over the first
    # frames in which anybody has moved a few metres.
    direction = (0.0, 0.0)
    for f in range(1, n):
        dx = sum(c.positions[f][0] - c.positions[0][0] for c in replay.cars)
        dz = sum(c.positions[f][2] - c.positions[0][2] for c in replay.cars)
        if math.hypot(dx, dz) > 10 * len(replay.cars):
            norm = math.hypot(dx, dz)
            direction = (dx / norm, dz / norm)
            break
    if direction == (0.0, 0.0):
        return [0.0] * len(starts)
    along = [s[0] * direction[0] + s[2] * direction[1] for s in starts]
    front = max(along)
    return [a - front for a in along]


class RaceModel:
    """Pre-computed progress for each car at every frame."""

    def __init__(self, replay):
        self.replay = replay
        self.frames = replay.frame_count
        offsets = grid_offsets(replay)
        self.progress = []
        for car, off in zip(replay.cars, offsets):
            dist = driven_distance(car.positions[:self.frames])
            self.progress.append([d + off for d in dist])

    def standings(self, frame):
        frame = max(0, min(frame, self.frames - 1))
        order = sorted(range(len(self.replay.cars)),
                       key=lambda i: self.progress[i][frame], reverse=True)
        leader = self.progress[order[0]][frame]
        rows = []
        prev = leader
        for rank, i in enumerate(order, start=1):
            p = self.progress[i][frame]
            rows.append(Standing(rank, i, self.replay.cars[i].driver, p,
                                 0.0 if rank == 1 else prev - p, leader - p))
            prev = p
        return rows

    def gap_to_leader_series(self):
        """For each car a list with its gap to the leader (metres) per frame."""
        series = [[] for _ in self.replay.cars]
        for f in range(self.frames):
            lead = max(p[f] for p in self.progress)
            for i, p in enumerate(self.progress):
                series[i].append(lead - p[f])
        return series


def classification(replay):
    """Cars sorted by laps completed (desc) then total time (asc)."""
    return sorted(replay.cars,
                  key=lambda c: (-len(c.laps), c.total_time_ms if c.laps else 10**12))


def fmt_lap(ms):
    if ms is None:
        return "-"
    minutes, rest = divmod(int(ms), 60_000)
    return "%d:%06.3f" % (minutes, rest / 1000)
