"""AC Overtake Finder - race overview screen.

Run:  python app.py [path\to\replay.acreplay]

Needs only Python 3.9+ with Tkinter (included in the standard Windows installer).
"""
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import acreplay
import analysis

COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd",
          "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#7f7f7f"]


def default_replay_dir():
    docs = os.path.join(os.path.expanduser("~"), "Documents",
                        "Assetto Corsa", "replay")
    return docs if os.path.isdir(docs) else os.path.expanduser("~")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AC Overtake Finder - Race overview")
        self.geometry("1000x820")
        self.minsize(820, 640)
        self.replay = None
        self.model = None
        self.gap_series = []
        self.frame = 0
        self._build()

    # ------------------------------------------------------------------ UI
    def _build(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="Open replay...", command=self.open_dialog).pack(side="left")
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

        scols = ("pos", "driver", "ahead", "leader", "dist")
        self.stand_tree = ttk.Treeview(stand, columns=scols, show="headings", height=7)
        for key, text, width in (("pos", "Pos", 40), ("driver", "Driver", 230),
                                 ("ahead", "Gap to car ahead (m)", 160),
                                 ("leader", "Gap to leader (m)", 150),
                                 ("dist", "Distance (m)", 110)):
            self.stand_tree.heading(key, text=text)
            self.stand_tree.column(key, width=width, anchor="w" if key == "driver" else "center")
        self.stand_tree.pack(fill="both", expand=True)
        paned.add(stand, weight=2)

        # Gap chart
        chart = ttk.Labelframe(paned, text="Gap to leader over the race (click to jump)", padding=4)
        self.canvas = tk.Canvas(chart, background="white", height=220, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.draw_chart())
        self.canvas.bind("<Button-1>", self._on_chart_click)
        self.canvas.bind("<B1-Motion>", self._on_chart_click)
        paned.add(chart, weight=2)

        ttk.Label(self, padding=(8, 0, 8, 6), foreground="#666666",
                  text="Gaps are measured along the distance each car has driven, corrected "
                       "for the starting grid. Positions are shown by replay frame number.").pack(fill="x")

        self.bind("<Left>", lambda e: self.step(-10))
        self.bind("<Right>", lambda e: self.step(10))

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
            model = analysis.RaceModel(replay)
            series = model.gap_to_leader_series()
        except (acreplay.ReplayError, OSError) as exc:
            self.path_var.set("No replay loaded")
            messagebox.showerror("Cannot open replay", str(exc))
            return
        finally:
            self.config(cursor="")
        self.replay, self.model, self.gap_series = replay, model, series
        self.path_var.set(path)
        layout = replay.layout or "-"
        self.info_var.set("Track: %s (layout %s)   Weather: %s   Cars: %d   Frames: %d   Format v%d"
                          % (replay.track, layout, replay.weather, len(replay.cars),
                             replay.frame_count, replay.version))
        self.fill_drivers()
        self.scale.configure(to=replay.frame_count - 1)
        self.set_frame(0)
        self.draw_chart()

    def fill_drivers(self):
        tree = self.drivers_tree
        tree.delete(*tree.get_children())
        for pos, car in enumerate(analysis.classification(self.replay), start=1):
            km = self.model.progress[car.index][-1] / 1000.0
            times = "  |  ".join(analysis.fmt_lap(ms) for _, ms in car.laps) or "-"
            tree.insert("", "end", values=(pos, car.driver, car.nation, car.team or car.skin,
                                           len(car.laps), analysis.fmt_lap(car.best_lap_ms),
                                           times, "%.2f" % km))

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
        self.frame_var.set("Frame %d / %d" % (frame, self.model.frames - 1))
        tree = self.stand_tree
        tree.delete(*tree.get_children())
        for s in self.model.standings(frame):
            tree.insert("", "end", values=(s.position, s.driver,
                                           "-" if s.position == 1 else "%.1f" % s.gap_ahead_m,
                                           "-" if s.position == 1 else "%.1f" % s.gap_leader_m,
                                           "%.0f" % s.progress_m))
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
        top = max(max(s) for s in self.gap_series) or 1.0
        step = 50 if top <= 300 else 100 if top <= 800 else 250
        ymax = (int(top / step) + 1) * step
        for v in range(0, ymax + 1, step):
            y = y0 + (y1 - y0) * v / ymax
            c.create_line(x0, y, x1, y, fill="#e6e6e6")
            c.create_text(x0 - 6, y, anchor="e", text="%d m" % v, fill="#555555", font=("TkDefaultFont", 8))
        stride = max(1, n // max(1, (x1 - x0)))
        for i, series in enumerate(self.gap_series):
            pts = []
            for f in range(0, n, stride):
                pts.extend((x0 + (x1 - x0) * f / (n - 1), y0 + (y1 - y0) * series[f] / ymax))
            c.create_line(*pts, fill=COLORS[i % len(COLORS)], width=2)
        c.create_text(x0, y1 + 12, anchor="w", text="frame 0", fill="#555555", font=("TkDefaultFont", 8))
        c.create_text(x1, y1 + 12, anchor="e", text="frame %d" % (n - 1), fill="#555555", font=("TkDefaultFont", 8))
        lx = x0
        for i, car in enumerate(self.replay.cars):
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
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        app.after(100, lambda: app.load(sys.argv[1]))
    app.mainloop()


if __name__ == "__main__":
    main()
