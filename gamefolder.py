"""Finding the Assetto Corsa installation and the track data inside it."""
import os
import re

STEAM_APP = os.path.join("steamapps", "common", "assettocorsa")
AI_NAME = "fast_lane.ai"
MAX_SEARCH_DEPTH = 5       # folders below the track folder that are searched


def is_game_folder(path):
    return bool(path) and os.path.isdir(os.path.join(path, "content", "tracks"))


def normalize_game_folder(path):
    """Accept the game folder or its 'content' / 'tracks' subfolder.
    Returns the game folder, or None if `path` is not an Assetto Corsa folder."""
    if not path:
        return None
    path = os.path.normpath(path)
    for _ in range(3):
        if is_game_folder(path):
            return path
        if os.path.basename(path).lower() in ("content", "tracks"):
            path = os.path.dirname(path)
        else:
            break
    return None


def _steam_roots():
    roots = []
    try:
        import winreg  # Windows only
        for hive, key, value in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(hive, key) as handle:
                    roots.append(winreg.QueryValueEx(handle, value)[0])
            except OSError:
                pass
    except ImportError:
        pass
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env)
        if base:
            roots.append(os.path.join(base, "Steam"))
    for letter in "CDEFGH":
        for sub in ("Steam", "SteamLibrary", os.path.join("Program Files (x86)", "Steam"),
                    os.path.join("Games", "Steam")):
            roots.append("%s:\\%s" % (letter, sub))
    return roots


def _library_folders(steam_root):
    """Extra Steam library locations listed in libraryfolders.vdf."""
    try:
        with open(os.path.join(steam_root, "steamapps", "libraryfolders.vdf"),
                  encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return []
    return [m.replace("\\\\", "\\") for m in re.findall(r'"path"\s+"([^"]+)"', text)]


def detect_game_folder(extra_roots=()):
    """Look for Assetto Corsa in the usual Steam locations. Returns a path or None."""
    seen = set()
    roots = list(extra_roots) + _steam_roots()
    for root in list(roots):
        roots.extend(_library_folders(root))
    for root in roots:
        candidate = os.path.normpath(os.path.join(root, STEAM_APP))
        if candidate not in seen:
            seen.add(candidate)
            if is_game_folder(candidate):
                return candidate
    return None


def find_ai_lines(game_folder, track, layout=""):
    """All fast_lane.ai files of a track, best candidate first.

    The file is not always in the same place: some tracks keep it in
    <track>/ai/, others in <track>/<layout>/ai/. So the whole track folder is
    searched. A folder named like the replay's layout comes first, then the
    shallowest path. Returns (paths, message)."""
    track_dir = os.path.join(game_folder, "content", "tracks", track)
    if not track or not os.path.isdir(track_dir):
        return [], "Track '%s' is not installed in your game folder." % track
    found = []
    for dirpath, dirs, files in os.walk(track_dir):
        depth = len(os.path.relpath(dirpath, track_dir).split(os.sep)) if dirpath != track_dir else 0
        if depth >= MAX_SEARCH_DEPTH:
            dirs[:] = []
        for name in files:
            if name.lower() == AI_NAME:
                found.append(os.path.join(dirpath, name))
    if not found:
        return [], "No %s found for track '%s' (this track may have no AI support)." % (AI_NAME, track)
    want = (layout or "").lower()

    def rank(path):
        parts = [p.lower() for p in os.path.relpath(path, track_dir).split(os.sep)]
        has_layout = bool(want) and want in parts[:-1]
        return (0 if has_layout else 1, len(parts), path.lower())

    found.sort(key=rank)
    return found, ""
