"""Top-down map of the track with every car at the shown frame.

World x runs to the right and world z up the screen (a plain top-down view).
The track is drawn once per zoom/size (layer "track"), the cars on every frame
(layer "cars"), so stepping through frames stays fast.

Track shape, best source first:
  1. tarmac measured from the track model (road/kerb meshes) along the AI line
  2. the AI racing line as a wide line (approximate width)
  3. the paths the cars drove (dots)
"""
import math

import analysis
import surface

ASPHALT = "#6b6b6b"
GRASS = "#e9efe3"
MARGIN_PX = 18
TRAIL_S = 4
AHEAD_S = 3


class MapView:
    def __init__(self, canvas, colors, name_of=None):
        self.c = canvas
        self.colors = colors
        self.model = None
        self.shape = None                 # ("surface", quads) | ("line", pts) | ("dots", pts)
        self.bounds = None
        self.view = None                  # (centre_x, centre_z, pixels per metre)
        self.fit_view = None
        self._drag = None
        self.user_moved = False           # zoomed/panned by hand: do not refit on resize
        self._size_seen = None
        self.trail_s = TRAIL_S
        self.ahead_s = AHEAD_S
        self.units = analysis.METRIC
        self.initials = True
        self.labels = []
        canvas.bind("<Configure>", self._on_configure)
        canvas.bind("<MouseWheel>", self._on_wheel)
        canvas.bind("<Button-4>", lambda e: self._zoom(e.x, e.y, 1.25))
        canvas.bind("<Button-5>", lambda e: self._zoom(e.x, e.y, 0.8))
        canvas.bind("<ButtonPress-1>", self._on_press)
        canvas.bind("<B1-Motion>", self._on_drag)
        canvas.bind("<Double-Button-1>", lambda e: self.reset_view())

    # ------------------------------------------------------------ model
    def set_model(self, model, roadmap=None):
        self.model = model
        self.labels = car_labels(model.replay) if model else []
        self.shape = None
        self.bounds = None
        self.view = self.fit_view = None
        if model is None:
            self.redraw()
            return
        line = model.line
        if line is not None and roadmap is not None:
            left, right = surface.track_edges(roadmap, line)
            quads = []
            n = line.n
            last = n if line.closed else n - 1
            for i in range(last):
                j = (i + 1) % n
                quads.append(_quad(line, i, j, left, right))
            self.shape = ("surface", quads)
            xs = [p for q in quads for p in q[0::2]]
            zs = [p for q in quads for p in q[1::2]]
        elif line is not None:
            pts = [(line.xs[i], line.zs[i]) for i in range(line.n)]
            if line.closed:
                pts.append(pts[0])
            self.shape = ("line", pts)
            xs, zs = [p[0] for p in pts], [p[1] for p in pts]
        else:
            pts = []
            for car in model.replay.cars:
                if car.has_positions:
                    pts.extend((p[0], p[2]) for p in car.positions[::15] if p != (0.0, 0.0, 0.0))
            self.shape = ("dots", pts)
            xs, zs = [p[0] for p in pts], [p[1] for p in pts]
        if xs:
            self.bounds = (min(xs), max(xs), min(zs), max(zs))
        self.redraw()

    # ------------------------------------------------------------ view
    def _size(self):
        return max(1, self.c.winfo_width()), max(1, self.c.winfo_height())

    def _fit(self):
        if not self.bounds:
            return None
        x0, x1, z0, z1 = self.bounds
        w, h = self._size()
        scale = min((w - 2 * MARGIN_PX) / max(x1 - x0, 1.0), (h - 2 * MARGIN_PX) / max(z1 - z0, 1.0))
        return ((x0 + x1) / 2, (z0 + z1) / 2, max(scale, 1e-4))

    def _on_configure(self, _event=None):
        size = self._size()
        if size != self._size_seen:
            self._size_seen = size
            if not self.user_moved and self.bounds:
                self.view = self.fit_view = self._fit()
        self.redraw()

    def reset_view(self):
        self.user_moved = False
        self.view = self.fit_view = self._fit()
        self.redraw()

    def _to_screen(self, x, z):
        cx, cz, s = self.view
        w, h = self._size()
        return w / 2 + (x - cx) * s, h / 2 - (z - cz) * s

    def _to_world(self, px, py):
        cx, cz, s = self.view
        w, h = self._size()
        return cx + (px - w / 2) / s, cz - (py - h / 2) / s

    def _zoom(self, px, py, factor):
        if not self.view:
            return
        wx, wz = self._to_world(px, py)
        cx, cz, s = self.view
        s2 = min(max(s * factor, self.fit_view[2] * 0.5), self.fit_view[2] * 60)
        w, h = self._size()
        self.view = (wx - (px - w / 2) / s2, wz + (py - h / 2) / s2, s2)
        self.user_moved = True
        self.redraw()

    def _on_wheel(self, event):
        self._zoom(event.x, event.y, 1.25 if event.delta > 0 else 0.8)

    def _on_press(self, event):
        self._drag = (event.x, event.y)

    def _on_drag(self, event):
        if not self.view or not self._drag:
            return
        dx, dy = event.x - self._drag[0], event.y - self._drag[1]
        self._drag = (event.x, event.y)
        cx, cz, s = self.view
        self.view = (cx - dx / s, cz + dy / s, s)
        self.user_moved = True
        self.redraw()

    # ------------------------------------------------------------ drawing
    def redraw(self):
        self.c.delete("all")
        if self.model is None or not self.bounds:
            self.c.create_text(12, 12, anchor="nw", fill="#777777", text="Open a replay to see the track map.")
            return
        if self.view is None:
            self.view = self.fit_view = self._fit()
        self._draw_track()
        self.draw_cars()

    frame = 0
    selected = None       # car index of the driver chosen in the filter
    highlight = ()        # car indexes of the selected overtake (overtaker, passed)

    def _draw_track(self):
        kind, data = self.shape
        c, to = self.c, self._to_screen
        if kind == "surface":
            for q in data:
                pts = []
                for k in range(0, 8, 2):
                    pts.extend(to(q[k], q[k + 1]))
                c.create_polygon(*pts, fill=ASPHALT, outline=ASPHALT, tags="track")
        elif kind == "line":
            pts = []
            for x, z in data:
                pts.extend(to(x, z))
            width = max(3, int(self.view[2] * 9))
            c.create_line(*pts, fill=ASPHALT, width=width, capstyle="round", joinstyle="round", tags="track")
        else:
            for x, z in data:
                px, py = to(x, z)
                c.create_rectangle(px, py, px + 1.5, py + 1.5, fill="#9a9a9a", outline="", tags="track")

    def draw_cars(self):
        """Cars at self.frame; the app sets frame, selected and highlight first."""
        c = self.c
        c.delete("cars")
        if self.model is None or not self.view:
            return
        model, replay = self.model, self.model.replay
        f = max(0, min(self.frame, model.frames - 1))
        if self._recentre(f):                  # view jumped: the track layer must follow
            self.redraw()
            return
        many = len(model.active) > 12
        focus = set(self.highlight) | ({self.selected} if self.selected is not None else set())
        dim = bool(self.highlight)
        step_s = replay.interval_ms / 1000.0
        trail_n = int(self.trail_s / step_s)
        ahead_n = int(self.ahead_s / step_s)
        order = sorted(model.active, key=lambda i: i in focus)       # focus cars on top
        screen = {}
        for i in model.active:
            p = replay.cars[i].positions[f]
            if p != (0.0, 0.0, 0.0):
                screen[i] = self._to_screen(p[0], p[2])
        self._draw_gap_line(f, screen)
        for i in order:
            car = replay.cars[i]
            pos = car.positions[f]
            if pos == (0.0, 0.0, 0.0):
                continue
            col = self.colors[i % len(self.colors)]
            if i in focus:
                trail = []
                for g in range(max(0, f - trail_n), f + 1, 6):
                    trail.extend(self._to_screen(car.positions[g][0], car.positions[g][2]))
                if len(trail) >= 4:
                    c.create_line(*trail, fill=col, width=2, tags="cars")
                if ahead_n > 0 and f < model.frames - 1:
                    self._draw_ahead(car, f, ahead_n, step_s, col, labels=(not self.highlight or i == self.highlight[0]))
            px, py = screen[i]
            r = 7 if i in focus else 5
            fill = col if (i in focus or not dim) else _fade(col, 0.6)
            c.create_oval(px - r, py - r, px + r, py + r, fill=fill, outline="#111111" if i in focus else "white",
                          width=2 if i in focus else 1, tags="cars")
            alone = all(j == i or abs(screen[j][0] - px) > 70 or abs(screen[j][1] - py) > 14 for j in screen)
            if i in focus or (not many and alone and not self.initials):
                name = car.driver if len(car.driver) < 16 else car.driver[:14] + ".."
                c.create_text(px + r + 3, py, anchor="w", text=name, font=("TkDefaultFont", 8), tags="cars")
            elif self.initials:
                c.create_text(px + r + 2, py, anchor="w", text=self.labels[i] if self.labels else initials(car.driver),
                              font=("TkDefaultFont", 7), fill="#333333", tags="cars")
        t = model.time_s(f)
        c.create_text(8, 6, anchor="nw", fill="#444444", font=("TkDefaultFont", 9),
                      text="Replay time %s" % _clock(t), tags="cars")

    def _recentre(self, f):
        """Zoomed in and the focus cars left the picture: move the view onto them.

        Jumps by a whole screen (centred on the cars) rather than creeping, so there is
        always some track to see. Returns True when the view changed."""
        if not self.user_moved or not self.view:
            return False
        focus = list(self.highlight) or ([self.selected] if self.selected is not None else [])
        pts = []
        for i in focus:
            p = self.model.replay.cars[i].positions[f]
            if p != (0.0, 0.0, 0.0):
                pts.append((p[0], p[2]))
        if not pts:
            return False
        w, h = self._size()
        pad = 30
        def inside(x, z):
            px, py = self._to_screen(x, z)
            return pad <= px <= w - pad and pad <= py <= h - pad
        if any(inside(x, z) for x, z in pts):
            return False
        cx = sum(p[0] for p in pts) / len(pts)
        cz = sum(p[1] for p in pts) / len(pts)
        s = self.view[2]
        # both cars on screen after centring? otherwise centre on the first (overtaker)
        if not all(abs(x - cx) * s < w / 2 - pad and abs(z - cz) * s < h / 2 - pad for x, z in pts):
            cx, cz = pts[0]
        self.view = (cx, cz, s)
        return True

    def _draw_ahead(self, car, f, ahead_n, step_s, col, labels=True):
        """Dashed line along where the car goes next, with a dot every second."""
        c = self.c
        last = min(self.model.frames - 1, f + ahead_n)
        pts = []
        for g in range(f, last + 1, 3):
            pts.extend(self._to_screen(car.positions[g][0], car.positions[g][2]))
        if last % 3:
            pts.extend(self._to_screen(car.positions[last][0], car.positions[last][2]))
        if len(pts) >= 4:
            c.create_line(*pts, fill=col, width=2, dash=(5, 4), tags="cars")
        per_s = max(1, int(round(1.0 / step_s)))
        for k in range(per_s, ahead_n + 1, per_s):
            g = f + k
            if g > last:
                break
            px, py = self._to_screen(car.positions[g][0], car.positions[g][2])
            c.create_oval(px - 3, py - 3, px + 3, py + 3, fill="white", outline=col, width=2, tags="cars")
            if labels:
                c.create_text(px + 5, py - 6, anchor="w", text="+%ds" % (k // per_s),
                              font=("TkDefaultFont", 7), fill="#444444", tags="cars")

    ZOOM_WIDTH_M = {"full": 120.0, "half": 450.0}

    def zoom_to(self, cars, frame, metres=None, level="full"):
        """Centre the map on `cars` at `frame`, showing a fixed stretch of track across the
        smaller side of the map (+ the distance between the cars): "full" about 120 m,
        "half" about 450 m, "off" leaves the view alone."""
        if level == "off" or not self.view or not self.fit_view:
            return
        pts = [self.model.replay.cars[i].positions[frame] for i in cars]
        pts = [p for p in pts if p != (0.0, 0.0, 0.0)]
        if not pts:
            return
        cx = sum(p[0] for p in pts) / len(pts)
        cz = sum(p[2] for p in pts) / len(pts)
        w, h = self._size()
        spread = max([math.hypot(p[0] - cx, p[2] - cz) for p in pts] + [0.0]) * 2
        width = metres if metres else self.ZOOM_WIDTH_M.get(level, 120.0)
        scale = min(w, h) / (spread + width)
        # never zoomed out further than the whole track
        scale = max(scale, self.fit_view[2])
        self.view = (cx, cz, scale)
        self.user_moved = True
        self.redraw()

    def _draw_gap_line(self, f, screen):
        """Line between the two cars of the selected overtake, labelled with the gap."""
        if len(self.highlight) != 2:
            return
        a, b = self.highlight
        if a not in screen or b not in screen:
            return
        model = self.model
        if model.progress[a] is None or model.progress[b] is None:
            return
        gap = abs(model.progress[a][f] - model.progress[b][f])
        short = analysis.UNIT_NAMES[self.units][0]
        (x1, y1), (x2, y2) = screen[a], screen[b]
        self.c.create_line(x1, y1, x2, y2, fill="#222222", width=1, dash=(2, 3), tags="cars")
        self.c.create_text((x1 + x2) / 2 + 6, (y1 + y2) / 2 - 8, anchor="w", font=("TkDefaultFont", 8),
                           fill="#222222", text="%.0f %s" % (analysis.short_length(gap, self.units), short),
                           tags="cars")


def car_labels(replay):
    """Short label per car index: initials, with a number when several cars share them."""
    base = [initials(c.driver or "?") for c in replay.cars]
    count = {}
    for b in base:
        count[b] = count.get(b, 0) + 1
    seen = {}
    out = []
    for b in base:
        if count[b] > 1:
            seen[b] = seen.get(b, 0) + 1
            out.append("%s%d" % (b, seen[b]))
        else:
            out.append(b)
    return out


def initials(name):
    """First letter of every word of a name ("Bas Leinders" -> "BL"); symbols are skipped."""
    letters = [w[0].upper() for w in name.replace("-", " ").replace("_", " ").split() if w[0].isalnum()]
    return "".join(letters) or "?"


def _quad(line, i, j, left, right):
    """Corners (x, z) of the tarmac between line points i and j."""
    pts = []
    for k, sign in ((i, 1.0), (j, 1.0), (j, -1.0), (i, -1.0)):
        a = (k - 1) % line.n if line.closed else max(k - 1, 0)
        b = (k + 1) % line.n if line.closed else min(k + 1, line.n - 1)
        dx, dz = line.xs[b] - line.xs[a], line.zs[b] - line.zs[a]
        d = math.hypot(dx, dz) or 1.0
        nx, nz = -dz / d, dx / d
        dist = left[k] if sign > 0 else right[k]
        pts.extend((line.xs[k] + sign * nx * dist, line.zs[k] + sign * nz * dist))
    return pts


def _fade(color, amount):
    """Blend a #rrggbb colour towards white."""
    r, g, b = (int(color[k:k + 2], 16) for k in (1, 3, 5))
    r, g, b = (int(v + (255 - v) * amount) for v in (r, g, b))
    return "#%02x%02x%02x" % (r, g, b)


def _clock(seconds):
    tenths = int(round(seconds * 10))
    minutes, rest = divmod(tenths, 600)
    return "%d:%04.1f" % (minutes, rest / 10)
