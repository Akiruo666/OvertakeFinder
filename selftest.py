"""Headless check: parse every .acreplay next to this file and print a summary.

Exits with an error if a replay cannot be read or looks wrong. Does nothing
(and succeeds) if there is no replay in the folder.
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
    replay = acreplay.load(path)
    model = analysis.RaceModel(replay)
    order = [s.driver for s in model.standings(replay.frame_count - 1)]
    print("%s: %s, %d cars, %d frames, final order: %s"
          % (os.path.basename(path), replay.track, len(replay.cars),
             replay.frame_count, ", ".join(order)))
    assert len(replay.cars) >= 1 and replay.frame_count > 1
print("Self-test passed.")
