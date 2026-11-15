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
import gamefolder
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


def default_replay_dir():
    docs = os.path.join(os.path.expanduser("~"), "Documents",
                        "Assetto Corsa", "replay")
    return docs if os.path.isdir(docs) else os.path.expanduser("~")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AC Overtake Finder - Race overview")
        self.geometry("1000x940")
        self.minsize(820, 640)
        self.replay = None
        self.model = None
        self.gap_series = []
        self.frame = 0
        self.units = load_units()
        cfg = load_config()
        self.game_folder = gamefolder.normalize_game_folder(cfg.get("game_folder"))
        self.line_note = ""
        self.overtakes = []
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
        self.drivers_tree = ttk.Treeview(drivers, columns=cols, show="headings", height=7)
        heads = (("pos", "Pos", 40), ("driver", "Driver", 190), ("nation", "Nat", 45),
                 ("team", "Team / livery", 190), ("laps", "Laps", 45),
                 ("best", "Best lap", 80), ("times", "Lap times", 210), ("km", "Driven km", 80))
        for key, text, width in heads:
            self.drivers_tree.heading(key, text=text)
            self.drivers_tree.column(key, width=width, anchor="w" if key in ("driver", "team", "times") else "center")
        self.drivers_tree.pack(fill="both", expand=True)
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
        self.stand_tree = ttk.Treeview(stand, columns=scols, show="headings", height=7)
        # Headings that carry a unit are set by _apply_unit_headings().
        for key, text, width in (("pos", "Pos", 40), ("driver", "Driver", 200),
                                 ("lap", "Lap", 45), ("ahead", "", 140),
                                 ("leader", "", 130), ("speed", "", 90),
                                 ("dist", "", 100), ("distlong", "", 100)):
            self.stand_tree.heading(key, text=text)
            self.stand_tree.column(key, width=width, anchor="w" if key == "driver" else "center")
        self.stand_tree.pack(fill="both", expand=True)
        paned.add(stand, weight=2)

        # Overtakes
        ov = ttk.Labelframe(paned, text="Overtakes", padding=4)
        obar = ttk.Frame(ov)
        obar.pack(fill="x")
        ttk.Label(obar, text="Driver:").pack(side="left")
        self.driver_var = tk.StringVar(value=self.ALL_DRIVERS)
        self.driver_box = ttk.Combobox(obar, textvariable=self.driver_var, state="readonly",
                                       values=[self.ALL_DRIVERS], width=32)
        self.driver_box.pack(side="left", padx=6)
        self.driver_box.bind("<<ComboboxSelected>>", lambda e: self.fill_overtakes())
        self.ov_count = tk.StringVar(value="")
        ttk.Label(obar, textvariable=self.ov_count).pack(side="left", padx=10)
        ocols = ("time", "lap", "who", "passed", "notes")
        self.ov_tree = ttk.Treeview(ov, columns=ocols, show="headings", height=6, selectmode="extended")
        for key, text, width in (("time", "Replay time", 90), ("lap", "Lap", 45),
                                 ("who", "Overtaker", 200), ("passed", "Passed", 200),
                                 ("notes", "Notes", 300)):
            self.ov_tree.heading(key, text=text)
            self.ov_tree.column(key, width=width, anchor="w" if key in ("who", "passed", "notes") else "center")
        self.ov_tree.pack(fill="both", expand=True)
        self.ov_tree.bind("<<TreeviewSelect>>", self._on_overtake_select)
        paned.add(ov, weight=2)

        # Gap chart
        chart = ttk.Labelframe(paned, text="Gap to leader over the race (click to jump)", padding=4)
        self.canvas = tk.Canvas(chart, background="white", height=220, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.draw_chart())
        self.canvas.bind("<Button-1>", self._on_chart_click)
        self.canvas.bind("<B1-Motion>", self._on_chart_click)
        paned.add(chart, weight=2)

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
            self.draw_chart()

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

    def fill_overtakes(self):
        tree = self.ov_tree
        tree.delete(*tree.get_children())
        self.shown_overtakes = []
        if not self.model:
            return
        cars = self.replay.cars
        pick = self.driver_var.get()
        for o in self.overtakes:
            who, passed = cars[o.overtaker].driver, cars[o.passed].driver
            if pick != self.ALL_DRIVERS and pick not in (who, passed):
                continue
            self.shown_overtakes.append(o)
            tree.insert("", "end", values=(
                analysis.fmt_clock(o.time_s), "-" if o.lap is None else o.lap, who, passed,
                ", ".join(o.flags) if o.flags else ""))
        self.ov_count.set("%d overtakes shown (of %d). Choose a driver to see only his overtakes "
                          "and the ones he suffered." % (len(self.shown_overtakes), len(self.overtakes)))

    def _on_overtake_select(self, _event=None):
        sel = self.ov_tree.selection()
        if sel and self.model:
            idx = self.ov_tree.index(sel[0])
            if idx < len(self.shown_overtakes):
                self.set_frame(self.shown_overtakes[idx].frame)

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
            model = analysis.RaceModel(replay, line)
            series = model.gap_to_leader_series()
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
        self.replay, self.model, self.gap_series = replay, model, series
        self.overtakes = found
        names = sorted({replay.cars[i].driver for i in model.active})
        self.driver_box.configure(values=[self.ALL_DRIVERS] + names)
        self.driver_var.set(self.ALL_DRIVERS)
        self.path_var.set(path)
        layout = replay.layout or "-"
        text = ("Track: %s (layout %s)   Weather: %s   Cars: %d   Length: %s   Format v%d, read with: %s"
                % (replay.track, layout, replay.weather, len(replay.cars),
                   analysis.fmt_clock(replay.time_s(replay.frame_count - 1)),
                   replay.version, replay.strategy))
        text += "\n" + self.line_note
        if replay.warnings:
            text += "\nNote: " + " ".join(replay.warnings)
        self.info_var.set(text)
        self.fill_drivers()
        self.fill_overtakes()
        self.scale.configure(to=replay.frame_count - 1)
        self.set_frame(0)
        self.draw_chart()

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
        self.draw_cursor()

    # --------------------------------------------------------------- chart
    MARGIN = (52, 14, 14, 26)  # left, top, right, bottom

    def _plot_area(self):
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        l, t, r, b = self.MARGIN
        return l, t + 18, w - r, h - b  # extra 18 px on top for the legend

    def draw_chart(self):
        c = self.canvas
        c.delete("all")
        if not self.model:
            c.create_text(20, 20, anchor="nw", text="Open a replay to see the race overview.")
            return
        x0, y0, x1, y1 = self._plot_area()
        if x1 - x0 < 50 or y1 - y0 < 30:
            return
        n = self.model.frames
        unit = analysis.UNIT_NAMES[self.units][0]
        scale = analysis.short_length(1.0, self.units)       # display units per metre
        top = (max(max(s) for s in self.gap_series if s is not None) * scale) or 1.0
        step = nice_step(top)
        ymax = (int(top / step) + 1) * step
        for v in range(0, ymax + 1, step):
            y = y0 + (y1 - y0) * v / ymax
            c.create_line(x0, y, x1, y, fill="#e6e6e6")
            c.create_text(x0 - 6, y, anchor="e", text="%d %s" % (v, unit), fill="#555555",
                          font=("TkDefaultFont", 8))
        stride = max(1, n // max(1, (x1 - x0)))
        for i, series in enumerate(self.gap_series):
            if series is None:
                continue
            pts = []
            for f in range(0, n, stride):
                pts.extend((x0 + (x1 - x0) * f / (n - 1),
                            y0 + (y1 - y0) * series[f] * scale / ymax))
            c.create_line(*pts, fill=COLORS[i % len(COLORS)], width=2)
        c.create_text(x0, y1 + 12, anchor="w", text=analysis.fmt_clock(0), fill="#555555", font=("TkDefaultFont", 8))
        c.create_text(x1, y1 + 12, anchor="e", text=analysis.fmt_clock(self.model.time_s(n - 1)),
                      fill="#555555", font=("TkDefaultFont", 8))
        lx = x0
        for i, car in enumerate(self.replay.cars):
            if self.gap_series[i] is None:
                continue
            c.create_rectangle(lx, 6, lx + 10, 16, fill=COLORS[i % len(COLORS)], outline="")
            label = car.driver if len(car.driver) < 18 else car.driver[:16] + ".."
            tid = c.create_text(lx + 14, 11, anchor="w", text=label, font=("TkDefaultFont", 8))
            lx = c.bbox(tid)[2] + 12
        self.draw_cursor()

    def draw_cursor(self):
        c = self.canvas
        c.delete("cursor")
        if not self.model:
            return
        x0, y0, x1, y1 = self._plot_area()
        if x1 - x0 < 50:
            return
        x = x0 + (x1 - x0) * self.frame / (self.model.frames - 1)
        c.create_line(x, y0, x, y1, fill="#333333", dash=(4, 3), tags="cursor")

    def _on_chart_click(self, event):
        if not self.model:
            return
        x0, _, x1, _ = self._plot_area()
        frac = min(1.0, max(0.0, (event.x - x0) / max(1, (x1 - x0))))
        self.set_frame(round(frac * (self.model.frames - 1)))


def main():
    app = App()
    app.after(50, app.ensure_game_folder)
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        app.after(100, lambda: app.load(sys.argv[1]))
    app.mainloop()


if __name__ == "__main__":
    main()
