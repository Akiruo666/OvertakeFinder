"""The chart under the tables: positions, or gaps, over the replay time.

Modes
  positions  race position of every car over time (1 = top). Lines that cross are
             overtakes; the overtakes themselves are drawn on it.
  leader     gap to the leader.
  driver     gap to the driver chosen in the overtakes list (+ = car is ahead).

The mouse wheel zooms around the pointer, the scroll bar below moves through the
zoomed replay, and the strip at the bottom shows the clips of ticked overtakes.
"""
import bisect
import tkinter as tk
from tkinter import ttk

import analysis
import clips

POSITIONS, LEADER, DRIVER = "positions", "leader", "driver"
MODES = ((POSITIONS, "Positions"), (LEADER, "Gap to leader"), (DRIVER, "Gap to selected driver"))
TIME_STEPS = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600)
MIN_WINDOW = 100          # frames (3 s)
DRIVER_RANGE_M = 150.0    # gaps beyond this are not drawn in "driver" mode
ZOOM_AROUND_S = 60


class ChartView:
    MARGIN = (58, 28, 14, 44)      # left, top, right, bottom

    def __init__(self, parent, app, colors):
        self.app = app
        self.colors = colors
        self.v0, self.v1 = 0.0, 1.0
        self._markers = []
        self._y_of = None
        bar = ttk.Frame(parent)
        bar.pack(fill="x")
        self.mode_var = tk.StringVar(value=POSITIONS)
        for value, text in MODES:
            ttk.Radiobutton(bar, text=text, variable=self.mode_var, value=value,
                            command=self.draw).pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="Reset zoom", command=self.reset_zoom).pack(side="right")
        ttk.Button(bar, text="Zoom %d s around cursor" % (2 * ZOOM_AROUND_S),
                   command=self.zoom_to_cursor).pack(side="right", padx=6)
        self.canvas = tk.Canvas(parent, background="white", height=240, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.scroll = ttk.Scrollbar(parent, orient="horizontal", command=self._on_scrollbar)
        self.scroll.pack(fill="x")
        c = self.canvas
        c.bind("<Configure>", lambda e: self.draw())
        c.bind("<Button-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<Motion>", self._on_motion)
        c.bind("<Leave>", lambda e: c.delete("hover"))
        c.bind("<MouseWheel>", lambda e: self._wheel(e, 1 if e.delta > 0 else -1))
        c.bind("<Button-4>", lambda e: self._wheel(e, 1))
        c.bind("<Button-5>", lambda e: self._wheel(e, -1))

    @property
    def model(self):
        return self.app.model

    # ------------------------------------------------------------- view
    def set_model(self):
        self.reset_zoom()

    def reset_zoom(self):
        self.v0, self.v1 = 0.0, float(max(1, self.model.frames - 1)) if self.model else 1.0
        self.draw()

    def zoom_to_cursor(self):
        if self.model:
            self.zoom_around(self.app.frame, ZOOM_AROUND_S)

    def zoom_around(self, frame, seconds):
        half = seconds / (self.model.replay.interval_ms / 1000.0)
        self._set_view(frame - half, frame + half)

    def _set_view(self, v0, v1):
        total = self.model.frames - 1
        span = min(max(v1 - v0, MIN_WINDOW), total)
        v0 = min(max(v0, 0.0), total - span)
        self.v0, self.v1 = v0, v0 + span
        self.draw()

    def _wheel(self, event, direction):
        if not self.model:
            return
        x0, _, x1, _ = self._plot()
        frac = min(1.0, max(0.0, (event.x - x0) / max(1, x1 - x0)))
        anchor = self.v0 + frac * (self.v1 - self.v0)
        factor = 0.75 if direction > 0 else 1 / 0.75
        span = (self.v1 - self.v0) * factor
        self._set_view(anchor - frac * span, anchor + (1 - frac) * span)

    def _on_scrollbar(self, action, *args):
        if not self.model:
            return
        span, total = self.v1 - self.v0, self.model.frames - 1
        if action == "moveto":
            self._set_view(float(args[0]) * total, float(args[0]) * total + span)
        elif action == "scroll":
            step = float(args[0]) * (span * (0.9 if args[1] == "pages" else 0.1))
            self._set_view(self.v0 + step, self.v1 + step)

    def _plot(self):
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        l, t, r, b = self.MARGIN
        return l, t, w - r, h - b

    def _x(self, frame):
        x0, _, x1, _ = self._plot()
        return x0 + (frame - self.v0) / (self.v1 - self.v0) * (x1 - x0)

    def _frame_at(self, x):
        x0, _, x1, _ = self._plot()
        frac = min(1.0, max(0.0, (x - x0) / max(1, x1 - x0)))
        return int(round(self.v0 + frac * (self.v1 - self.v0)))

    # ------------------------------------------------------------- data
    def _raw(self, frame):
        """{car: value} at a frame for the current mode (None = not drawn)."""
        m = self.model
        mode = self.mode_var.get()
        if mode == POSITIONS:
            frames, ranks = m.rank_series()
            stride = frames[1] - frames[0] if len(frames) > 1 else 1
            idx = min(len(frames) - 1, frame // stride)
            return {i: ranks[i][idx] for i in m.active}
        if mode == LEADER:
            lead = max(m.progress[i][frame] for i in m.active)
            return {i: lead - m.progress[i][frame] for i in m.active}
        ref = self.app.selected_car()
        if ref is None:
            return {}
        base = m.progress[ref][frame]
        return {i: m.progress[i][frame] - base for i in m.active if i != ref}

    def _setup_axis(self, mode, y0, y1, f0, f1):
        """Value -> y mapping and gridlines for the mode; returns (mapper, grid)."""
        m, units = self.model, self.app.units
        scale = analysis.short_length(1.0, units)
        unit = analysis.UNIT_NAMES[units][0]
        if mode == POSITIONS:
            n = max(1, len(m.active))
            row = (y1 - y0) / n
            step = 1 if n <= 12 else 5
            grid = [(y0 + (r - 0.5) * row, "P%d" % r) for r in range(1, n + 1) if r == 1 or r % step == 0]
            return (lambda v: y0 + (v - 0.5) * row), grid
        if mode == LEADER:
            top = 1.0
            for f in range(int(f0), int(f1) + 1, max(1, int(f1 - f0) // 400)):
                lead = max(m.progress[i][f] for i in m.active)
                top = max(top, max(lead - m.progress[i][f] for i in m.active) * scale)
            step = _nice(top)
            ymax = (int(top / step) + 1) * step
            grid = [(y0 + (y1 - y0) * v / ymax, "%d %s" % (v, unit)) for v in range(0, int(ymax) + 1, step)]
            return (lambda v: y0 + (y1 - y0) * v * scale / ymax), grid
        rng = DRIVER_RANGE_M * scale
        step = _nice(rng / 2)
        mid, half = (y0 + y1) / 2, (y1 - y0) / 2
        ticks = list(range(-int(rng // step) * step, int(rng // step) * step + 1, step))
        grid = [(mid - half * v / rng, ("+%d" % v if v > 0 else "%d" % v) + " " + unit) for v in ticks]
        return (lambda v: mid - half * v * scale / rng), grid

    # ------------------------------------------------------------- drawing
    def draw(self):
        c = self.canvas
        c.delete("all")
        self._markers = []
        m = self.model
        if not m:
            c.create_text(20, 20, anchor="nw", text="Open a replay to see the race chart.")
            return
        x0, y0, x1, y1 = self._plot()
        if x1 - x0 < 60 or y1 - y0 < 40:
            return
        mode = self.mode_var.get()
        total = m.frames - 1
        self.scroll.set(self.v0 / total, self.v1 / total)
        y_of, grid = self._setup_axis(mode, y0, y1, self.v0, self.v1)
        self._y_of = y_of
        for y, label in grid:
            c.create_line(x0, y, x1, y, fill="#ececec")
            c.create_text(x0 - 6, y, anchor="e", text=label, fill="#555555", font=("TkDefaultFont", 8))
        self._draw_time_axis(x0, y1, x1)
        ref = self.app.selected_car()
        if mode == DRIVER and ref is None:
            c.create_text((x0 + x1) / 2, (y0 + y1) / 2, text="Choose a driver in the overtakes list "
                          "to see the gaps to him.", fill="#777777")
        else:
            self._draw_lines(mode, ref, y_of, x0, x1, y0, y1)
            self._draw_markers(mode, ref, y_of)
        self._draw_clips(x0, x1, y1)
        self.draw_cursor()

    def _draw_lines(self, mode, ref, y_of, x0, x1, y0, y1):
        c, m = self.canvas, self.model
        step = max(1, int((self.v1 - self.v0) / max(1, x1 - x0)))
        if mode == POSITIONS:
            step = max(step, m.rank_series()[0][1] - m.rank_series()[0][0])
        first = max(0, int(self.v0) - step)
        last = min(m.frames - 1, int(self.v1) + step)
        frames = list(range(first, last + 1, step))
        samples = [self._raw(f) for f in frames]
        shown = []
        for i in m.active:
            segs, cur = [], []
            for f, s in zip(frames, samples):
                v = s.get(i)
                if v is None or (mode == DRIVER and abs(v) > DRIVER_RANGE_M):
                    if len(cur) >= 4:
                        segs.append(cur)
                    cur = []
                    continue
                cur.extend((self._x(f), y_of(v)))
            if len(cur) >= 4:
                segs.append(cur)
            if segs:
                shown.append((i, segs))
        for i, segs in sorted(shown, key=lambda s: s[0] == ref):
            col = self.colors[i % len(self.colors)]
            focus = ref is not None and i == ref
            if ref is not None and not focus and mode != DRIVER:
                col = _fade(col, 0.55)
            for seg in segs:
                c = self.canvas
                c.create_line(*seg, fill=col, width=3 if focus else 2)
        if mode == DRIVER and ref is not None:
            y = y_of(0)
            c.create_line(x0, y, x1, y, fill="#333333", dash=(3, 3))
            c.create_text(x1, y - 8, anchor="e", fill="#333333", font=("TkDefaultFont", 8),
                          text=m.replay.cars[ref].driver)
        names = [i for i, _ in shown]
        if len(names) <= 12:
            lx = x0
            for i in names:
                col = self.colors[i % len(self.colors)]
                c.create_rectangle(lx, 8, lx + 10, 18, fill=col, outline="")
                name = m.replay.cars[i].driver
                tid = c.create_text(lx + 14, 13, anchor="w", text=name if len(name) < 18 else name[:16] + "..",
                                    font=("TkDefaultFont", 8))
                lx = c.bbox(tid)[2] + 12
        else:
            c.create_text(x0, 13, anchor="w", font=("TkDefaultFont", 8), fill="#555555",
                          text="%d cars: point at a line to see who it is" % len(names))

    def _draw_markers(self, mode, ref, y_of):
        if mode == LEADER:
            return
        c, app, m = self.canvas, self.app, self.model
        frames, ranks = m.rank_series()
        stride = frames[1] - frames[0] if len(frames) > 1 else 1
        for idx, o in enumerate(app.overtakes):
            if not (self.v0 <= o.frame <= self.v1) or not app._overtake_visible(o):
                continue
            if mode == POSITIONS:
                k = min(len(frames) - 1, o.frame // stride)
                y = y_of(ranks[o.overtaker][k])
            else:
                if ref not in (o.overtaker, o.passed):
                    continue
                y = y_of(0.0)
            x = self._x(o.frame)
            ticked = idx in app.ticked
            flagged = bool(o.flags)
            outline = "#e65100" if flagged else "#444444"
            fill = ("#2e7d32" if not flagged else "#ef6c00") if ticked else "white"
            r = 5
            if idx == app.selected_overtake:
                c.create_oval(x - r - 4, y - r - 4, x + r + 4, y + r + 4, outline="#d50000", width=2)
            if flagged:
                c.create_polygon(x, y - r - 1, x + r + 1, y, x, y + r + 1, x - r - 1, y,
                                 fill=fill, outline=outline, width=2)
            else:
                c.create_oval(x - r, y - r, x + r, y + r, fill=fill, outline=outline, width=2)
            self._markers.append((x, y, idx))

    def _draw_time_axis(self, x0, y1, x1):
        c, m = self.canvas, self.model
        dt = m.replay.interval_ms / 1000.0
        span_s = (self.v1 - self.v0) * dt
        step = next((s for s in TIME_STEPS if span_s / s <= 8), TIME_STEPS[-1])
        t = int(self.v0 * dt // step) * step
        while t <= self.v1 * dt:
            f = t / dt
            if f >= self.v0:
                x = self._x(f)
                c.create_line(x, self._plot()[1], x, y1, fill="#f3f3f3")
                c.create_text(x, y1 + 10, text=analysis.fmt_clock(t), fill="#555555", font=("TkDefaultFont", 8))
            t += step

    def _draw_clips(self, x0, x1, y1):
        app, m = self.app, self.model
        c = self.canvas
        c.create_text(x0 - 6, y1 + 29, anchor="e", text="clips", fill="#2e7d32", font=("TkDefaultFont", 8))
        for a, b in app.clip_ranges():
            if b < self.v0 or a > self.v1:
                continue
            c.create_rectangle(max(x0, self._x(a)), y1 + 24, min(x1, self._x(b)), y1 + 34,
                               fill="#66bb6a", outline="#2e7d32")

    def follow(self):
        """Move a zoomed view along when the shown frame leaves it (slider, buttons)."""
        m = self.model
        f = self.app.frame
        span = self.v1 - self.v0
        if m and not (self.v0 <= f <= self.v1) and span < m.frames - 1:
            self._set_view(f - span / 2, f + span / 2)

    def draw_cursor(self):
        c, m = self.canvas, self.model
        c.delete("cursor")
        if not m:
            return
        f = self.app.frame
        if not (self.v0 <= f <= self.v1):
            return
        x0, y0, x1, y1 = self._plot()
        if x1 - x0 < 60:
            return
        x = self._x(f)
        c.create_line(x, y0, x, y1, fill="#222222", dash=(4, 3), tags="cursor")

    # ------------------------------------------------------------- mouse
    def _on_press(self, event):
        if not self.model:
            return
        best = None
        for x, y, idx in self._markers:
            d = abs(x - event.x) + abs(y - event.y)
            if d <= 12 and (best is None or d < best[0]):
                best = (d, idx)
        if best:
            self.app.select_overtake(best[1])
        else:
            self.app.set_frame(self._frame_at(event.x))

    def _on_drag(self, event):
        if self.model:
            self.app.set_frame(self._frame_at(event.x))

    def _on_motion(self, event):
        c = self.canvas
        c.delete("hover")
        if not self.model or self._y_of is None:
            return
        x0, y0, x1, y1 = self._plot()
        if not (x0 <= event.x <= x1 and y0 <= event.y <= y1):
            return
        f = self._frame_at(event.x)
        best = None
        for i, v in self._raw(f).items():
            if v is None:
                continue
            d = abs(self._y_of(v) - event.y)
            if d <= 10 and (best is None or d < best[0]):
                best = (d, i, v)
        if not best:
            return
        _, i, v = best
        name = self.model.replay.cars[i].driver
        mode = self.mode_var.get()
        unit = analysis.UNIT_NAMES[self.app.units][0]
        text = "P%d  %s" % (v, name) if mode == POSITIONS else "%s  %.0f %s" % (
            name, analysis.short_length(v, self.app.units), unit)
        c.create_text(event.x + 10, event.y - 12, anchor="w", text=text, tags="hover",
                      font=("TkDefaultFont", 9, "bold"), fill="#111111")


def _nice(top):
    for step in (1, 2, 5, 10, 20, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000, 20000, 50000):
        if top / step <= 6:
            return step
    return 100000


def _fade(color, amount):
    r, g, b = (int(color[k:k + 2], 16) for k in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(int(v + (255 - v) * amount) for v in (r, g, b))
