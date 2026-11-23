"""The chart of the Battles tab: how the time gap of the selected battle develops.

The background has the colour bands of the gap (red, orange, yellow = close,
grey = the battle goes on), the line is the gap of every pair in the battle,
coloured the same way. Side by side is a thick red bar on the zero line, lead
changes are vertical lines, and the strips below show the phases (dark =
close, light = pause) and the clips. Click or drag to jump, the wheel zooms.
"""
import tkinter as tk
from tkinter import ttk

import analysis
import battles
import chartview

PAD_S = 10.0          # seconds shown before and after the battle


class BattleChart:
    MARGIN = (58, 26, 14, 64)      # left, top, right, bottom

    def __init__(self, parent, app):
        self.app = app
        self.battle = None
        self.v0, self.v1 = 0.0, 1.0
        self._series = []          # (pair, [(frame, |gap|, side)])
        bar = ttk.Frame(parent)
        bar.pack(fill="x")
        self.title = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.title).pack(side="left")
        ttk.Button(bar, text="Whole battle", command=self.reset_zoom).pack(side="right")
        self.canvas = tk.Canvas(parent, background="white", height=200, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        c = self.canvas
        c.bind("<Configure>", lambda e: self.draw())
        c.bind("<Button-1>", self._jump)
        c.bind("<B1-Motion>", self._jump)
        c.bind("<Motion>", self._on_motion)
        c.bind("<Leave>", lambda e: c.delete("hover"))
        c.bind("<MouseWheel>", lambda e: self._wheel(e, 1 if e.delta > 0 else -1))
        c.bind("<Button-4>", lambda e: self._wheel(e, 1))
        c.bind("<Button-5>", lambda e: self._wheel(e, -1))

    @property
    def model(self):
        return self.app.model

    # ------------------------------------------------------------ view
    def show(self, battle):
        """Show a battle (None: empty chart)."""
        self.battle = battle
        self._series = []
        if battle is not None and battle.kind != "start" and self.app.battle_result:
            gaps = self.app.battle_result.gaps
            step = gaps.sample
            for pair in battle.pairs:
                g, s = gaps.gaps[pair], gaps.sides[pair]
                lo = max(0, int((battle.start - 60 / self._dt()) // step))
                hi = min(len(g) - 1, int((battle.end + 60 / self._dt()) // step))
                self._series.append((pair, [(k * step, None if g[k] is None else abs(g[k]), s[k])
                                            for k in range(lo, hi + 1)]))
        self.reset_zoom()

    def _dt(self):
        return self.model.replay.interval_ms / 1000.0

    def reset_zoom(self):
        b = self.battle
        if b is None or not self.model:
            self.v0, self.v1 = 0.0, 1.0
        else:
            pad = PAD_S / self._dt()
            self.v0 = max(0.0, b.start - pad)
            self.v1 = min(float(self.model.frames - 1), b.end + pad)
            if self.v1 - self.v0 < 1:
                self.v1 = self.v0 + 1
        self.draw()

    def _wheel(self, event, direction):
        if self.battle is None:
            return
        x0, _, x1, _ = self._plot()
        frac = min(1.0, max(0.0, (event.x - x0) / max(1, x1 - x0)))
        anchor = self.v0 + frac * (self.v1 - self.v0)
        span = (self.v1 - self.v0) * (0.75 if direction > 0 else 1 / 0.75)
        span = min(max(span, 3 / self._dt()), self.model.frames - 1)
        v0 = min(max(0.0, anchor - frac * span), self.model.frames - 1 - span)
        self.v0, self.v1 = v0, v0 + span
        self.draw()

    def _plot(self):
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        left, top, right, bottom = self.MARGIN
        return left, top, w - right, h - bottom

    def _x(self, frame):
        x0, _, x1, _ = self._plot()
        return x0 + (frame - self.v0) / (self.v1 - self.v0) * (x1 - x0)

    def _frame_at(self, x):
        x0, _, x1, _ = self._plot()
        frac = min(1.0, max(0.0, (x - x0) / max(1, x1 - x0)))
        return int(round(self.v0 + frac * (self.v1 - self.v0)))

    def _limits(self):
        st = self.app.battle_settings
        return st.close_s, st.battle_s, st.battle_s + 0.25

    def _y(self, gap):
        _, y0, _, y1 = self._plot()
        top = self._limits()[2]
        return y1 - (y1 - y0) * min(gap, top) / top

    # ------------------------------------------------------------ drawing
    def draw(self):
        c = self.canvas
        c.delete("all")
        b = self.battle
        x0, y0, x1, y1 = self._plot()
        if not self.model or b is None:
            self.title.set("")
            c.create_text(20, 20, anchor="nw", fill="#777777",
                          text="Select a battle to see how the gap develops.")
            return
        if x1 - x0 < 60 or y1 - y0 < 30:
            return
        names = self.app.replay.cars
        if b.kind == "start":
            self.title.set("Start")
            c.create_text(20, 20, anchor="nw", fill="#777777",
                          text="The start has no gap chart: the whole field drives close together.")
            return
        self.title.set("Time gap: " + " vs ".join(names[i].driver for i in b.cars))
        close_s, battle_s, top = self._limits()
        bands = ((0.0, battles.RED_S, "red"), (battles.RED_S, battles.ORANGE_S, "orange"),
                 (battles.ORANGE_S, close_s, "yellow"), (close_s, battle_s, "grey"))
        for lo, hi, lvl in bands:
            if hi > lo:
                c.create_rectangle(x0, self._y(hi), x1, self._y(lo), fill=battles.LEVEL_LIGHT[lvl], outline="")
        for v in sorted({0.0, battles.RED_S, battles.ORANGE_S, close_s, battle_s}):
            y = self._y(v)
            c.create_line(x0, y, x1, y, fill="#d0d0d0")
            c.create_text(x0 - 6, y, anchor="e", text="%.1f s" % v if v >= 1 or v == 0 else "%.2f s" % v,
                          fill="#555555", font=("TkDefaultFont", 8))
        # battle span
        c.create_rectangle(self._x(b.start), y0, self._x(b.end), y1, outline="#90a4ae", dash=(3, 3))
        self._draw_lines()
        self._draw_lead_changes(y0, y1)
        self._draw_strips(x0, x1, y1)
        self._draw_time_axis(x0, x1, y1)
        self.draw_cursor()

    def _draw_lines(self):
        c = self.canvas
        close_s, battle_s, top = self._limits()
        step = max(1, int((self.v1 - self.v0) / max(1, self._plot()[2] - self._plot()[0]) / 3))
        for _pair, pts in self._series:
            prev = None
            for n in range(0, len(pts), step):
                f, g, side = pts[n]
                if f < self.v0 - 10 or f > self.v1 + 10:
                    prev = None
                    continue
                if g is None or g > top:
                    prev = None
                    continue
                lvl = battles.level(g, side, close_s, battle_s) or "grey"
                x, y = self._x(f), self._y(g)
                if prev is not None:
                    c.create_line(prev[0], prev[1], x, y, fill=battles.LEVEL_COLORS[lvl], width=2)
                if side:
                    c.create_line(x - 1, self._y(0) - 3, x + 2, self._y(0) - 3,
                                  fill=battles.LEVEL_COLORS["red"], width=6)
                prev = (x, y)

    def _draw_lead_changes(self, y0, y1):
        c, labels = self.canvas, self.app.map.labels
        ends = []            # right end of the label on each text row, so labels do not overlap
        for f, w, x in self.battle.lead_changes:
            if not self.v0 <= f <= self.v1:
                continue
            px = self._x(f)
            c.create_line(px, y0, px, y1, fill="#424242", width=1)
            row = next((r for r, end in enumerate(ends) if px > end + 4), len(ends))
            if row == len(ends):
                ends.append(0)
            tid = c.create_text(px + 3, y0 + 2 + 11 * row, anchor="nw", font=("TkDefaultFont", 8, "bold"),
                                fill="#212121",
                                text="%s > %s" % (labels[w] if labels else w, labels[x] if labels else x))
            box = c.bbox(tid)
            c.create_rectangle(box, fill="white", outline="")
            c.tag_raise(tid)
            ends[row] = box[2]

    def _draw_strips(self, x0, x1, y1):
        c, app = self.canvas, self.app
        rows = (("phases", y1 + 22), ("clips", y1 + 36), ("selected", y1 + 50))
        for text, y in rows:
            c.create_text(x0 - 6, y, anchor="e", text=text, fill="#555555", font=("TkDefaultFont", 8))

        def strip(a, b, y, fill, outline):
            if b < self.v0 or a > self.v1:
                return
            c.create_rectangle(max(x0, self._x(a)), y - 5, min(x1, self._x(b)), y + 5, fill=fill, outline=outline)
        for p in self.battle.phases:
            strip(p.start, p.end, rows[0][1], "#616161" if p.kind == "close" else "#d6d6d6", "")
        for a, b in app.clip_ranges():
            strip(a, b, rows[1][1], "#66bb6a", "#2e7d32")
        for a, b in app.selected_battle_clips():
            strip(a, b, rows[2][1], "#64b5f6", "#1565c0")

    def _draw_time_axis(self, x0, x1, y1):
        c = self.canvas
        dt = self._dt()
        span_s = (self.v1 - self.v0) * dt
        step = next((s for s in chartview.TIME_STEPS if span_s / s <= 8), chartview.TIME_STEPS[-1])
        t = int(self.v0 * dt // step) * step
        while t <= self.v1 * dt:
            f = t / dt
            if f >= self.v0:
                x = self._x(f)
                c.create_text(x, y1 + 8, text=analysis.fmt_clock(t), fill="#555555", font=("TkDefaultFont", 8))
            t += step

    def draw_cursor(self):
        c = self.canvas
        c.delete("cursor")
        if not self.model or self.battle is None or self.battle.kind == "start":
            return
        f = self.app.frame
        if not self.v0 <= f <= self.v1:
            return
        x0, y0, x1, y1 = self._plot()
        x = self._x(f)
        c.create_line(x, y0, x, y1, fill="#222222", dash=(4, 3), tags="cursor")

    def follow(self):
        """Keep the cursor in view when the time moves outside a zoomed chart."""
        if self.battle is None or not self.model:
            return
        f = self.app.frame
        span = self.v1 - self.v0
        if not self.v0 <= f <= self.v1 and span < self.model.frames - 1:
            v0 = min(max(0.0, f - span / 2), self.model.frames - 1 - span)
            self.v0, self.v1 = v0, v0 + span
            self.draw()
        else:
            self.draw_cursor()

    # ------------------------------------------------------------ mouse
    def _jump(self, event):
        if self.battle is not None and self.model and self.battle.kind != "start":
            self.app.set_frame(self._frame_at(event.x))

    def _on_motion(self, event):
        c = self.canvas
        c.delete("hover")
        if self.battle is None or not self._series:
            return
        x0, y0, x1, y1 = self._plot()
        if not (x0 <= event.x <= x1 and y0 <= event.y <= y1):
            return
        f = self._frame_at(event.x)
        cars = self.app.replay.cars
        best = None
        for (a, b), pts in self._series:
            k = min(range(len(pts)), key=lambda n: abs(pts[n][0] - f))
            g = pts[k][1]
            if g is None:
                continue
            d = abs(self._y(g) - event.y)
            if d <= 12 and (best is None or d < best[0]):
                best = (d, a, b, g, pts[k][2])
        if best is None:
            return
        _, a, b, g, side = best
        text = "%s - %s  %.2f s%s  (%s)" % (cars[a].driver, cars[b].driver, g, "  side by side" if side else "",
                                           analysis.fmt_clock(f * self._dt()))
        c.create_text(event.x + 10, event.y - 12, anchor="w", text=text, tags="hover",
                      font=("TkDefaultFont", 9, "bold"), fill="#111111")
