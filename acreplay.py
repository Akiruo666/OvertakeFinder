"""Reader for Assetto Corsa .acreplay files.

The format is not officially documented. This layout was reverse engineered
(see skiv23/assetto-corsa-replay-merger) and extended by inspecting a
format-version-16 race replay:

  uint32 version, uint32 unknown, float32 unknown
  str weather, str track, str layout            (uint32 length + UTF-8)
  uint32 num_cars, uint32 num_frames, uint32 num_intervals
  uint32[num_intervals] interval_table
  per car:
    uint32 unknown
    str car_model, str driver, str nation, str team, str skin
    uint32 frame_count, uint32 wheel_field
    byte[16] padding
    (frame_count - 1) frames of `frame_size` bytes
    final frame: (frame_size - 16) fixed bytes followed by a variable length
                 lap list of (lap_number, lap_time_ms) uint32 pairs
  trailer: INI text starting with [BENCHMARK]

Each frame starts with an int32 flag word followed by the body position as
three float32 values (x, y, z in metres).
"""
import math
import struct
from dataclasses import dataclass, field

CANDIDATE_FRAME_SIZES = (312, 292)


class ReplayError(Exception):
    """Raised when a file does not look like a replay we can read."""


@dataclass
class Car:
    index: int
    model: str
    driver: str
    nation: str
    team: str
    skin: str
    frame_count: int
    laps: list = field(default_factory=list)  # [(lap_number, lap_time_ms)], ascending
    positions: list = field(default_factory=list, repr=False)  # [(x, y, z)] per frame

    @property
    def total_time_ms(self):
        return sum(ms for _, ms in self.laps)

    @property
    def best_lap_ms(self):
        return min((ms for _, ms in self.laps), default=None)


@dataclass
class Replay:
    path: str
    version: int
    weather: str
    track: str
    layout: str
    frame_size: int
    intervals: tuple
    cars: list
    trailer: bytes

    @property
    def frame_count(self):
        return min((c.frame_count for c in self.cars), default=0)


def _read_str(buf, pos):
    (n,) = struct.unpack_from("<I", buf, pos)
    if n > 4096 or pos + 4 + n > len(buf):
        raise ReplayError("Unexpected string length %d at offset %d" % (n, pos))
    return buf[pos + 4:pos + 4 + n].decode("utf-8", "replace"), pos + 4 + n


def _read_lap_pairs(buf, pos):
    laps = []
    while pos + 8 <= len(buf):
        lap, ms = struct.unpack_from("<II", buf, pos)
        if not (1 <= lap <= 500 and 10_000 <= ms <= 3_600_000):
            break
        laps.append((lap, ms))
        pos += 8
    laps.sort()
    return laps, pos


def _positions_look_valid(buf, start, frame_size, count):
    """Frames are valid if consecutive positions are never far apart."""
    prev = None
    for i in range(count):
        o = start + i * frame_size
        if o + 16 > len(buf):
            return False
        p = struct.unpack_from("<3f", buf, o + 4)
        if any(math.isnan(v) or abs(v) > 1e5 for v in p):
            return False
        if prev is not None and math.dist(prev, p) > 25:
            return False
        prev = p
    return True


def _read_positions(buf, start, frame_size, count):
    unpack = struct.Struct("<3f").unpack_from
    return [unpack(buf, start + i * frame_size + 4) for i in range(count)]


def load(path):
    with open(path, "rb") as fh:
        buf = fh.read()
    if len(buf) < 100:
        raise ReplayError("File is too small to be a replay.")
    try:
        pos = 0
        version, _unknown = struct.unpack_from("<II", buf, pos)
        pos += 12  # includes one float32 of unknown meaning
        weather, pos = _read_str(buf, pos)
        track, pos = _read_str(buf, pos)
        layout, pos = _read_str(buf, pos)
        num_cars, _num_frames, num_iv = struct.unpack_from("<III", buf, pos)
        pos += 12
        if not (1 <= num_cars <= 200) or num_iv > 5_000_000:
            raise ReplayError("Header values look wrong (cars=%d)." % num_cars)
        intervals = struct.unpack_from("<%dI" % num_iv, buf, pos)
        pos += 4 * num_iv

        # Locate the first car's frame data so the frame size can be verified.
        probe = pos + 4
        for _ in range(5):
            _, probe = _read_str(buf, probe)
        probe_count = struct.unpack_from("<I", buf, probe)[0]
        probe += 8 + 16
        frame_size = None
        for size in CANDIDATE_FRAME_SIZES:
            if _positions_look_valid(buf, probe, size, min(300, probe_count - 1)):
                frame_size = size
                break
        if frame_size is None:
            raise ReplayError(
                "Unsupported replay layout (format version %d). "
                "Frame data could not be recognised." % version)

        cars = []
        for index in range(num_cars):
            pos += 4
            model, pos = _read_str(buf, pos)
            driver, pos = _read_str(buf, pos)
            nation, pos = _read_str(buf, pos)
            team, pos = _read_str(buf, pos)
            skin, pos = _read_str(buf, pos)
            frame_count, _wheels = struct.unpack_from("<II", buf, pos)
            pos += 8 + 16
            positions = _read_positions(buf, pos, frame_size, frame_count)
            body_end = pos + (frame_count - 1) * frame_size + (frame_size - 16)
            laps, end = _read_lap_pairs(buf, body_end)
            cars.append(Car(index, model, driver, nation, team, skin,
                            frame_count, laps, positions))
            pos = end
    except struct.error as exc:
        raise ReplayError("The file ended unexpectedly: %s" % exc) from exc

    return Replay(path, version, weather, track, layout, frame_size,
                  intervals, cars, buf[pos:])
