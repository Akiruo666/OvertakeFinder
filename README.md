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
- **Standings at selected moment**: slider, buttons or the left/right arrow keys move through the replay; shows position, gap to the car ahead and gap to the leader in metres.
- **Gap chart**: gap to the leader for every car over the whole replay; click it to jump to that moment.

## Notes

- Gaps use the distance each car has driven, corrected for the starting grid. They become exact once the track centreline (`fast_lane.ai`) is used.
- Moments are shown as replay frame numbers. Converting a frame to the time AC displays still has to be worked out.
- The replay format is reverse engineered; only format version 16 has been verified.
- `python selftest.py` parses any `.acreplay` in the folder without opening a window.
