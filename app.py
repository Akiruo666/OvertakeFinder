"""AC Overtake Finder - race overview screen.

Run:  python app.py [path\to\replay.acreplay]

Needs only Python 3.9+ with Tkinter (included in the standard Windows installer).
"""
import json
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import acreplay
import analysis
import chartview
import clips
import gamefolder
import mapview
import overtakes

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


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AC Overtake Finder - Race overview")
        self.geometry("1280x980")
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
        self._build()

    # ------------------------------------------------------------------ UI
    def _build(self):
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

        self.info_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.info_var, padding=(8, 0)).pack(fill="x")

        paned = ttk.Panedwindow(self, orient="vertical")
        paned.pack(fill="both", expand=True, padx=8, pady=8)

        # Drivers table
        drivers = ttk.Labelframe(paned, text="Drivers and lap times", padding=4)
        cols = ("pos", "driver", "nation", "team", "laps", "best", "times", "km")
        self.drivers_tree = scrolled_tree(drivers, horizontal=True, columns=cols, show="headings", height=7)
        heads = (("pos", "Pos", 40), ("driver", "Driver", 190), ("nation", "Nat", 45),
                 ("team", "Team / livery", 190), ("laps", "Laps", 45),
                 ("best", "Best lap", 80), ("times", "Lap times", 210), ("km", "Driven km", 80))
        for key, text, width in heads:
            self.drivers_tree.heading(key, text=text)
            self.drivers_tree.column(key, width=width, anchor="w" if key in ("driver", "team", "times") else "center")
        paned.add(drivers, weight=2)

        # Standings at a chosen moment
        stand = ttk.Labelframe(paned, text="Standings at selected moment", padding=4)
        bar = ttk.Frame(stand)
        bar.pack(fill="x")
        ttk.Button(bar, text="<<", width=4, command=lambda: self.step(-100)).pack(side="left")
        ttk.Button(bar, text="<", width=4, command=lambda: self.step(-10)).pack(side="left")
        self.scale = ttk.Scale(bar, from_=0, to=1, orient="horizontal", command=self._on_scale)
        self.scale.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(bar, text=">", width=4, command=lambda: self.step(10)).pack(side="left")
        ttk.Button(bar, text=">>", width=4, command=lambda: self.step(100)).pack(side="left")
        self.frame_var = tk.StringVar(value="Frame - / -")
        ttk.Label(stand, textvariable=self.frame_var).pack(anchor="w")

        scols = ("pos", "driver", "lap", "ahead", "leader", "speed", "dist", "distlong")
        self.stand_tree = scrolled_tree(stand, columns=scols, show="headings", height=7)
        # Headings that carry a unit are set by _apply_unit_headings().
        for key, text, width in (("pos", "Pos", 40), ("driver", "Driver", 200),
                                 ("lap", "Lap", 45), ("ahead", "", 140),
                                 ("leader", "", 130), ("speed", "", 90),
                                 ("dist", "", 100), ("distlong", "", 100)):
            self.stand_tree.heading(key, text=text)
            self.stand_tree.column(key, width=width, anchor="w" if key == "driver" else "center")
        paned.add(stand, weight=2)

        # Overtakes
        ov = ttk.Labelframe(paned, text="Overtakes", padding=4)
        obar = ttk.Frame(ov)
        obar.pack(fill="x")
        ttk.Label(obar, text="Driver:").pack(side="left")
        self.driver_var = tk.StringVar(value=self.ALL_DRIVERS)
        self.driver_box = ttk.Combobox(obar, textvariable=self.driver_var, state="readonly",
                                       values=[self.ALL_DRIVERS], width=30)
        self.driver_box.pack(side="left", padx=6)
        self.driver_box.bind("<<ComboboxSelected>>", lambda e: self.fill_overtakes())
        self.by_var = tk.BooleanVar(value=True)
        self.passed_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(obar, text="Overtakes by this driver", variable=self.by_var,
                        command=self.fill_overtakes).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(obar, text="Driver overtaken", variable=self.passed_var,
                        command=self.fill_overtakes).pack(side="left", padx=(6, 0))
        obar2 = ttk.Frame(ov)
        obar2.pack(fill="x", pady=(4, 2))
        ttk.Button(obar2, text="Tick all shown", command=lambda: self.tick_shown(True)).pack(side="left")
        ttk.Button(obar2, text="Untick all shown", command=lambda: self.tick_shown(False)).pack(side="left", padx=6)
        self.ov_count = tk.StringVar(value="")
        ttk.Label(obar2, textvariable=self.ov_count).pack(side="left", padx=10)
        ocols = ("tick", "time", "lap", "who", "passed", "notes")
        self.ov_tree = scrolled_tree(ov, columns=ocols, show="headings", height=6, selectmode="extended")
        for key, text, width in (("tick", "Use", 45), ("time", "Replay time", 90), ("lap", "Lap", 45),
                                 ("who", "Overtaker", 200), ("passed", "Passed", 200),
                                 ("notes", "Notes", 300)):
            self.ov_tree.heading(key, text=text)
            self.ov_tree.column(key, width=width, anchor="w" if key in ("who", "passed", "notes") else "center")
        self.ov_tree.bind("<<TreeviewSelect>>", self._on_overtake_select)
        self.ov_tree.bind("<Button-1>", self._on_overtake_click)
        self.ov_tree.bind("<space>", self._on_overtake_space)
        paned.add(ov, weight=2)

        # Chart and track map side by side
        bottom = ttk.Panedwindow(paned, orient="horizontal")
        chart_box = ttk.Labelframe(bottom, text="Race chart (wheel = zoom, click = jump, click a marker = select overtake)",
                                   padding=4)
        self.chart = chartview.ChartView(chart_box, self, COLORS)
        bottom.add(chart_box, weight=3)
        map_box = ttk.Labelframe(bottom, text="Track map (wheel = zoom, drag = move, double-click = reset)", padding=4)
        self.map_canvas = tk.Canvas(map_box, background=mapview.GRASS, height=240, width=320, highlightthickness=0)
        self.map_canvas.pack(fill="both", expand=True)
        self.map = mapview.MapView(self.map_canvas, COLORS)
        bottom.add(map_box, weight=2)
        paned.add(bottom, weight=3)

        ttk.Label(self, padding=(8, 0, 8, 6), foreground="#666666",
                  text="Distance is measured from the start line (negative on the grid) along the track's "
                       "racing line, or from the distance driven when no track data is available. "
                       "Times are replay time from the start of the recording.").pack(fill="x")

        self.bind("<Left>", lambda e: self.step(-10))
        self.bind("<Right>", lambda e: self.step(10))
        self._apply_unit_headings()

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
        self.set_frame(self.overtakes[index].frame)
        self.chart.draw()

    def _update_tick_count(self):
        spans = clips.clip_ranges(self.model, self.overtakes, self.ticked) if self.model else []
        seconds = sum(b - a for a, b in spans) * (self.replay.interval_ms / 1000.0) if spans else 0
        self.ov_count.set("%d shown of %d overtakes   |   %d ticked, %d clip(s), %s in total "
                          "(%d s before, %d s after each)" % (
                              len(self.shown_overtakes), len(self.overtakes), len(self.ticked), len(spans),
                              analysis.fmt_clock(seconds), clips.CLIP_BEFORE_S, clips.CLIP_AFTER_S))
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
        if sel and self.model:
            index = int(sel[0])
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
        self.fill_drivers()
        self.fill_overtakes()
        self.scale.configure(to=replay.frame_count - 1)
        self.chart.set_model()
        self.map.set_model(model, roadmap)
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
            tree.insert("", "end", values=(rank, car.driver or "(no name)", car.nation,
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
        for s in self.model.standings(frame):
            tree.insert("", "end", values=(
                s.position, s.driver,
                "-" if s.lap is None else s.lap,
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
        self.map.selected = self.selected_car()
        o = self.overtakes[self.selected_overtake] if self.selected_overtake is not None else None
        self.map.highlight = (o.overtaker, o.passed) if o else ()
        self.map.draw_cars()


def main():
    app = App()
    app.after(50, app.ensure_game_folder)
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        app.after(100, lambda: app.load(sys.argv[1]))
    app.mainloop()


if __name__ == "__main__":
    main()
