"""AC Overtake Finder - race overview screen.

Run:  python app.py [path\to\replay.acreplay]

Needs only Python 3.9+ with Tkinter (included in the standard Windows installer).
"""
import json
import os
import re
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import acreplay
import analysis
import battles
import battletab
import chartview
import clips
import focusplan
import gamefolder
import mapview
import overtakes
import replaywriter

COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd",
          "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#7f7f7f"]
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".ac_overtake_finder.json")


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(**changes):
    """Merge `changes` into the saved settings (units, game folder, ...)."""
    data = load_config()
    data.update(changes)
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError:
        pass  # not being able to remember a choice is not worth an error


def load_units():
    """The unit system chosen last time (metric if nothing was saved)."""
    value = load_config().get("units")
    return value if value in analysis.UNIT_NAMES else analysis.METRIC


def save_units(system):
    save_config(units=system)


def nice_step(top):
    """A round grid step that gives at most about six lines up to `top`."""
    for step in (10, 20, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000, 20000, 50000):
        if top / step <= 6:
            return step
    return 100000


def scrolled_tree(parent, horizontal=False, **options):
    """A Treeview with a vertical (and optionally horizontal) scroll bar."""
    box = ttk.Frame(parent)
    tree = ttk.Treeview(box, **options)
    vbar = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vbar.set)
    vbar.pack(side="right", fill="y")
    if horizontal:
        hbar = ttk.Scrollbar(box, orient="horizontal", command=tree.xview)
        tree.configure(xscrollcommand=hbar.set)
        hbar.pack(side="bottom", fill="x")
    tree.pack(side="left", fill="both", expand=True)
    box.pack(fill="both", expand=True)
    return tree


def default_replay_dir():
    docs = os.path.join(os.path.expanduser("~"), "Documents",
                        "Assetto Corsa", "replay")
    return docs if os.path.isdir(docs) else os.path.expanduser("~")


class App(tk.Tk, battletab.BattlesTab):
    TAB_NAMES = ("race", "overtakes", "battles", "save")
    LEGACY_TABS = {0: "race", 1: "overtakes", 2: "save"}     # saved tab number before Battles

    def __init__(self):
        super().__init__()
        self.title("AC Overtake Finder - Race overview")
        cfg0 = load_config()
        geo = cfg0.get("geometry")
        self.geometry(geo if isinstance(geo, str) and re.fullmatch(r"\d+x\d+([+-]-?\d+){0,2}", geo) else "1280x860")
        self.minsize(820, 640)
        self.replay = None
        self.model = None
        self.frame = 0
        self.units = load_units()
        cfg = load_config()
        self.game_folder = gamefolder.normalize_game_folder(cfg.get("game_folder"))
        self.line_note = ""
        self.road_note = ""
        self.overtakes = []
        self.ticked = set()
        self.shown_overtakes = []
        self.selected_overtake = None
        self.default_before = clips.clamp_seconds(cfg.get("clip_before_s"), clips.CLIP_BEFORE_S)
        self.default_after = clips.clamp_seconds(cfg.get("clip_after_s"), clips.CLIP_AFTER_S)
        self.clip_override = {}          # overtake index -> (before, after) set by hand
        self._syncing = False
        self.colors_list = COLORS
        self._build()

    # ------------------------------------------------------------------ UI
    def _build(self):
        cfg = load_config()
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="Open replay...", command=self.open_dialog).pack(side="left")
        ttk.Button(top, text="Game folder...", command=self.choose_game_folder).pack(side="left", padx=(6, 0))

        # Unit system toggle, top right
        units_box = ttk.Frame(top)
        units_box.pack(side="right")
        self.units_var = tk.StringVar(value=self.units)
        ttk.Label(units_box, text="Units:").pack(side="left", padx=(0, 6))
        ttk.Radiobutton(units_box, text="Metric (m, km, km/h)", variable=self.units_var,
                        value=analysis.METRIC, command=self.on_units_changed).pack(side="left")
        ttk.Radiobutton(units_box, text="Imperial (ft, mi, mph)", variable=self.units_var,
                        value=analysis.IMPERIAL, command=self.on_units_changed).pack(side="left", padx=(8, 0))

        self.path_var = tk.StringVar(value="No replay loaded")
        ttk.Label(top, textvariable=self.path_var).pack(side="left", padx=10)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        race_tab, over_tab, save_tab = ttk.Frame(self.notebook), ttk.Frame(self.notebook), ttk.Frame(self.notebook)
        battle_tab = ttk.Frame(self.notebook)
        self.notebook.add(race_tab, text="  Race  ")
        self.notebook.add(over_tab, text="  Overtakes  ")
        self.notebook.add(battle_tab, text="  Battles  ")
        self.notebook.add(save_tab, text="  Save  ")
        self._build_race(race_tab, cfg)
        self._build_overtakes(over_tab, cfg)
        self._build_battles(battle_tab, cfg)
        self._build_save(save_tab)
        self.notebook.bind("<<NotebookTabChanged>>", lambda e: self._on_tab_changed())

        # Time control, visible on every tab
        bar = ttk.Frame(self, padding=(8, 0))
        bar.pack(fill="x")
        ttk.Button(bar, text="<<", width=4, command=lambda: self.step(-100)).pack(side="left")
        ttk.Button(bar, text="<", width=4, command=lambda: self.step(-10)).pack(side="left")
        self.scale = ttk.Scale(bar, from_=0, to=1, orient="horizontal", command=self._on_scale)
        self.scale.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(bar, text=">", width=4, command=lambda: self.step(10)).pack(side="left")
        ttk.Button(bar, text=">>", width=4, command=lambda: self.step(100)).pack(side="left")
        self.frame_var = tk.StringVar(value="Frame - / -")
        ttk.Label(self, textvariable=self.frame_var, padding=(8, 0, 8, 2)).pack(fill="x")

        ttk.Label(self, padding=(8, 0, 8, 6), foreground="#666666",
                  text="Distance is measured from the start line (negative on the grid) along the track's "
                       "racing line, or from the distance driven when no track data is available. "
                       "Times are replay time from the start of the recording.").pack(fill="x")

        self.bind("<Left>", lambda e: self.step(-10))
        self.bind("<Right>", lambda e: self.step(10))
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._apply_unit_headings()
        tab = cfg.get("tab_name")
        if tab not in self.TAB_NAMES:
            tab = self.LEGACY_TABS.get(cfg.get("tab"))
        if tab in self.TAB_NAMES:
            self.notebook.select(self.TAB_NAMES.index(tab))

    # ---- tab 1: race overview
    def _build_race(self, parent, cfg):
        head = ttk.Frame(parent)
        head.pack(fill="x", pady=(2, 2))
        self.track_open = bool(cfg.get("track_panel_open", False))
        self.track_btn = ttk.Button(head, text="", width=14, command=self.toggle_track_panel)
        self.track_btn.pack(side="left")
        self.summary_var = tk.StringVar(value="Open a replay to start.")
        ttk.Label(head, textvariable=self.summary_var).pack(side="left", padx=8)
        self.track_body = ttk.Frame(parent, padding=(4, 0, 4, 4))
        self.info_var = tk.StringVar(value="")
        ttk.Label(self.track_body, textvariable=self.info_var, justify="left").pack(anchor="w")
        ttk.Button(self.track_body, text="Game folder...", command=self.choose_game_folder).pack(anchor="w", pady=(4, 0))
        self._apply_track_panel(head)

        paned = ttk.Panedwindow(parent, orient="vertical")
        paned.pack(fill="both", expand=True)

        drivers = ttk.Labelframe(paned, text="Drivers and lap times", padding=4)
        cols = ("pos", "driver", "init", "nation", "team", "laps", "best", "times", "km")
        self.drivers_tree = scrolled_tree(drivers, horizontal=True, columns=cols, show="headings", height=7)
        heads = (("pos", "Pos", 40), ("driver", "Driver", 190), ("init", "Map label", 70), ("nation", "Nat", 45),
                 ("team", "Team / livery", 190), ("laps", "Laps", 45),
                 ("best", "Best lap", 80), ("times", "Lap times", 210), ("km", "Driven km", 80))
        for key, text, width in heads:
            self.drivers_tree.heading(key, text=text)
            self.drivers_tree.column(key, width=width, anchor="w" if key in ("driver", "team", "times") else "center")
        paned.add(drivers, weight=3)

        stand = ttk.Labelframe(paned, text="Standings at the selected moment (use the time bar below)", padding=4)
        scols = ("pos", "driver", "lap", "tgap", "ahead", "leader", "speed", "dist", "distlong")
        self.stand_tree = scrolled_tree(stand, columns=scols, show="headings", height=7)
        # Headings that carry a unit are set by _apply_unit_headings().
        for key, text, width in (("pos", "Pos", 40), ("driver", "Driver", 200),
                                 ("lap", "Lap", 45), ("tgap", "Time gap ahead", 110), ("ahead", "", 140),
                                 ("leader", "", 130), ("speed", "", 90),
                                 ("dist", "", 100), ("distlong", "", 100)):
            self.stand_tree.heading(key, text=text)
            self.stand_tree.column(key, width=width, anchor="w" if key == "driver" else "center")
        for lvl, colour in battles.LEVEL_LIGHT.items():          # row colour = time gap to the car ahead
            self.stand_tree.tag_configure(lvl, background=colour)
        paned.add(stand, weight=2)

    def _apply_track_panel(self, head=None):
        self.track_btn.configure(text=("v Track data" if self.track_open else "> Track data"))
        if self.track_open:
            self.track_body.pack(fill="x", after=head) if head is not None else self.track_body.pack(fill="x")
        else:
            self.track_body.pack_forget()

    def toggle_track_panel(self):
        self.track_open = not self.track_open
        self._apply_track_panel(self.track_btn.master)
        save_config(track_panel_open=self.track_open)

    # ---- tab 2: overtakes
    def _build_overtakes(self, parent, cfg):
        row1 = ttk.Frame(parent)
        row1.pack(fill="x", pady=(2, 2))
        ttk.Label(row1, text="Driver:").pack(side="left")
        self.driver_var = tk.StringVar(value=self.ALL_DRIVERS)
        self.driver_box = ttk.Combobox(row1, textvariable=self.driver_var, state="readonly",
                                       values=[self.ALL_DRIVERS], width=30)
        self.driver_box.pack(side="left", padx=6)
        self.driver_box.bind("<<ComboboxSelected>>", lambda e: self.fill_overtakes())
        self.by_var = tk.BooleanVar(value=True)
        self.passed_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row1, text="Overtakes by this driver", variable=self.by_var,
                        command=self.fill_overtakes).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(row1, text="Driver overtaken", variable=self.passed_var,
                        command=self.fill_overtakes).pack(side="left", padx=(6, 0))
        ttk.Button(row1, text="Tick all shown", command=lambda: self.tick_shown(True)).pack(side="left", padx=(14, 0))
        ttk.Button(row1, text="Untick all shown", command=lambda: self.tick_shown(False)).pack(side="left", padx=6)

        row2 = ttk.Frame(parent)
        row2.pack(fill="x", pady=(0, 4))
        ttk.Label(row2, text="Default clip:  before").pack(side="left")
        self.before_var = tk.StringVar(value=str(self.default_before))
        self.after_var = tk.StringVar(value=str(self.default_after))
        spin_before = ttk.Spinbox(row2, from_=0, to=clips.MAX_CLIP_S, width=4, textvariable=self.before_var,
                                  command=self._on_default_changed)
        spin_before.pack(side="left", padx=4)
        ttk.Label(row2, text="s   after").pack(side="left")
        spin_after = ttk.Spinbox(row2, from_=0, to=clips.MAX_CLIP_S, width=4, textvariable=self.after_var,
                                 command=self._on_default_changed)
        spin_after.pack(side="left", padx=4)
        for spin in (spin_before, spin_after):          # typed values count too
            spin.bind("<Return>", lambda e: self._on_default_changed())
            spin.bind("<FocusOut>", lambda e: self._on_default_changed())
        ttk.Label(row2, text="s").pack(side="left")
        self.ov_count = tk.StringVar(value="")
        ttk.Label(row2, textvariable=self.ov_count).pack(side="left", padx=14)
        self.chart_visible = bool(cfg.get("chart_visible", True))
        self.chart_btn = ttk.Button(row2, text="", width=12, command=self.toggle_chart)
        self.chart_btn.pack(side="right")

        self.work = ttk.Panedwindow(parent, orient="vertical")
        self.work.pack(fill="both", expand=True)
        upper = ttk.Panedwindow(self.work, orient="horizontal")
        self.upper = upper
        self._sashes_set = False
        self._cfg_sashes = (cfg.get("sash_h"), cfg.get("sash_v"))

        # list (left) with the clip sliders under it
        left = ttk.Frame(upper)
        self.clip_panel = ttk.Frame(left, padding=(0, 4, 0, 0))
        ttk.Label(self.clip_panel, text="Selected overtake(s):").pack(side="left")
        self.sel_before = self._slider_box(self.clip_panel, "Before", "before")
        self.sel_after = self._slider_box(self.clip_panel, "After", "after")
        ttk.Button(self.clip_panel, text="Reset to default", command=self.reset_selected_clips).pack(side="left", padx=10)
        self.clip_panel_shown = False
        ocols = ("tick", "time", "lap", "who", "passed", "before", "after", "notes")
        self.ov_tree = scrolled_tree(left, columns=ocols, show="headings", height=10, selectmode="extended")
        for key, text, width in (("tick", "Use", 45), ("time", "Replay time", 90), ("lap", "Lap", 45),
                                 ("who", "Overtaker", 170), ("passed", "Passed", 170),
                                 ("before", "Before (s)", 70), ("after", "After (s)", 70),
                                 ("notes", "Notes", 230)):
            self.ov_tree.heading(key, text=text)
            self.ov_tree.column(key, width=width, anchor="w" if key in ("who", "passed", "notes") else "center")
        self.ov_tree.bind("<<TreeviewSelect>>", self._on_overtake_select)
        self.ov_tree.bind("<Button-1>", self._on_overtake_click)
        self.ov_tree.bind("<space>", self._on_overtake_space)
        upper.add(left, weight=3)

        # track map (right)
        map_box = ttk.Labelframe(upper, text="Track map (wheel = zoom, drag = move, double-click = reset)", padding=4)
        mbar = ttk.Frame(map_box)
        mbar.pack(fill="x")
        self.trail_var = tk.StringVar(value=str(cfg.get("map_trail_s", mapview.TRAIL_S)))
        self.ahead_var = tk.StringVar(value=str(cfg.get("map_ahead_s", mapview.AHEAD_S)))
        ttk.Label(mbar, text="Trail behind").pack(side="left")
        sp1 = ttk.Spinbox(mbar, from_=0, to=20, width=3, textvariable=self.trail_var, command=self._on_map_options)
        sp1.pack(side="left", padx=4)
        ttk.Label(mbar, text="s   Look ahead").pack(side="left")
        sp2 = ttk.Spinbox(mbar, from_=0, to=20, width=3, textvariable=self.ahead_var, command=self._on_map_options)
        sp2.pack(side="left", padx=4)
        ttk.Label(mbar, text="s").pack(side="left")
        for spin in (sp1, sp2):
            spin.bind("<Return>", lambda e: self._on_map_options())
            spin.bind("<FocusOut>", lambda e: self._on_map_options())
        mbar2 = ttk.Frame(map_box)
        mbar2.pack(fill="x", pady=(2, 0))
        ttk.Label(mbar2, text="Zoom on selecting an overtake:").pack(side="left")
        saved = cfg.get("map_zoom")
        self.zoom_var = tk.StringVar(value=dict(self.ZOOM_CHOICES).get(saved, "Half"))
        zbox = ttk.Combobox(mbar2, textvariable=self.zoom_var, state="readonly", width=7,
                            values=[label for _k, label in self.ZOOM_CHOICES])
        zbox.pack(side="left", padx=4)
        zbox.bind("<<ComboboxSelected>>", lambda e: self._on_map_options())
        self.initials_var = tk.BooleanVar(value=bool(cfg.get("map_initials", True)))
        ttk.Checkbutton(mbar2, text="Initials on other cars", variable=self.initials_var,
                        command=self._on_map_options).pack(side="left", padx=(12, 0))
        self.gap_lines_var = tk.BooleanVar(value=bool(cfg.get("map_gap_lines", True)))
        ttk.Checkbutton(mbar2, text="Gap colours", variable=self.gap_lines_var,
                        command=self._on_map_options).pack(side="left", padx=(12, 0))
        self.map_canvas = tk.Canvas(map_box, background=mapview.GRASS, height=200, width=200, highlightthickness=0)
        self.map_canvas.pack(fill="both", expand=True)
        self.map = mapview.MapView(self.map_canvas, COLORS)
        self._on_map_options(redraw=False)
        upper.add(map_box, weight=4)
        self.work.add(upper, weight=4)

        # chart (bottom, can be hidden)
        self.chart_box = ttk.Labelframe(self.work, text="Race chart (wheel = zoom, click = jump, "
                                        "click a marker = select overtake)", padding=4)
        self.chart = chartview.ChartView(self.chart_box, self, COLORS)
        if self.chart_visible:
            self.work.add(self.chart_box, weight=2)
        self._label_chart_button()

    def _label_chart_button(self):
        self.chart_btn.configure(text="Hide chart" if self.chart_visible else "Show chart")

    def toggle_chart(self):
        self.chart_visible = not self.chart_visible
        if self.chart_visible:
            self.work.add(self.chart_box, weight=2)
            self.after(80, self._place_chart_sash)
        else:
            self.work.forget(self.chart_box)
        self._label_chart_button()
        save_config(chart_visible=self.chart_visible)

    ZOOM_CHOICES = (("off", "Off"), ("half", "Half"), ("full", "Full"))

    def _zoom_level(self):
        label = self.zoom_var.get()
        return next((k for k, text in self.ZOOM_CHOICES if text == label), "half")

    def _on_map_options(self, redraw=True):
        def seconds(var, fallback):
            try:
                return max(0, min(20, int(round(float(var.get())))))
            except (TypeError, ValueError):
                return fallback
        self.map.trail_s = seconds(self.trail_var, mapview.TRAIL_S)
        self.map.ahead_s = seconds(self.ahead_var, mapview.AHEAD_S)
        self.trail_var.set(str(self.map.trail_s))
        self.ahead_var.set(str(self.map.ahead_s))
        self.map.initials = bool(self.initials_var.get())
        self.map.gap_lines = bool(self.gap_lines_var.get())
        if hasattr(self, "battle_map"):
            self.battle_map.trail_s, self.battle_map.ahead_s = self.map.trail_s, self.map.ahead_s
            self.battle_map.initials = self.map.initials
            self.battle_map.gap_lines = self.map.gap_lines
        if redraw and self.model and self.selected_overtake is not None:     # show the new zoom at once
            o = self.overtakes[self.selected_overtake]
            self.map.zoom_to((o.overtaker, o.passed), o.frame, level=self._zoom_level())
        save_config(map_trail_s=self.map.trail_s, map_ahead_s=self.map.ahead_s,
                    map_zoom=self._zoom_level(), map_initials=self.map.initials,
                    map_gap_lines=self.map.gap_lines)
        if redraw and self.model:
            self._update_map()

    def _show_clip_panel(self, show):
        if show and not self.clip_panel_shown:
            self.clip_panel.pack(side="bottom", fill="x")
        elif not show and self.clip_panel_shown:
            self.clip_panel.pack_forget()
        self.clip_panel_shown = show

    # ---- tab 3: save
    def _build_save(self, parent):
        box = ttk.Frame(parent, padding=6)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="Clips of the ticked overtakes and battles (in the order they will be in the new replay; "
                            "clips that overlap are joined)").pack(anchor="w")
        ccols = ("n", "start", "end", "length", "what")
        self.clips_tree = scrolled_tree(box, columns=ccols, show="headings", height=8)
        for key, text, width in (("n", "Clip", 45), ("start", "From", 80), ("end", "To", 80),
                                 ("length", "Length", 70), ("what", "In this clip", 800)):
            self.clips_tree.heading(key, text=text)
            self.clips_tree.column(key, width=width, anchor="w" if key == "what" else "center")
        self.save_summary = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.save_summary).pack(anchor="w", pady=(4, 8))

        opts = ttk.Labelframe(box, text="New replay", padding=8)
        opts.pack(fill="x")
        row = ttk.Frame(opts)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Extra data from the original:", width=28).pack(side="left")
        self.trailer_var = tk.StringVar(value=self._trailer_label(load_config().get("trailer")))
        ttk.Combobox(row, textvariable=self.trailer_var, state="readonly", width=26,
                     values=[label for _k, label in replaywriter.TRAILERS]).pack(side="left")
        row = ttk.Frame(opts)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Camera follows:", width=28).pack(side="left")
        self.focus_var = tk.StringVar(value=self._focus_label(load_config().get("focus")))
        ttk.Combobox(row, textvariable=self.focus_var, state="readonly", width=26,
                     values=[label for _k, label in focusplan.FOCUS_CHOICES]).pack(side="left")
        row = ttk.Frame(opts)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="Save new replay...", command=self.save_replay).pack(side="left")
        ttk.Button(row, text="Export mp4... (planned)", state="disabled").pack(side="left", padx=8)

    def _fill_clip_overview(self):
        tree = self.clips_tree
        tree.delete(*tree.get_children())
        if not self.model:
            self.save_summary.set("Open a replay first.")
            return
        spans = self.clip_ranges()
        step = self.replay.interval_ms / 1000.0
        cars = self.replay.cars
        total = 0.0
        items = self.battle_clip_items()
        for n, (a, b) in enumerate(spans, start=1):
            parts = ["Battle: " + d["label"] for d in items if a <= d["first"] and d["last"] <= b]
            inside = [o for i, o in enumerate(self.overtakes) if i in self.ticked and a <= o.frame <= b]
            parts += ["%s > %s (%s)" % (cars[o.overtaker].driver, cars[o.passed].driver,
                                        analysis.fmt_clock(o.time_s)) for o in inside]
            what = ",  ".join(parts)
            length = (b - a + 1) * step
            total += length
            tree.insert("", "end", values=(n, analysis.fmt_clock(a * step), analysis.fmt_clock(b * step),
                                           "%.0f s" % length, what))
        if spans:
            self.save_summary.set("%d clip(s) from %d ticked overtake(s) and %d ticked battle(s)/phase(s), "
                                  "%s in total." % (len(spans), len(self.ticked), len(items),
                                                    analysis.fmt_clock(total)))
        else:
            self.save_summary.set("Nothing ticked yet: tick overtakes in the Overtakes tab or battles in "
                                  "the Battles tab (column 'Use').")

    def _current_tab(self):
        try:
            return self.TAB_NAMES[self.notebook.index("current")]
        except (tk.TclError, TypeError, ValueError, IndexError):
            return "race"

    def _on_tab_changed(self):
        self._fill_clip_overview()
        if not self._sashes_set:
            self.after(80, self._place_sashes)
        if self._current_tab() == "battles" and not self._battle_sash_set:
            self.after(80, self._place_battle_sash)

    def _place_chart_sash(self):
        try:
            h = self.work.winfo_height()
            fv = self._cfg_sashes[1]
            fv = fv if isinstance(fv, (int, float)) and 0.2 < fv < 0.9 else self.SASH_V
            if h > 200:
                self.work.sashpos(0, int(h * fv))
        except (tk.TclError, AttributeError, TypeError):
            pass

    SASH_H = 0.34       # share of the width taken by the overtakes list
    SASH_V = 0.66       # share of the height taken by list + map (the chart gets the rest)

    def _place_sashes(self):
        """Default proportions of the Overtakes tab: small list, big map, medium chart."""
        try:
            w, h = self.upper.winfo_width(), self.work.winfo_height()
            if w < 300 or h < 200:          # tab not shown yet: try again a few times
                self._sash_tries = getattr(self, "_sash_tries", 0) + 1
                if self._sash_tries < 20 and self._current_tab() == "overtakes":
                    self.after(250, self._place_sashes)
                return
            fh, fv = self._cfg_sashes
            fh = fh if isinstance(fh, (int, float)) and 0.1 < fh < 0.9 else self.SASH_H
            fv = fv if isinstance(fv, (int, float)) and 0.2 < fv < 0.9 else self.SASH_V
            self.upper.sashpos(0, int(w * fh))
            if self.chart_visible:
                self.work.sashpos(0, int(h * fv))
            self._sashes_set = True
        except (tk.TclError, AttributeError, TypeError):
            pass

    def _on_close(self):
        extra = self.battle_sash_shares()
        try:
            if self._sashes_set:
                w, h = self.upper.winfo_width(), self.work.winfo_height()
                if w > 300:
                    extra["sash_h"] = round(self.upper.sashpos(0) / w, 3)
                if h > 200 and self.chart_visible:
                    extra["sash_v"] = round(self.work.sashpos(0) / h, 3)
        except (tk.TclError, AttributeError, TypeError, ZeroDivisionError):
            pass
        save_config(geometry=self.geometry(), tab_name=self._current_tab(), **extra)
        self.destroy()

    # --------------------------------------------------------------- units
    def _apply_unit_headings(self):
        short, long_, speed = analysis.UNIT_NAMES[self.units]
        head = self.stand_tree.heading
        head("ahead", text="Gap to car ahead (%s)" % short)
        head("leader", text="Gap to leader (%s)" % short)
        head("speed", text="Speed (%s)" % speed)
        head("dist", text="Distance (%s)" % short)
        head("distlong", text="Distance (%s)" % long_)
        self.drivers_tree.heading("km", text="Driven (%s)" % long_)

    def on_units_changed(self):
        self.units = self.units_var.get()
        save_units(self.units)
        self._apply_unit_headings()
        if self.model:
            self.fill_drivers()
            self.set_frame(self.frame)
            self.chart.draw()

    def _gap_text(self, metres):
        value = analysis.short_length(metres, self.units)
        return ("%.1f" if self.units == analysis.METRIC else "%.0f") % value

    # --------------------------------------------------------- game folder
    def ensure_game_folder(self):
        """First launch: find the game by itself, or ask where it is."""
        if self.game_folder or load_config().get("game_folder_declined"):
            return
        found = gamefolder.detect_game_folder()
        if found:
            self.game_folder = found
            save_config(game_folder=found)
            return
        if messagebox.askyesno(
                "Assetto Corsa folder",
                "Where is Assetto Corsa installed?\n\nWith the game folder the program can read "
                "the track data (the AI racing line), which makes gaps and lap numbers exact.\n\n"
                "Choose the folder now? (Usually ...\\steamapps\\common\\assettocorsa. "
                "You can also do this later with 'Game folder...'.)"):
            self.choose_game_folder()
        else:
            save_config(game_folder_declined=True)

    def choose_game_folder(self):
        while True:
            path = filedialog.askdirectory(
                title="Select the Assetto Corsa folder (the one that contains 'content')",
                initialdir=self.game_folder or os.path.expanduser("~"))
            if not path:
                return
            folder = gamefolder.normalize_game_folder(path)
            if folder:
                break
            if not messagebox.askyesno(
                    "Not an Assetto Corsa folder",
                    "There is no 'content\\tracks' folder in:\n%s\n\nTry another folder?" % path):
                return
        self.game_folder = folder
        save_config(game_folder=folder, game_folder_declined=False)
        if self.replay is not None:        # use the new track data right away
            self.load(self.replay.path)

    # ----------------------------------------------------------- overtakes
    ALL_DRIVERS = "All drivers"

    # -------------------------------------------------------------- clips
    def _trailer_label(self, key):
        for k, label in replaywriter.TRAILERS:
            if k == key:
                return label
        return replaywriter.TRAILERS[0][1]

    def _focus_label(self, key):
        for k, label in focusplan.FOCUS_CHOICES:
            if k == key:
                return label
        return focusplan.FOCUS_CHOICES[0][1]

    def _focus_key(self):
        for k, label in focusplan.FOCUS_CHOICES:
            if label == self.focus_var.get():
                return k
        return focusplan.FOCUS_NONE

    def _trailer_key(self):
        for k, label in replaywriter.TRAILERS:
            if label == self.trailer_var.get():
                return k
        return replaywriter.TRAILER_NONE

    def _slider_box(self, parent, title, which):
        """[-] slider [+] and the value, for the clip length before/after."""
        box = ttk.Frame(parent)
        box.pack(side="left", padx=(10, 0))
        ttk.Label(box, text=title).pack(side="left")
        ttk.Button(box, text="-", width=2, command=lambda: self._nudge(which, -1)).pack(side="left", padx=(4, 0))
        scale = ttk.Scale(box, from_=0, to=clips.MAX_CLIP_S, orient="horizontal", length=170,
                          command=lambda v, w=which: self._on_slider(w, v))
        scale.pack(side="left")
        ttk.Button(box, text="+", width=2, command=lambda: self._nudge(which, 1)).pack(side="left")
        label = tk.StringVar(value="- s")
        ttk.Label(box, textvariable=label, width=6).pack(side="left", padx=(4, 0))
        return {"scale": scale, "label": label}

    def clip_span(self, index):
        """(seconds before, seconds after) used for one overtake."""
        return self.clip_override.get(index, (self.default_before, self.default_after))

    def clip_ranges(self):
        """Clips of the ticked overtakes and battles, joined where they overlap."""
        if not self.model:
            return []
        spans = clips.clip_ranges(self.model, self.overtakes, self.ticked, self.clip_span)
        spans += [(d["first"], d["last"]) for d in self.battle_clip_items()]
        return clips.merge(spans)

    def _on_default_changed(self):
        self.default_before = clips.clamp_seconds(self.before_var.get(), self.default_before)
        self.default_after = clips.clamp_seconds(self.after_var.get(), self.default_after)
        self.before_var.set(str(self.default_before))
        self.after_var.set(str(self.default_after))
        save_config(clip_before_s=self.default_before, clip_after_s=self.default_after)
        # hand-set clips that now equal the default are not "hand-set" any more
        self.clip_override = {i: v for i, v in self.clip_override.items()
                              if v != (self.default_before, self.default_after)}
        self._clips_changed()

    def _selected_indexes(self):
        return [int(r) for r in self.ov_tree.selection()]

    def _set_clip(self, index, before=None, after=None):
        b, a = self.clip_span(index)
        b = b if before is None else before
        a = a if after is None else after
        if (b, a) == (self.default_before, self.default_after):
            self.clip_override.pop(index, None)
        else:
            self.clip_override[index] = (b, a)

    def _on_slider(self, which, value):
        if self._syncing or not self.model:
            return
        v = clips.clamp_seconds(value)
        rows = self._selected_indexes()
        if not rows:
            return
        for i in rows:
            self._set_clip(i, **{which: v})
        self._clips_changed()

    def _nudge(self, which, delta):
        rows = self._selected_indexes()
        if not rows or not self.model:
            return
        current = self.clip_span(rows[0])[0 if which == "before" else 1]
        v = clips.clamp_seconds(current + delta)
        for i in rows:
            self._set_clip(i, **{which: v})
        self._clips_changed()

    def reset_selected_clips(self):
        for i in self._selected_indexes():
            self.clip_override.pop(i, None)
        self._clips_changed()

    def _clips_changed(self):
        """Refresh the columns, the sliders, the chart strip and the totals."""
        self._refresh_clip_columns()
        self._sync_sliders()
        self._update_tick_count()

    def _refresh_clip_columns(self):
        for i in self.shown_overtakes:
            b, a = self.clip_span(i)
            mark = "*" if i in self.clip_override else ""
            self.ov_tree.set(str(i), "before", "%d%s" % (b, mark))
            self.ov_tree.set(str(i), "after", "%d%s" % (a, mark))

    def _sync_sliders(self):
        """Show the values of the first selected overtake on the sliders."""
        rows = self._selected_indexes()
        self._syncing = True
        try:
            for box, k in ((self.sel_before, 0), (self.sel_after, 1)):
                if rows:
                    value = self.clip_span(rows[0])[k]
                    box["scale"].set(value)
                    box["label"].set("%d s" % value + ("" if len(rows) == 1 else " (+%d)" % (len(rows) - 1)))
                else:
                    box["label"].set("- s")
        finally:
            self._syncing = False

    def _write_focus_plan(self, target, spans, seconds, focus):
        gaps = self.battle_result.gaps if self.battle_result else None
        switches = focusplan.build(self.model, self.overtakes, self.ticked, spans, focus,
                                   self.battle_clip_items(), gaps)
        doc = focusplan.document(self.model, target, switches, seconds, focus, spans)
        try:
            sidecar = focusplan.save(doc, target)
        except OSError as exc:
            return "The camera plan could not be written: %s" % exc
        return ("Camera plan (%d switches): %s\nKeep it next to the replay: the Overtake Focus app in "
                "the game finds it by the replay's name (also when Content Manager renamed it to "
                "...focus.json.acreplay). Otherwise use 'Open plan...' in the app." % (len(switches), sidecar))

    def save_replay(self):
        if not self.model:
            messagebox.showinfo("Save new replay", "Open a replay first.")
            return
        spans = self.clip_ranges()
        if not spans:
            messagebox.showinfo("Save new replay", "Tick at least one overtake or battle first (column 'Use').")
            return
        if self.replay.strategy != "exact layout":
            messagebox.showerror("Save new replay", "This replay was only read in limited mode, "
                                 "so it cannot be cut into a new replay.")
            return
        key = self._trailer_key()
        focus = self._focus_key()
        save_config(trailer=key, focus=focus)
        stem = os.path.splitext(os.path.basename(self.replay.path))[0]
        target = filedialog.asksaveasfilename(
            title="Save new replay", defaultextension=".acreplay",
            initialdir=os.path.dirname(self.replay.path), initialfile=stem + "_export.acreplay",
            filetypes=[("AC replay", "*.acreplay")])
        if not target:
            return
        if os.path.normcase(os.path.abspath(target)) == os.path.normcase(os.path.abspath(self.replay.path)):
            # never overwrite the replay we are reading from
            target = os.path.splitext(target)[0] + "_export.acreplay"
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            frames, size = replaywriter.write_file(self.replay.path, target, spans, key)
        except (replaywriter.WriteError, acreplay.ReplayError, OSError) as exc:
            messagebox.showerror("Save new replay", str(exc))
            return
        finally:
            self.config(cursor="")
        seconds = frames * self.replay.interval_ms / 1000.0
        text = ("%d clip(s), %s long, %.1f MB:\n%s\n\nCopy it to your Assetto Corsa replay folder "
                "(Documents\\Assetto Corsa\\replay) to play it in the game." % (
                    len(spans), analysis.fmt_clock(seconds), size / 1e6, target))
        if focus != focusplan.FOCUS_NONE:
            text += "\n\n" + self._write_focus_plan(target, spans, seconds, focus)
        messagebox.showinfo("Replay saved", text)

    TICK_ON, TICK_OFF = "\u2611", "\u2610"

    def _overtake_visible(self, o):
        pick = self.driver_var.get()
        if pick == self.ALL_DRIVERS:
            return True
        cars = self.replay.cars
        return ((self.by_var.get() and cars[o.overtaker].driver == pick) or
                (self.passed_var.get() and cars[o.passed].driver == pick))

    def fill_overtakes(self):
        tree = self.ov_tree
        tree.delete(*tree.get_children())
        self.shown_overtakes = []        # indexes into self.overtakes
        if not self.model:
            self._update_tick_count()
            return
        cars = self.replay.cars
        for i, o in enumerate(self.overtakes):
            if not self._overtake_visible(o):
                continue
            self.shown_overtakes.append(i)
            tree.insert("", "end", iid=str(i), values=(
                self.TICK_ON if i in self.ticked else self.TICK_OFF,
                analysis.fmt_clock(o.time_s), "-" if o.lap is None else o.lap,
                cars[o.overtaker].driver, cars[o.passed].driver,
                self._clip_text(i, 0), self._clip_text(i, 1),
                ", ".join(o.flags) if o.flags else ""))
        self._update_tick_count()

    def selected_car(self):
        """Car index of the driver chosen in the filter (None for all drivers)."""
        pick = self.driver_var.get()
        if not self.model or pick == self.ALL_DRIVERS:
            return None
        for i in self.model.active:
            if self.replay.cars[i].driver == pick:
                return i
        return None

    def select_overtake(self, index):
        """Select an overtake (also from the chart): jump to it and highlight it."""
        if not self.model or not (0 <= index < len(self.overtakes)):
            return
        if index not in self.shown_overtakes:       # hidden by the filter: show everybody
            self.driver_var.set(self.ALL_DRIVERS)
            self.fill_overtakes()
        self.selected_overtake = index
        self.ov_tree.selection_set(str(index))
        self.ov_tree.see(str(index))
        self._sync_sliders()
        o = self.overtakes[index]
        self.map.zoom_to((o.overtaker, o.passed), o.frame, level=self._zoom_level())
        self.set_frame(o.frame)
        self.chart.draw()

    def _clip_text(self, index, k):
        return "%d%s" % (self.clip_span(index)[k], "*" if index in self.clip_override else "")

    def _update_tick_count(self):
        spans = self.clip_ranges()
        seconds = sum(b - a for a, b in spans) * (self.replay.interval_ms / 1000.0) if spans else 0
        self.ov_count.set("%d shown of %d overtakes   |   %d ticked, %d clip(s), %s in total "
                          "(with the ticked battles; * = clip length set by hand)" % (
                              len(self.shown_overtakes), len(self.overtakes), len(self.ticked), len(spans),
                              analysis.fmt_clock(seconds)))
        self._fill_clip_overview()
        self._battle_count_text()
        if self.model:
            self.chart.draw()
            self._update_map()

    def _set_tick(self, index, on):
        if on:
            self.ticked.add(index)
        else:
            self.ticked.discard(index)
        if str(index) in self.ov_tree.get_children():
            self.ov_tree.set(str(index), "tick", self.TICK_ON if on else self.TICK_OFF)

    def toggle_overtake(self, index):
        self._set_tick(index, index not in self.ticked)
        self._update_tick_count()

    def tick_shown(self, on):
        for i in self.shown_overtakes:
            self._set_tick(i, on)
        self._update_tick_count()

    def _on_overtake_click(self, event):
        tree = self.ov_tree
        if tree.identify_region(event.x, event.y) == "cell" and tree.identify_column(event.x) == "#1":
            row = tree.identify_row(event.y)
            if row:
                self.toggle_overtake(int(row))
                return "break"
        return None

    def _on_overtake_space(self, _event=None):
        rows = [int(r) for r in self.ov_tree.selection()]
        if rows:
            on = rows[0] not in self.ticked      # all selected rows follow the first one
            for i in rows:
                self._set_tick(i, on)
            self._update_tick_count()
        return "break"

    def _on_overtake_select(self, _event=None):
        sel = self.ov_tree.selection()
        if not sel:
            self._show_clip_panel(False)
        if sel and self.model:
            index = int(sel[0])
            self._sync_sliders()
            self._show_clip_panel(True)
            if self.model and len(sel) == 1:
                o = self.overtakes[index]
                self.map.zoom_to((o.overtaker, o.passed), o.frame, level=self._zoom_level())
            if index != self.selected_overtake:
                self.selected_overtake = index
                self.set_frame(self.overtakes[index].frame)
                self.chart.draw()

    # ------------------------------------------------------------- loading
    def open_dialog(self):
        path = filedialog.askopenfilename(
            title="Open Assetto Corsa replay",
            initialdir=default_replay_dir(),
            filetypes=[("AC replay", "*.acreplay"), ("All files", "*.*")])
        if path:
            self.load(path)

    def load(self, path):
        self.path_var.set("Loading %s ..." % os.path.basename(path))
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            replay = acreplay.load(path)
            if replay.frame_count < 2:
                raise acreplay.ReplayError("The replay contains no frames.")
            line, self.line_note = analysis.choose_line(replay, self.game_folder)
            roadmap, self.road_note = analysis.choose_roadmap(replay, self.game_folder)
            model = analysis.RaceModel(replay, line, roadmap)
            found = overtakes.find_overtakes(model)
            measured = battles.measure(model, found)
        except acreplay.ReplayError as exc:
            self.path_var.set("No replay loaded")
            self._offer_report(path, exc)
            return
        except OSError as exc:
            self.path_var.set("No replay loaded")
            messagebox.showerror("Cannot open replay", str(exc))
            return
        finally:
            self.config(cursor="")
        self.replay, self.model = replay, model
        self.selected_overtake = None
        self.clip_override = {}
        self.overtakes = found
        self.ticked = set()
        names = sorted({replay.cars[i].driver for i in model.active})
        self.driver_box.configure(values=[self.ALL_DRIVERS] + names)
        self.driver_var.set(self.ALL_DRIVERS)
        self.by_var.set(True)
        self.passed_var.set(True)
        self.path_var.set(path)
        layout = replay.layout or "-"
        text = ("Track: %s (layout %s)   Weather: %s   Cars: %d   Length: %s   Format v%d, read with: %s"
                % (replay.track, layout, replay.weather, len(replay.cars),
                   analysis.fmt_clock(replay.time_s(replay.frame_count - 1)),
                   replay.version, replay.strategy))
        text += "\n" + self.line_note
        if self.road_note:
            text += "\n" + self.road_note
        if replay.warnings:
            text += "\nNote: " + " ".join(replay.warnings)
        self.info_var.set(text)
        problem = line is None or roadmap is None or bool(replay.warnings)
        self.summary_var.set("%s%s | %d cars | %s | racing line: %s | road surface: %s" % (
            "(!) " if problem else "", replay.track, len(replay.cars),
            analysis.fmt_clock(replay.time_s(replay.frame_count - 1)),
            "yes" if line is not None else "no (gaps estimated)",
            "yes" if roadmap is not None else "no (tyre dirt used)"))
        self.map.set_model(model, roadmap)
        self.battles_loaded(measured)
        self.fill_drivers()
        self.fill_overtakes()
        self.scale.configure(to=replay.frame_count - 1)
        self.chart.set_model()
        self.set_frame(0)

    def _offer_report(self, path, exc):
        """Explain the failure and offer a small text report that can be shared."""
        message = str(exc)
        if exc.report and messagebox.askyesno(
                "Cannot open replay",
                message + "\n\nSave a diagnostic report? It is a small text file with "
                "technical details of this replay (track, car and driver names, some raw "
                "bytes) that helps to add support for it."):
            target = filedialog.asksaveasfilename(
                title="Save diagnostic report", defaultextension=".txt",
                initialfile=os.path.basename(path) + ".diagnostic.txt",
                filetypes=[("Text file", "*.txt")])
            if target:
                with open(target, "w", encoding="utf-8") as fh:
                    fh.write(exc.report + "\n")
        elif not exc.report:
            messagebox.showerror("Cannot open replay", message)

    def fill_drivers(self):
        tree = self.drivers_tree
        tree.delete(*tree.get_children())
        final = [p[-1] if p is not None else None for p in self.model.progress]
        pos = 0
        for car in analysis.classification(self.replay, final):
            if car.has_positions:
                pos += 1
                km = "%.2f" % analysis.long_length(final[car.index], self.units)
                times = "  |  ".join(analysis.fmt_lap(ms) for ms in car.lap_times_ms) or "-"
                rank = pos
            else:
                km, times, rank = "-", "no position data in this replay", "-"
            tree.insert("", "end", values=(rank, car.driver or "(no name)", self.map.labels[car.index],
                                           car.nation,
                                           car.team or car.skin, len(car.lap_times_ms),
                                           analysis.fmt_lap(car.best_lap_ms), times, km))

    # --------------------------------------------------------- interaction
    def step(self, delta):
        if self.model:
            self.set_frame(self.frame + delta)

    def _on_scale(self, value):
        if self.model:
            self.set_frame(int(float(value)), from_scale=True)

    def set_frame(self, frame, from_scale=False):
        frame = max(0, min(int(frame), self.model.frames - 1))
        self.frame = frame
        if not from_scale:
            self.scale.set(frame)
        self.frame_var.set("Replay time %s of %s   (frame %d / %d)" % (
            analysis.fmt_clock(self.model.time_s(frame)),
            analysis.fmt_clock(self.model.time_s(self.model.frames - 1)),
            frame, self.model.frames - 1))
        tree = self.stand_tree
        tree.delete(*tree.get_children())
        gaps = self.battle_result.gaps if self.battle_result else None
        close_s = self.battle_settings.close_s
        ahead = None
        for s in self.model.standings(frame):
            tgap, tags = "-", ()
            if gaps is not None and ahead is not None and gaps.lap(ahead, s.car_index, frame) == 0:
                g = gaps.gap(ahead, s.car_index, frame)
                side = gaps.side(ahead, s.car_index, frame)
                tgap = "side by side" if side else "%.2f s" % abs(g)
                lvl = battles.level(g, side, close_s)
                tags = (lvl,) if lvl else ()
            ahead = s.car_index
            tree.insert("", "end", tags=tags, values=(
                s.position, s.driver,
                "-" if s.lap is None else s.lap, tgap,
                "-" if s.position == 1 else self._gap_text(s.gap_ahead_m),
                "-" if s.position == 1 else self._gap_text(s.gap_leader_m),
                "-" if s.speed_kmh is None else "%.0f" % analysis.speed_value(s.speed_kmh, self.units),
                "%.0f" % analysis.short_length(s.progress_m, self.units),
                "%.2f" % analysis.long_length(s.progress_m, self.units)))
        self.chart.follow()
        self.chart.draw_cursor()
        self._update_map()

    def _update_map(self):
        self.map.frame = self.frame
        self.map.units = self.units
        self.map.selected = self.selected_car()
        o = self.overtakes[self.selected_overtake] if self.selected_overtake is not None else None
        self.map.highlight = (o.overtaker, o.passed) if o else ()
        self.map.draw_cars()
        self.update_battle_map()


App._scrolled_tree = staticmethod(scrolled_tree)
App._save_config = staticmethod(save_config)


def main():
    app = App()
    app.after(50, app.ensure_game_folder)
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        app.after(100, lambda: app.load(sys.argv[1]))
    app.mainloop()


if __name__ == "__main__":
    main()
