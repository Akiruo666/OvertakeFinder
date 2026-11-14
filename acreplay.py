"""Reader for Assetto Corsa .acreplay files.

The format is not officially documented. The layout below was reverse engineered
by the community (see abchouhan/acreplay-parser, which this module's field
offsets follow) and checked against a real race replay.

Format version 16 layout (all little endian, strings = uint32 length + UTF-8):

  uint32  version
  float64 recording interval in milliseconds (30.0 in the files seen so far)
  str     weather, track, track layout
  uint32  num_cars, recording_index, num_frames, num_track_objects
  byte[(4 + 12 * num_track_objects) * num_frames]   sun angle / object data
  per car:
    str    car model, driver, nation, team, skin
    uint32 frame_count, num_wings
    byte[20]
    frame_count frames, each  256 bytes CarFrame + 20 + 4 * num_wings bytes
                              (the very last frame has no 20 byte gap)
    uint32 lap_count, then lap_count * (uint32 lap_time_ms, uint32 flag)
  trailer (Custom Shaders Patch data, ignored)

Because the frame size depends on num_wings and the header size on
num_track_objects, nothing here is a fixed size. load() tries the exact layout
first, then a scanning reader that searches the file for car blocks, so that
files in a slightly different layout can still be opened in a limited mode.
"""
import math
import re
import struct
from dataclasses import dataclass, field

CAR_FRAME_SIZE = 256
FRAME_GAP = 20
SUPPORTED_VERSION = 16

# Offsets inside the 256 byte CarFrame.
OFF_POSITION = 0        # 3 x float32, metres (x, y = up, z)
OFF_VELOCITY = 164      # 3 x float16, m/s
OFF_RPM = 170           # float16
OFF_STEER = 212         # float16, degrees
OFF_LAP_TIME = 220      # uint32 ms, time in the current lap
OFF_LAST_LAP = 224      # uint32 ms
OFF_BEST_LAP = 228      # uint32 ms
OFF_GEAR = 234          # uint8, 0 = reverse, 1 = neutral, 2 = first ...
OFF_TIRE_DIRT = 235     # 4 x uint8
OFF_GAS = 244           # uint8
OFF_BRAKE = 245         # uint8
OFF_LAP = 246           # uint8, 0 = first lap


class ReplayError(Exception):
    """The file could not be read. `report` holds a text report for debugging."""

    def __init__(self, message, report=""):
        super().__init__(message)
        self.report = report


@dataclass
class Car:
    index: int
    model: str
    driver: str
    nation: str
    team: str
    skin: str
    frame_count: int
    wings: int
    stride: int
    lap_times_ms: list = field(default_factory=list)  # completed laps, chronological
    positions: list = field(default_factory=list, repr=False)  # (x, y, z) per frame
    lap_index: list = None      # 0 based lap per frame, or None in limited mode
    lap_time_ms: list = None    # time in current lap per frame
    speed_kmh: list = None
    gas: bytes = None
    brake: bytes = None
    gear: bytes = None
    tire_dirt: list = None      # highest dirt value of the four tyres per frame
    has_positions: bool = True  # False if every position in the file is (0, 0, 0)

    @property
    def total_time_ms(self):
        return sum(self.lap_times_ms)

    @property
    def best_lap_ms(self):
        return min(self.lap_times_ms, default=None)


@dataclass
class Replay:
    path: str
    version: int
    strategy: str
    interval_ms: float
    weather: str
    track: str
    layout: str
    cars: list
    warnings: list = field(default_factory=list)

    @property
    def frame_count(self):
        return min((c.frame_count for c in self.cars), default=0)

    @property
    def has_details(self):
        return all(c.lap_index is not None for c in self.cars)

    def time_s(self, frame):
        return frame * self.interval_ms / 1000.0


# ---------------------------------------------------------------- low level
class _Reader:
    def __init__(self, buf, pos=0):
        self.buf, self.pos = buf, pos

    def need(self, n):
        if self.pos + n > len(self.buf):
            raise ReplayError("The file ended unexpectedly at offset %d." % self.pos)

    def u32(self):
        self.need(4)
        (v,) = struct.unpack_from("<I", self.buf, self.pos)
        self.pos += 4
        return v

    def f64(self):
        self.need(8)
        (v,) = struct.unpack_from("<d", self.buf, self.pos)
        self.pos += 8
        return v

    def skip(self, n):
        if n < 0:
            raise ReplayError("Negative skip at offset %d." % self.pos)
        self.need(n)
        self.pos += n

    def string(self, max_len=512):
        n = self.u32()
        if n > max_len:
            raise ReplayError("Implausible string length %d at offset %d." % (n, self.pos - 4))
        self.need(n)
        s = bytes(self.buf[self.pos:self.pos + n])
        self.pos += n
        return s.decode("utf-8", "replace")


def _column(buf, base, stride, count, offset, size):
    """Bytes of one field of `count` consecutive frames, packed together."""
    out = bytearray(count * size)
    for k in range(size):
        start = base + offset + k
        out[k::size] = buf[start:start + (count - 1) * stride + 1:stride]
    return out


def _decode_frames(buf, base, stride, count):
    """All per-frame values of one car (frames at base, base+stride, ...)."""
    pos = list(struct.iter_unpack("<3f", _column(buf, base, stride, count, OFF_POSITION, 12)))
    lap_time = struct.unpack("<%dI" % count, _column(buf, base, stride, count, OFF_LAP_TIME, 4))
    vel = struct.iter_unpack("<3e", _column(buf, base, stride, count, OFF_VELOCITY, 6))
    speed = []
    for vx, vy, vz in vel:
        s = 3.6 * math.sqrt(vx * vx + vy * vy + vz * vz)
        speed.append(s if s == s and s < 1500 else 0.0)
    dirt_raw = _column(buf, base, stride, count, OFF_TIRE_DIRT, 4)
    dirt = [max(dirt_raw[i:i + 4]) for i in range(0, len(dirt_raw), 4)]
    return dict(
        positions=pos,
        lap_time_ms=list(lap_time),
        speed_kmh=speed,
        lap_index=list(_column(buf, base, stride, count, OFF_LAP, 1)),
        gas=bytes(_column(buf, base, stride, count, OFF_GAS, 1)),
        brake=bytes(_column(buf, base, stride, count, OFF_BRAKE, 1)),
        gear=bytes(_column(buf, base, stride, count, OFF_GEAR, 1)),
        tire_dirt=dirt,
    )


_ZERO = (0.0, 0.0, 0.0)


def _plausible_positions(positions, limit=400):
    """True if the points look like a car driving around (not random bytes)."""
    pts = positions[:limit]
    if len(pts) < 2:
        return False
    steps = []
    for a, b in zip(pts, pts[1:]):
        # NaN, huge values and tiny non-zero values (misaligned bytes read as
        # floats) are never real coordinates.
        if any(v != v or abs(v) > 1e5 or (v != 0 and abs(v) < 1e-6) for v in b):
            return False
        steps.append(math.dist(a, b))
    steps.sort()
    median = steps[len(steps) // 2]
    big = sum(1 for s in steps if s > 30)
    return median < 10 and big <= max(1, len(steps) // 50)


def _check_car(positions, name):
    if not _plausible_positions(positions) or not _plausible_positions(positions[len(positions) // 2:]):
        raise ReplayError("Car '%s': positions do not look like driving data." % name)


# ----------------------------------------------------------- strategy 1: v16
def _parse_v16(buf):
    r = _Reader(buf)
    version = r.u32()
    interval = r.f64()
    if not (1.0 <= interval <= 1000.0):
        raise ReplayError("Recording interval %r ms is not plausible." % interval)
    weather, track, layout = r.string(), r.string(), r.string()
    num_cars, _rec_index, num_frames, num_objects = (r.u32() for _ in range(4))
    if not (1 <= num_cars <= 256):
        raise ReplayError("Car count %d is not plausible." % num_cars)
    if not (1 <= num_frames <= 5_000_000) or num_objects > 100_000:
        raise ReplayError("Frame/object count (%d/%d) is not plausible." % (num_frames, num_objects))
    r.skip((4 + 12 * num_objects) * num_frames)

    cars = []
    for index in range(num_cars):
        model, driver, nation, team, skin = (r.string() for _ in range(5))
        frame_count = r.u32()
        wings = r.u32()
        if not (2 <= frame_count <= 5_000_000) or wings > 64:
            raise ReplayError("Car %d: frame count %d / wings %d not plausible."
                              % (index, frame_count, wings))
        r.skip(FRAME_GAP)
        base = r.pos
        stride = CAR_FRAME_SIZE + FRAME_GAP + 4 * wings
        r.skip((frame_count - 1) * stride + CAR_FRAME_SIZE + 4 * wings)
        lap_count = r.u32()
        if lap_count > 1000:
            raise ReplayError("Car %d: lap list length %d not plausible." % (index, lap_count))
        laps = []
        for _ in range(lap_count):
            ms, _flag = r.u32(), r.u32()
            laps.append(ms)
        car = Car(index, model, driver, nation, team, skin, frame_count, wings, stride, laps)
        for key, value in _decode_frames(buf, base, stride, frame_count).items():
            setattr(car, key, value)
        car.has_positions = any(p != _ZERO for p in car.positions)
        if car.has_positions:
            _check_car(car.positions, driver)
        cars.append(car)
    if not any(c.has_positions for c in cars):
        raise ReplayError("No car in this file has any position data.")

    replay = Replay("", version, "exact layout", interval, weather, track, layout, cars)
    if version != SUPPORTED_VERSION:
        replay.warnings.append(
            "Format version %d has not been tested (only version %d has); the file "
            "matches the known layout, so it was opened anyway." % (version, SUPPORTED_VERSION))
    return replay


# --------------------------------------------------------- strategy 2: scan
_STRING_START = re.compile(rb"[\x03-\x50]\x00\x00\x00(?=[a-z0-9])")
_MODEL = re.compile(rb"^[A-Za-z0-9_.\-]{3,80}$")


def _scan_block(buf, p):
    """Try to read a car block header at p. Returns (fields, position_after) or None."""
    r = _Reader(buf, p)
    try:
        model = r.string(96)
        if not _MODEL.match(model.encode("utf-8", "replace")):
            return None
        driver = r.string(160)
        nation = r.string(16)
        team = r.string(160)
        skin = r.string(160)
        frames, wings = r.u32(), r.u32()
    except ReplayError:
        return None
    if not (50 <= frames <= 5_000_000 and wings <= 64):
        return None
    return (model, driver, nation, team, skin, frames, wings), r.pos


def _find_stride_and_offset(buf, base, frames, hint_strides):
    """Find (stride, position_offset) that yields continuous driving positions."""
    # Sample the middle of the recording: cars usually stand still on the grid
    # at the start, which would look like "no movement" for any layout.
    first = max(0, frames // 2 - 60)
    sample = min(frames - first, 120)
    for stride in hint_strides:
        start = base + first * stride
        if stride < 24 or start + (sample - 1) * stride + 12 > len(buf):
            continue
        for off in range(0, min(stride - 12, 96) + 1, 4):
            pts = list(struct.iter_unpack("<3f", _column(buf, start, stride, sample, off, 12)))
            if any(all(p[k] == 0 for p in pts) for k in range(3)):
                continue  # an axis that is always exactly 0 is not a coordinate
            if _plausible_positions(pts) and math.dist(pts[0], pts[-1]) > 0.5:
                return stride, off
    return None


def _parse_scan(buf):
    starts = [m.start() for m in _STRING_START.finditer(buf)]
    blocks = []
    last_end = 0
    for p in starts:
        if p < last_end:
            continue
        found = _scan_block(buf, p)
        if not found:
            continue
        fields, after = found
        frames, wings = fields[5], fields[6]
        blocks.append((p, after, fields))
        # Cars are laid out back to back; skip the data region of this car.
        last_end = after + (frames - 1) * (CAR_FRAME_SIZE + FRAME_GAP) // 2
    if not blocks:
        raise ReplayError("No car blocks could be found in the file.")

    cars = []
    known_stride = None
    for i, (p, after, fields) in enumerate(blocks):
        model, driver, nation, team, skin, frames, wings = fields
        spacing = []
        if i + 1 < len(blocks):
            approx = (blocks[i + 1][0] - after) / frames
            spacing = [int(approx) - d for d in range(0, 4)]
        hints = []
        for s in [CAR_FRAME_SIZE + FRAME_GAP + 4 * wings, known_stride] + spacing:
            if s and s not in hints:
                hints.append(s)
        # The body position is the first field of a frame, while the wheel
        # positions further in also look like valid coordinates. Try every
        # possible start and keep the earliest valid triple in the file.
        best = None
        for skip in (FRAME_GAP, 16, 24, 0, 4, 8, 12, 28, 32):
            found = _find_stride_and_offset(buf, after + skip, frames, hints)
            if found and (best is None or skip + found[1] < best[0]):
                best = (skip + found[1], skip, found)
        if not best:
            continue
        _, skip, (stride, off) = best
        base = after + skip
        known_stride = stride
        count = min(frames, (len(buf) - base) // stride)
        raw = list(struct.iter_unpack("<3f", _column(buf, base, stride, count, off, 12)))
        car = Car(len(cars), model, driver, nation, team, skin, count, wings, stride,
                  positions=raw)
        try:
            _check_car(car.positions, driver)
        except ReplayError:
            continue
        cars.append(car)
    if not cars:
        raise ReplayError("Car blocks were found, but no frame layout gave valid positions.")

    version = struct.unpack_from("<I", buf, 0)[0]
    replay = Replay("", version, "scan (limited)", 30.0, "?", "?", "", cars)
    replay.warnings.append(
        "This file does not match the known layout (format version %d), so it was read "
        "in limited mode: positions only. Lap numbers and speeds are not available, and "
        "times assume 30 ms per frame." % version)
    return replay


STRATEGIES = (("exact layout", _parse_v16), ("scan (limited)", _parse_scan))


# ------------------------------------------------------------------ report
def diagnose(buf, attempts=()):
    """A plain-text report that helps to work out an unknown file layout."""
    lines = ["AC replay diagnostic report", "size: %d bytes" % len(buf)]
    lines.append("first 96 bytes: " + bytes(buf[:96]).hex(" "))
    if len(buf) >= 12:
        lines.append("uint32 @0 (version): %d" % struct.unpack_from("<I", buf, 0)[0])
        lines.append("float64 @4: %r" % struct.unpack_from("<d", buf, 4)[0])
        try:
            r = _Reader(buf, 12)
            lines.append("strings: %r %r %r" % (r.string(), r.string(), r.string()))
            lines.append("uint32 x4 after strings: %s" % ([r.u32() for _ in range(4)],))
        except ReplayError as exc:
            lines.append("header strings: %s" % exc)
    lines.append("last 64 bytes: " + bytes(buf[-64:]).hex(" "))
    for name, message in attempts:
        lines.append("strategy '%s' failed: %s" % (name, message))
    cands = []
    for m in _STRING_START.finditer(buf):
        found = _scan_block(buf, m.start())
        if found:
            cands.append((m.start(), found[0], found[1]))
    lines.append("car-like blocks found by scanning: %d" % len(cands))
    for p, f, after in cands[:12]:
        lines.append("  @%d model=%r driver=%r nation=%r team=%r skin=%r frames=%d wings=%d"
                     % ((p,) + tuple(f)))
        lines.append("     first 96 bytes after the car header: "
                     + bytes(buf[after:after + 96]).hex(" "))
    return "\n".join(lines)


# --------------------------------------------------------------------- API
def load_bytes(buf, path=""):
    if len(buf) < 64:
        raise ReplayError("The file is too small to be a replay.",
                          diagnose(buf))
    attempts = []
    for name, strategy in STRATEGIES:
        try:
            replay = strategy(buf)
        except (ReplayError, struct.error, ValueError, MemoryError, OverflowError) as exc:
            attempts.append((name, str(exc)))
            continue
        replay.path = path
        replay.strategy = name
        if attempts:
            replay.warnings.append(
                "The standard reader failed (%s); fell back to '%s'." % (attempts[0][1], name))
        empty = [c for c in replay.cars if not c.has_positions]
        if empty:
            replay.warnings.append(
                "%d of %d cars have no position data in this file (every frame is empty), "
                "so they are listed but left out of the standings and the chart."
                % (len(empty), len(replay.cars)))
        counts = {c.frame_count for c in replay.cars}
        if len(counts) > 1:
            replay.warnings.append(
                "Cars have different frame counts (%d to %d); only the common part is used."
                % (min(counts), max(counts)))
        return replay
    version = struct.unpack_from("<I", buf, 0)[0]
    raise ReplayError(
        "This replay could not be read (format version %d). Reader messages: %s"
        % (version, "; ".join("%s: %s" % a for a in attempts)),
        diagnose(buf, attempts))


def load(path):
    with open(path, "rb") as fh:
        buf = fh.read()
    return load_bytes(buf, path)
