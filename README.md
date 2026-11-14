# AC Overtake Finder

Step 1: race overview from an Assetto Corsa `.acreplay` file.

## Get the Windows exe (no Python needed)

The exe is built automatically by GitHub from the files in this repository.

1. Put all files from this folder in the repository root (including `.github/workflows/build-exe.yml`).
2. On GitHub open the **Actions** tab, choose **Build Windows exe**, and click **Run workflow** (it also runs on every push).
3. When the run turns green, open it and download **ACOvertakeFinder** from *Artifacts*. Unzip it and double-click `ACOvertakeFinder.exe`.

Windows SmartScreen or antivirus may warn about an unsigned program that was just downloaded. Choose "More info" > "Run anyway".

## Run from source instead (needs Python 3.9+)

`python app.py` (optionally with the path of a replay as argument). No third-party packages are needed.

## What you see

- **Drivers and lap times**: classification (laps completed, then total time), best lap, every lap time, distance driven.
- **Standings at selected moment**: slider, buttons or the left/right arrow keys move through the replay; shows replay time, lap, speed, and the gap to the car ahead and to the leader in metres.
- **Gap chart**: gap to the leader for every car over the whole replay; click it to jump to that moment.

## How replays are opened

The program checks each file and tries, in order:

1. **Exact layout**: the known layout (format version 16). The frame size is worked out per car, because it depends on the car (number of wing/aero elements), and tracks with extra objects are handled. A different version number with the same layout also opens, with a note.
2. **Scan (limited)**: if the exact layout does not fit, the file is searched for car blocks and the frame size and position are detected from the data. This gives positions and gaps only: no lap numbers or speeds, and times assume 30 ms per frame.

The line under the file name shows which method was used. If neither works, the program offers to save a small **diagnostic report** (text file). Send that file (or the replay itself) to get support for the format added.

## Cars without position data

Some replays (seen in an online Practice session) list many drivers but only record movement for some of them; every frame of the other cars is empty. Those cars are shown at the bottom of the drivers table as "no position data", and are left out of the standings and the chart. A note under the file name says how many.

## Notes

- Gaps use the distance each car has driven, corrected for the starting grid. They become exact once the track centreline (`fast_lane.ai`) is used.
- The replay format is reverse engineered; only format version 16 has been verified against real files.
- `python selftest.py` tries to read every `.acreplay` in the folder without opening a window.
