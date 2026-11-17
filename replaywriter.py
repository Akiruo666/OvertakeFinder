"""Writing a new .acreplay that holds only chosen stretches of an existing one.

The file is cut and pasted at the byte level, so everything the program does not
understand stays exactly as the game wrote it:

  header                 copied, the frame counts are changed
  per-frame table        (sun angle / track objects) the rows of the chosen frames
  every car              its header copied, the frame records of the chosen frames
                         (each with the bytes that separate frames), lap list copied
  extra data at the end  Custom Shaders Patch and the race settings: see TRAILER_*

The chosen stretches ("clips", first and last frame, both included) are joined in
time order. Cars jump from the end of one clip to the start of the next, as in
the replay merger tools.
"""
import os
import struct

import acreplay

FRAME_RECORD = acreplay.CAR_FRAME_SIZE            # 256 bytes of data per frame
GAP = acreplay.FRAME_GAP                          # bytes between two frame records

TRAILER_NONE = "none"          # nothing after the cars (what the known replay mergers do)
TRAILER_SETTINGS = "settings"  # race settings, without the per-frame data of the shaders patch
TRAILER_FULL = "full"          # everything, unchanged: its per-frame data no longer matches
TRAILERS = ((TRAILER_NONE, "None (safest)"),
            (TRAILER_SETTINGS, "Race settings only"),
            (TRAILER_FULL, "Everything, unchanged"))

_PER_FRAME_MARK = b"EXT_PERFRAME_v1"


class WriteError(Exception):
    pass


def _walk(buf):
    """Offsets needed for cutting: returns a dict, raises ReplayError if the
    file does not follow the exact layout."""
    r = acreplay._Reader(buf)
    r.u32()
    interval = r.f64()
    for _ in range(3):
        r.string()
    count_pos = r.pos                      # num_cars, recording_index, num_frames, num_objects
    num_cars, _rec, num_frames, num_objects = (r.u32() for _ in range(4))
    row = 4 + 12 * num_objects
    table = r.pos
    r.skip(row * num_frames)
    cars = []
    for _ in range(num_cars):
        start = r.pos
        for _ in range(5):
            r.string()
        count_at = r.pos
        frame_count = r.u32()
        wings = r.u32()
        header_end = r.pos
        r.skip(GAP)
        base = r.pos
        stride = FRAME_RECORD + GAP + 4 * wings
        r.skip((frame_count - 1) * stride + FRAME_RECORD + 4 * wings)
        frames_end = r.pos
        lap_count = r.u32()
        r.skip(8 * lap_count)
        cars.append(dict(start=start, count_at=count_at, header_end=header_end, base=base,
                         stride=stride, wings=wings, frame_count=frame_count,
                         frames_end=frames_end, laps_end=r.pos))
    return dict(interval=interval, count_pos=count_pos, num_frames=num_frames, row=row,
                table=table, cars=cars, trailer=r.pos)


def frames_of(ranges, limit):
    """The chosen frame numbers in order, from (first, last) ranges."""
    out = []
    for first, last in sorted(ranges):
        out.extend(range(max(0, first), min(limit - 1, last) + 1))
    return out


def build(buf, ranges, trailer=TRAILER_NONE):
    """Bytes of the new replay: `buf` is the original file, `ranges` a list of
    (first_frame, last_frame)."""
    try:
        info = _walk(buf)
    except acreplay.ReplayError as exc:
        raise WriteError("This replay was not read with the exact layout, so it cannot be cut: %s" % exc)
    limit = min([info["num_frames"]] + [c["frame_count"] for c in info["cars"]])
    frames = frames_of(ranges, limit)
    if not frames:
        raise WriteError("There is nothing to write: no frames selected.")
    total = len(frames)
    out = bytearray()

    head = bytearray(buf[:info["table"]])
    pos = info["count_pos"]
    struct.pack_into("<I", head, pos + 4, total)       # recording index = last frame written
    struct.pack_into("<I", head, pos + 8, total)       # number of frames
    out += head

    row, table = info["row"], info["table"]
    for f in frames:
        out += buf[table + f * row:table + (f + 1) * row]

    for c in info["cars"]:
        out += buf[c["start"]:c["count_at"]]
        out += struct.pack("<I", total)
        out += buf[c["count_at"] + 4:c["header_end"]]   # wings
        base, stride = c["base"], c["stride"]
        record = FRAME_RECORD + 4 * c["wings"]
        out += buf[base - GAP:base]                      # separator before the first frame
        for n, f in enumerate(frames):
            if n:
                out += buf[base + f * stride - GAP:base + f * stride]
            out += buf[base + f * stride:base + f * stride + record]
        out += buf[c["frames_end"]:c["laps_end"]]        # lap list

    tail = buf[info["trailer"]:]
    if trailer == TRAILER_FULL:
        out += tail
    elif trailer == TRAILER_SETTINGS:
        cut = tail.find(_PER_FRAME_MARK)
        if cut >= 4:
            out += tail[:cut - 4]                        # the length prefix belongs to the name
    return bytes(out)


def write_file(src_path, dst_path, ranges, trailer=TRAILER_NONE):
    """Cut `src_path` into `dst_path`. Returns (frames_written, size_in_bytes).
    The result is read back with the normal reader to check it."""
    if os.path.abspath(src_path) == os.path.abspath(dst_path):
        raise WriteError("Choose another file name: the original replay is never overwritten.")
    with open(src_path, "rb") as fh:
        buf = fh.read()
    data = build(buf, ranges, trailer)
    check = acreplay.load_bytes(data, dst_path)
    expected = len(frames_of(ranges, min(c.frame_count for c in acreplay.load_bytes(buf).cars)))
    if check.frame_count != expected:
        raise WriteError("Internal check failed: %d frames written, %d expected." % (check.frame_count, expected))
    tmp = dst_path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, dst_path)
    return expected, len(data)
