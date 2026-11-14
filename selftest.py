"""Headless check: try to read every .acreplay next to this file.

A replay that cannot be read is reported but does not fail the check (the exe
build must not be blocked by a file that needs new format support). Only a
crash inside the reader (a bug) fails it.
"""
import glob
import os
import sys

import acreplay
import analysis

here = os.path.dirname(os.path.abspath(__file__))
files = sorted(glob.glob(os.path.join(here, "*.acreplay")))
if not files:
    print("No .acreplay files found, nothing to test.")
    sys.exit(0)

for path in files:
    name = os.path.basename(path)
    try:
        replay = acreplay.load(path)
    except acreplay.ReplayError as exc:
        print("%s: NOT READABLE - %s" % (name, exc))
        continue
    model = analysis.RaceModel(replay)
    order = [s.driver for s in model.standings(replay.frame_count - 1)]
    print("%s: %s, %d cars, %s long, read with '%s', final order: %s"
          % (name, replay.track, len(replay.cars),
             analysis.fmt_clock(replay.time_s(replay.frame_count - 1)),
             replay.strategy, ", ".join(order)))
    for w in replay.warnings:
        print("  note:", w)
print("Self-test finished.")
