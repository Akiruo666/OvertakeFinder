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

The window has three tabs and a time bar (slider, `<<` `<` `>` `>>`, left/right arrow keys) that stays visible on all of them, so you can jump in time wherever you are. Window size, the open tab and the panel states are remembered.

**Race tab**
- **Track data** (click to expand/collapse; collapsed by default): one line telling whether the racing line and road surface were found, with a (!) when something is missing. Expanded it shows the details and the *Game folder...* button.
- **Drivers and lap times**: classification (laps completed, then total time), best lap, every lap time, distance driven.
- **Standings at the selected moment**: lap, speed, gap to the car ahead and to the leader.

**Overtakes tab**
- Driver filter with the two checkboxes (overtakes by this driver / driver overtaken), *Tick all shown* / *Untick all shown*, default clip length before/after.
- **List** (left, large): Use, time, lap, overtaker, passed, Before/After (seconds, `*` = set by hand) and notes. Select one or more rows and the *Before / After* sliders appear under the list.
- **Track map** (right, large): the track from above with every car at the shown moment. Selecting an overtake zooms the map onto the two cars; *Zoom on selecting an overtake* chooses Off, Half (default, about 450 m of track across the map) or Full (about 120 m); changing the choice applies it to the selected overtake at once. Cars that are not selected carry their initials (first letter of every word of the name; switch off with *Initials on other cars*; point at any car to see its full name), the selected cars always show their full names. The selected pair (and the chosen driver) get a trail behind them and a dashed **look-ahead path** with dots at +1 s, +2 s, +3 s (the overtaker's labelled), plus a dotted line between the two cars with the gap. *Trail behind* and *Look ahead* (0-20 s) are set above the map. Wheel zooms, drag moves, double-click resets (and refits when the window is resized). When you are zoomed in and the selected cars leave the picture (you change the time), the map jumps to them again.
- **Race chart** (bottom, *Hide chart* / *Show chart*): Positions, Gap to leader or Gap to selected driver; wheel zooms in time, click jumps, click a marker selects the overtake. With many cars the Positions rows get a minimum height and a vertical scroll bar appears (Shift + wheel also scrolls); selecting an overtake scrolls its cars into view. Green strip = clips of the ticked overtakes, blue strip = the (effective) clip of the selected overtake.
- The list / map / chart proportions start like a normal working layout (small list, large map) and your own sash positions are remembered.

**Save tab**
- Overview of the clips that will be in the new replay (merged where they overlap): from, to, length and the overtakes inside, with the total length.
- *Extra data from the original*, *Camera follows* and *Save new replay...* (the mp4 export is planned).

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

## Saving a new replay

Tick the overtakes you want (column **Use**). Each tick becomes a clip: by default **5 seconds before and 5 seconds after** the overtake. The default is set with the two number boxes *Clip around each overtake - default: before / after* (0-60 s, remembered in `.ac_overtake_finder.json`).

Per overtake you can change this: select one or more rows in the list and use the **Before** and **After** sliders (or the `-` / `+` buttons, one second per step). The columns *Before (s)* and *After (s)* show the values; a `*` means it was set by hand. *Reset to default* puts the selected rows back to the default. Changing the default changes every row that was not set by hand. Overlapping clips are joined. The chart shows the clips as a green strip and the list shows the total length.

**Save new replay...** writes a new `.acreplay` with only the clips, one after the other. The original is never changed or overwritten. Copy the result to `Documents\Assetto Corsa\replay` to play it in the game. The cars jump from the end of one clip to the start of the next.

*Extra data*: replays recorded with the Custom Shaders Patch end with race settings and shader data that belong to the original frames. **None** (default) leaves all of that out, as the known replay merger tools do. **Race settings only** keeps the settings but not the per-frame shader data. **Everything, unchanged** keeps all of it, although the per-frame parts no longer match the cut replay. If the game does not accept a new replay, try another option.

## Camera follows the overtaking car (needs the in-game app)

A replay file does not store which car the camera follows; the game decides that while playing (it starts on the car of the person who recorded the replay). So *Save new replay...* can also write a **camera plan**: with *Camera follows* set to *Car that overtakes* (or *Car that is passed*), a small JSON file `<name>.focus.json` is written next to the new replay (keep them together). At the start of each clip the plan switches to the car of that overtake; when a clip holds several overtakes it switches halfway between them.

The in-game part is the CSP Lua app in `lua_app\OvertakeFocus`:

1. Copy the folder `OvertakeFocus` to `<Assetto Corsa folder>\apps\lua\OvertakeFocus`.
2. Start the game, open the replay saved by this program, and switch on **Overtake Focus** in the apps list. Its window shows the plan, the replay time, the followed car, and which replay fields this CSP version offers.
3. When a replay plays, the app finds its plan in two ways:
   - **By name**: the game writes the replay's file name in `Documents\Assetto Corsa\cfg\race.ini` (`[REPLAY] FILENAME=...`). The app looks in the replay folder for `<name>.focus.json`, also under the name Content Manager may give it (`<name>.focus.json.acreplay`). When another replay is loaded, it looks again.
   - **By car positions**, when no plan has the replay's name (e.g. you renamed the replay): every plan holds where each car is at every second of its replay; the app compares all plans in the replay folder with where the cars are and uses the one that matches (80 % of the cars within 8 m). This also tells apart several exports from the same race.
4. **Open plan...** opens a file dialog to pick a plan by hand (it stays until the next replay starts). The window warns when the cars are not where the plan has them. **Search again** repeats the search. *Details* shows each plan's match.
5. While the replay plays the app switches the followed car by itself. *Follow the plan* turns it off.

Plans saved before this version have no positions: open them with *Open plan...*, or save the replay again.

Content Manager renames every file in the replay folder that does not end in `.acreplay`, so the plan may show up in its replay list as `<name>.focus.json` with a `.acreplay` extension. It is not a replay; leave it there, the app finds it.

This app was written without being able to run the game (tested only for the plan file). If the window shows "NO" or "not found" for a field, or the followed car does not change, send a screenshot of the app window.


Note: *Save new replay...* proposes the original name plus `_export` (e.g. `race_export.acreplay`), and never overwrites the replay it is reading: choosing the original name adds `_export` automatically.
