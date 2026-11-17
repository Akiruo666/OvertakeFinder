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
- **Race chart** (bottom left): choose **Positions** (race position of every car over time; crossing lines are overtakes, drawn as markers: circle = clean, diamond = has a note; filled green/orange = ticked, red ring = selected), **Gap to leader**, or **Gap to selected driver** (the driver chosen in the overtakes list; + means that car is ahead). Mouse wheel zooms around the pointer, the scroll bar moves through the zoomed replay, *Zoom 120 s around cursor* and *Reset zoom* are above the chart. Click to jump to a moment, click a marker to select that overtake, point at a line to see who it is. The green strip at the bottom shows the **clips** of the ticked overtakes (5 s before and 5 s after each, overlapping ones merged); the total length is shown above the list.
- **Track map** (bottom right): the track seen from above with every car at the shown moment, so jumping in time (slider, chart, list) always shows the right situation. The selected overtake's two cars are highlighted with a short trail, the chosen driver as well. Wheel zooms, drag moves, double-click resets. The track shape comes from the track model (real tarmac), else the AI racing line, else the paths the cars drove.

## Overtakes

The **Overtakes** list shows every pass: replay time, lap, overtaker, passed driver and notes. Pick a driver to see only his overtakes, and use the two checkboxes (both on by default) to choose **Overtakes by this driver** and/or **Driver overtaken**. The **Use** column holds a tickbox per overtake: click it (or select rows and press the space bar) to tick, or use *Tick all shown* / *Untick all shown*. Ticks are kept when you change the driver filter; they are what will go into the new replay. Click a row elsewhere to jump to that moment in the standings. All tables have scroll bars. Notes mark: lapping (a lapped car is involved, needs track data), overtaker or passed car off track (all four wheels off the tarmac; one or two wheels over the white line is legal and not marked), and contact (a car lost speed suddenly without braking for it, e.g. a wall or collision; thresholds are not tuned on a real crash yet). With the track's 3D model (.kn5, found in the game folder) the program checks where each wheel is against the road, kerb and pit meshes: off track means all four wheels off those surfaces. Without a model it uses the tyre dirt values. The AI line's edge distances are not used for off-track checks: they are narrower than the real tarmac.

## Units

The **Units** switch at the top right toggles between **Metric** (m, km, km/h) and **Imperial** (ft, mi, mph). It changes the gap columns, both distance columns (short and long), the speed column, the "Driven" column in the drivers table and the chart axis. The choice is remembered in `.ac_overtake_finder.json` in your user folder.

## How replays are opened

The program checks each file and tries, in order:

1. **Exact layout**: the known layout (format version 16). The frame size is worked out per car, because it depends on the car (number of wing/aero elements), and tracks with extra objects are handled. A different version number with the same layout also opens, with a note.
2. **Scan (limited)**: if the exact layout does not fit, the file is searched for car blocks and the frame size and position are detected from the data. This gives positions and gaps only: no lap numbers or speeds, and times assume 30 ms per frame.

The line under the file name shows which method was used. If neither works, the program offers to save a small **diagnostic report** (text file). Send that file (or the replay itself) to get support for the format added.

## Cars without position data

Some replays (seen in an online Practice session) list many drivers but only record movement for some of them; every frame of the other cars is empty. Those cars are shown at the bottom of the drivers table as "no position data", and are left out of the standings and the chart. A note under the file name says how many.

## Game folder and track data

At the first start the program looks for Assetto Corsa itself (Steam locations). If it cannot find it, it asks for the folder (the one that contains `content`). Change it later with **Game folder...** (top left).

For each replay the program searches `content/tracks/<track>` (all subfolders, e.g. `ai/` or `<layout>/ai/`) for `fast_lane.ai`, the AI racing line, and checks that it matches the recorded driving. With it, distances, gaps and lap numbers are exact (distance from the start line, negative on the grid). The line under the file name says which file was used.

Tracks without an AI line, a damaged line, a track that is not installed, or no game folder: the program falls back to the distance driven (estimated gaps) and says why.

## Notes

- The replay format is reverse engineered; only format version 16 has been verified against real files.
- `python selftest.py` tries to read every `.acreplay` in the folder without opening a window.
