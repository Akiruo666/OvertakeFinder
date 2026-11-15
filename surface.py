"""Where is the tarmac? Point tests against the road and kerb meshes of a track.

Mesh names follow the game's convention: a number plus ROAD, KERB or PIT is a
driveable surface (tarmac, kerb, pit lane); GRASS, SAND, WALL and the rest are
not. A car is "off track" when all four wheels are off these surfaces.
"""
import math
import re

import kn5

CELL_M = 8.0
ROAD_RE = re.compile(r"^\d*(ROAD|KERB|PIT)", re.I)
# Wheel positions relative to the car's reference point (metres): along, across.
WHEELS = ((1.3, 0.8), (1.3, -0.8), (-1.3, 0.8), (-1.3, -0.8))


class RoadMap:
    def __init__(self, meshes):
        self.tris = []       # (ax, az, bx, bz, cx, cz)
        self.grid = {}
        for m in meshes:
            if not ROAD_RE.match(m["name"]):
                continue
            v = m["vertices"]
            for a, b, c in m["triangles"]:
                if a >= len(v) or b >= len(v) or c >= len(v):
                    continue
                t = (v[a][0], v[a][2], v[b][0], v[b][2], v[c][0], v[c][2])
                k = len(self.tris)
                self.tris.append(t)
                x0, x1 = int(min(t[0], t[2], t[4]) // CELL_M), int(max(t[0], t[2], t[4]) // CELL_M)
                z0, z1 = int(min(t[1], t[3], t[5]) // CELL_M), int(max(t[1], t[3], t[5]) // CELL_M)
                for gx in range(x0, x1 + 1):
                    for gz in range(z0, z1 + 1):
                        self.grid.setdefault((gx, gz), []).append(k)

    @classmethod
    def from_file(cls, path):
        _mats, meshes = kn5.read_model(path)
        rm = cls(meshes)
        if not rm.tris:
            raise kn5.Kn5Error("No road meshes found in %s." % path)
        return rm

    def on_road(self, x, z):
        for k in self.grid.get((int(x // CELL_M), int(z // CELL_M)), ()):
            ax, az, bx, bz, cx, cz = self.tris[k]
            d1 = (x - bx) * (az - bz) - (ax - bx) * (z - bz)
            d2 = (x - cx) * (bz - cz) - (bx - cx) * (z - cz)
            d3 = (x - ax) * (cz - az) - (cx - ax) * (z - az)
            neg = d1 < 0 or d2 < 0 or d3 < 0
            pos = d1 > 0 or d2 > 0 or d3 > 0
            if not (neg and pos):
                return True
        return False

    def wheels_on_road(self, pos, heading):
        """Number of wheels (0-4) on a driveable surface. `heading` = (dx, dz) unit."""
        hx, hz = heading
        n = 0
        for along, across in WHEELS:
            x = pos[0] + hx * along - hz * across
            z = pos[2] + hz * along + hx * across
            if self.on_road(x, z):
                n += 1
        return n


def heading_at(positions, f, span=3):
    a = positions[max(0, f - span)]
    b = positions[min(len(positions) - 1, f + span)]
    dx, dz = b[0] - a[0], b[2] - a[2]
    d = math.hypot(dx, dz)
    return (dx / d, dz / d) if d > 0.05 else None


def fit_fraction(roadmap, replay, max_cars=5, samples=300):
    """Share of moving samples whose car centre is on a road/kerb surface
    (None if nothing moves). Rejects a model of another track."""
    on = total = 0
    cars = [c for c in replay.cars if c.has_positions]
    cars.sort(key=lambda c: -max(c.speed_kmh or [0.0]))
    for car in cars[:max_cars]:
        k = max(1, car.frame_count // samples)
        for f in range(k, car.frame_count, k):
            if (car.speed_kmh[f] if car.speed_kmh else 0) > 20:
                total += 1
                p = car.positions[f]
                if roadmap.on_road(p[0], p[2]):
                    on += 1
    return (on / total) if total else None


def off_track_frames(roadmap, car, step=2):
    """Frames (every `step`) where all four wheels are off the driveable surface."""
    out = set()
    pos = car.positions
    for f in range(0, len(pos), step):
        h = heading_at(pos, f)
        if h is None:
            continue
        if roadmap.wheels_on_road(pos[f], h) == 0:
            out.add(f)
    return out
