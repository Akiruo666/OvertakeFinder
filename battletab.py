"""The Battles tab: cars that drive close together for a while (see battles.py).

A mixin for the App window: it uses the App's time bar (set_frame), its config
helpers and its map options. Every battle is one row that opens into its
phases (close / pause). Ticks are kept per battle and per phase; they follow
the battles when the settings change.
"""
import tkinter as tk
from tkinter import ttk

import analysis
import battles
import mapview

# (field of battles.Settings, label before the box, lowest, highest, step)
SETTING_FIELDS = (
    ("close_s", "Close up to", 0.1, 5.0, 0.1),
    ("battle_s", "s   battle goes on up to", 0.1, 10.0, 0.1),
    ("max_pause_s", "s   longest pause", 0.0, 300.0, 1.0),
    ("min_close_s", "s   least close time", 0.0, 300.0, 1.0),
    ("min_s", "s   smallest gap", 0.0, 2.0, 0.05),
)
SORTS = (("score", "Score"), ("time", "Time"), ("length", "Length"), ("close", "Close time"))
TICK_ON, TICK_OFF = "☑", "☐"
ALL_DRIVERS = "All drivers"


def settings_from(saved):
    """battles.Settings from the saved dict (bad or missing values: default)."""
    st = battles.Settings()
    if isinstance(saved, dict):
        for name, _label, lo, hi, _step in SETTING_FIELDS:
            try:
                setattr(st, name, max(lo, min(hi, float(saved.get(name, getattr(st, name))))))
            except (TypeError, ValueError):
                pass
        st.show_lapping = bool(saved.get("show_lapping", st.show_lapping))
    st.battle_s = max(st.battle_s, st.close_s)
    return st


def _num(value):
    return ("%.2f" % value).rstrip("0").rstrip(".") if value != int(value) else "%d" % value


def _key(kind, cars, start, end):
    return (kind, frozenset(cars), start, end)


def _same(old, new):
    """Is `new` (after a settings change) the same battle/phase as `old`?"""
    if old[0] != new[0] or old[1] != new[1]:
        return False
    both = min(old[3], new[3]) - max(old[2], new[2])
    return both > 0 and both >= 0.5 * min(old[3] - old[2], new[3] - new[2])


class BattlesTab:
    # ------------------------------------------------------------ building
    def _build_battles(self, parent, cfg):
        self.battle_settings = settings_from(cfg.get("battle_settings"))
        self.battle_measured = None
        self.battle_result = None
        self.battle_rows = {}          # iid -> (battle index, phase index or None)
        self.battle_keys = {}          # iid -> key (see _key)
        self.battle_ticked = set()     # keys
        self.selected_battle = None    # (battle index, phase index or None)
        self._battle_sash_set = False

        row1 = ttk.Frame(parent)
        row1.pack(fill="x", pady=(2, 2))
        ttk.Label(row1, text="Driver:").pack(side="left")
        self.bdriver_var = tk.StringVar(value=ALL_DRIVERS)
        self.bdriver_box = ttk.Combobox(row1, textvariable=self.bdriver_var, state="readonly",
                                        values=[ALL_DRIVERS], width=30)
        self.bdriver_box.pack(side="left", padx=6)
        self.bdriver_box.bind("<<ComboboxSelected>>", lambda e: self.fill_battles())
        ttk.Label(row1, text="Sort by:").pack(side="left", padx=(10, 0))
        saved_sort = cfg.get("battle_sort")
        self.bsort_var = tk.StringVar(value=dict(SORTS).get(saved_sort, "Score"))
        sbox = ttk.Combobox(row1, textvariable=self.bsort_var, state="readonly", width=11,
                            values=[label for _k, label in SORTS])
        sbox.pack(side="left", padx=6)
        sbox.bind("<<ComboboxSelected>>", lambda e: self._on_battle_sort())
        ttk.Button(row1, text="Tick all shown", command=lambda: self.tick_shown_battles(True)).pack(side="left", padx=(14, 0))
        ttk.Button(row1, text="Untick all shown", command=lambda: self.tick_shown_battles(False)).pack(side="left", padx=6)
        self.battle_count = tk.StringVar(value="")
        ttk.Label(row1, textvariable=self.battle_count).pack(side="left", padx=10)

        row2 = ttk.Frame(parent)
        row2.pack(fill="x", pady=(0, 2))
        self.bset_vars = {}
        for name, label, lo, hi, step in SETTING_FIELDS:
            ttk.Label(row2, text=label).pack(side="left")
            var = tk.StringVar(value=_num(getattr(self.battle_settings, name)))
            spin = ttk.Spinbox(row2, from_=lo, to=hi, increment=step, width=5, textvariable=var,
                               command=self._on_battle_settings)
            spin.pack(side="left", padx=4)
            spin.bind("<Return>", lambda e: self._on_battle_settings())
            spin.bind("<FocusOut>", lambda e: self._on_battle_settings())
            self.bset_vars[name] = var
        ttk.Label(row2, text="s").pack(side="left")
        self.blap_var = tk.BooleanVar(value=self.battle_settings.show_lapping)
        ttk.Checkbutton(row2, text="Show lapping", variable=self.blap_var,
                        command=self._on_battle_settings).pack(side="left", padx=(12, 0))
        ttk.Button(row2, text="Defaults", command=self.reset_battle_settings).pack(side="left", padx=10)
        self.battle_note = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.battle_note, foreground="#666666").pack(fill="x")

        self.bupper = ttk.Panedwindow(parent, orient="horizontal")
        self.bupper.pack(fill="both", expand=True)
        self._cfg_bsash = cfg.get("sash_bh")

        left = ttk.Frame(self.bupper)
        detail = ttk.Frame(left, padding=(0, 4, 0, 0))
        detail.pack(side="bottom", fill="x")
        buttons = ttk.Frame(detail)
        buttons.pack(fill="x")
        self.best_btn = ttk.Button(buttons, text="Best moment", command=self.battle_best_moment, state="disabled")
        self.best_btn.pack(side="left")
        self.prev_red_btn = ttk.Button(buttons, text="< Previous red", command=lambda: self.battle_red(-1),
                                       state="disabled")
        self.prev_red_btn.pack(side="left", padx=(10, 0))
        self.next_red_btn = ttk.Button(buttons, text="Next red >", command=lambda: self.battle_red(1),
                                       state="disabled")
        self.next_red_btn.pack(side="left", padx=4)
        self.battle_detail = tk.StringVar(value="Select a battle to see its details.")
        ttk.Label(detail, textvariable=self.battle_detail, justify="left", wraplength=620).pack(anchor="w", pady=(4, 0))

        cols = ("tick", "time", "length", "cars", "laps", "close", "gap", "side", "leads", "score", "notes")
        self.b_tree = self._scrolled_tree(left, horizontal=True, columns=cols, show="tree headings",
                                          height=10, selectmode="extended")
        self.b_tree.column("#0", width=30, minwidth=30, stretch=False)
        for key, text, width in (("tick", "Use", 40), ("time", "Replay time", 120), ("length", "Length", 60),
                                 ("cars", "Cars", 260), ("laps", "Lap", 50), ("close", "Close", 60),
                                 ("gap", "Gap min / mean", 110), ("side", "Side by side", 80),
                                 ("leads", "Lead changes", 85), ("score", "Score", 50), ("notes", "Notes", 260)):
            self.b_tree.heading(key, text=text)
            self.b_tree.column(key, width=width, stretch=key in ("cars", "notes"),
                               anchor="w" if key in ("cars", "notes") else "center")
        self.b_tree.tag_configure("start", foreground="#555555")
        self.b_tree.tag_configure("pause", foreground="#777777")
        self.b_tree.bind("<<TreeviewSelect>>", self._on_battle_select)
        self.b_tree.bind("<Button-1>", self._on_battle_click)
        self.b_tree.bind("<space>", self._on_battle_space)
        self.bupper.add(left, weight=5)

        map_box = ttk.Labelframe(self.bupper, text="Track map (wheel = zoom, drag = move, double-click = reset)",
                                 padding=4)
        self.bmap_canvas = tk.Canvas(map_box, background=mapview.GRASS, height=200, width=200, highlightthickness=0)
        self.bmap_canvas.pack(fill="both", expand=True)
        self.battle_map = mapview.MapView(self.bmap_canvas, self.colors_list)
        self.battle_map.trail_s, self.battle_map.ahead_s = self.map.trail_s, self.map.ahead_s
        self.battle_map.initials = self.map.initials
        self.bupper.add(map_box, weight=4)

    def _place_battle_sash(self):
        try:
            w = self.bupper.winfo_width()
            if w < 300:
                self._bsash_tries = getattr(self, "_bsash_tries", 0) + 1
                if self._bsash_tries < 20:
                    self.after(250, self._place_battle_sash)
                return
            f = self._cfg_bsash
            f = f if isinstance(f, (int, float)) and 0.15 < f < 0.9 else 0.55
            self.bupper.sashpos(0, int(w * f))
            self._battle_sash_set = True
        except (tk.TclError, AttributeError, TypeError):
            pass

    def battle_sash_share(self):
        """Share of the width taken by the battles list (None when not placed yet)."""
        try:
            w = self.bupper.winfo_width()
            if self._battle_sash_set and w > 300:
                return round(self.bupper.sashpos(0) / w, 3)
        except (tk.TclError, AttributeError, ZeroDivisionError):
            pass
        return None

    # ------------------------------------------------------------ data
    def battles_loaded(self, measured):
        """A new replay: measurement done by the loader, now find the battles."""
        self.battle_measured = measured
        self.battle_ticked = set()
        self.selected_battle = None
        names = sorted({self.replay.cars[i].driver for i in self.model.active})
        self.bdriver_box.configure(values=[ALL_DRIVERS] + names)
        self.bdriver_var.set(ALL_DRIVERS)
        self.battle_map.share_from(self.map)
        self._find_battles()
        self.fill_battles()

    def _find_battles(self):
        if self.model is None or self.battle_measured is None:
            self.battle_result = None
            return
        self.battle_result = battles.find_battles(self.model, settings=self.battle_settings,
                                                  measured=self.battle_measured)
        notes = list(self.battle_result.notes)
        self.battle_note.set("  ".join(notes) + ("  " if notes else "") +
                             "Gap = time gap (how long ago the car in front was where the car behind is now).")

    def _on_battle_settings(self):
        st = battles.Settings()
        for name, _label, lo, hi, _step in SETTING_FIELDS:
            try:
                value = float(self.bset_vars[name].get().replace(",", "."))
                value = max(lo, min(hi, value))
            except (TypeError, ValueError):
                value = getattr(self.battle_settings, name)
            setattr(st, name, value)
        st.battle_s = max(st.battle_s, st.close_s)
        st.show_lapping = bool(self.blap_var.get())
        for name, *_rest in SETTING_FIELDS:
            self.bset_vars[name].set(_num(getattr(st, name)))
        if st == self.battle_settings:
            return
        self.battle_settings = st
        self._save_config(battle_settings={name: getattr(st, name) for name, *_r in SETTING_FIELDS}
                          | {"show_lapping": st.show_lapping})
        old_ticked = self.battle_ticked
        self._find_battles()
        self.selected_battle = None
        self.fill_battles(old_ticked)

    def reset_battle_settings(self):
        st = battles.Settings()
        for name, *_rest in SETTING_FIELDS:
            self.bset_vars[name].set(_num(getattr(st, name)))
        self.blap_var.set(st.show_lapping)
        self._on_battle_settings()

    def _on_battle_sort(self):
        key = next((k for k, label in SORTS if label == self.bsort_var.get()), "score")
        self._save_config(battle_sort=key)
        self.fill_battles()

    # ------------------------------------------------------------ list
    def _battle_visible(self, b):
        pick = self.bdriver_var.get()
        if pick == ALL_DRIVERS:
            return True
        return any(self.replay.cars[i].driver == pick for i in b.cars)

    def _sorted_battles(self):
        found = self.battle_result.battles if self.battle_result else []
        key = next((k for k, label in SORTS if label == self.bsort_var.get()), "score")
        start = [i for i, b in enumerate(found) if b.kind == "start"]
        rest = [i for i, b in enumerate(found) if b.kind != "start"]
        if key == "time":
            rest.sort(key=lambda i: found[i].start)
        elif key == "length":
            rest.sort(key=lambda i: -found[i].duration_s)
        elif key == "close":
            rest.sort(key=lambda i: -found[i].close_s)
        else:
            rest.sort(key=lambda i: -found[i].score)
        return start + rest

    def _laps_text(self, b):
        first, last = b.laps
        if first is None:
            return "-"
        return str(first) if first == last or last is None else "%d-%d" % (first, last)

    def fill_battles(self, old_ticked=None):
        tree = self.b_tree
        tree.delete(*tree.get_children())
        self.battle_rows, self.battle_keys = {}, {}
        if old_ticked is not None:
            self.battle_ticked = set()
        if self.selected_battle is None:
            self.battle_detail.set("Select a battle to see its details.")
            for btn in (self.best_btn, self.prev_red_btn, self.next_red_btn):
                btn.configure(state="disabled")
        if not self.model or not self.battle_result:
            self._update_battle_count()
            return
        found = self.battle_result.battles
        cars = self.replay.cars
        fc = analysis.fmt_clock
        t = self.replay.time_s
        for i in self._sorted_battles():
            b = found[i]
            if not self._battle_visible(b):
                continue
            key = _key("b", b.cars, b.start, b.end)
            if old_ticked is not None and any(_same(o, key) for o in old_ticked):
                self.battle_ticked.add(key)
            iid = "b%d" % i
            self.battle_rows[iid], self.battle_keys[iid] = (i, None), key
            when = "%s - %s" % (fc(b.start_s), fc(b.end_s))
            names = " vs ".join(cars[c].driver for c in b.cars)
            if b.kind == "start":
                values = (self._tick(key), when, fc(b.duration_s), "Start (%d cars)" % len(b.cars),
                          self._laps_text(b), "", "", "", len(b.lead_changes) or "", "",
                          ", ".join(b.flags))
                tree.insert("", "end", iid=iid, values=values, tags=("start",))
                continue
            gap = "-" if b.min_gap_s is None else "%.2f / %.2f s" % (b.min_gap_s, b.mean_gap_s)
            values = (self._tick(key), when, fc(b.duration_s), names, self._laps_text(b),
                      "%.0f s" % b.close_s, gap, "%.1f s" % b.side_s if b.side_s else "-",
                      len(b.lead_changes) or "-", "%.0f" % b.score, ", ".join(b.flags))
            tree.insert("", "end", iid=iid, values=values)
            for j, p in enumerate(b.phases):
                pkey = _key("p", b.cars, p.start, p.end)
                if old_ticked is not None and any(_same(o, pkey) for o in old_ticked):
                    self.battle_ticked.add(pkey)
                pid = "b%d.%d" % (i, j)
                self.battle_rows[pid], self.battle_keys[pid] = (i, j), pkey
                pgap = "" if p.min_gap_s is None else "%.2f - %.2f s" % (p.min_gap_s, p.max_gap_s)
                leads = sum(1 for f, _w, _x in b.lead_changes if p.start <= f <= p.end)
                tree.insert(iid, "end", iid=pid, tags=(p.kind,), values=(
                    self._tick(pkey), "%s - %s" % (fc(t(p.start)), fc(t(p.end))), fc(t(p.end) - t(p.start)),
                    p.kind, "", "", pgap, "%.1f s" % p.side_s if p.side_s else "", leads or "", "", ""))
        self._update_battle_count()

    def _tick(self, key):
        return TICK_ON if key in self.battle_ticked else TICK_OFF

    def _update_battle_count(self):
        found = self.battle_result.battles if self.battle_result else []
        shown = sum(1 for iid, (i, j) in self.battle_rows.items() if j is None)
        n_b = sum(1 for k in self.battle_ticked if k[0] == "b")
        n_p = len(self.battle_ticked) - n_b
        self.battle_count.set("%d shown of %d   |   ticked: %d battle(s), %d phase(s)" % (
            shown, len(found), n_b, n_p))

    def _set_battle_tick(self, iid, on):
        key = self.battle_keys.get(iid)
        if key is None:
            return
        if on:
            self.battle_ticked.add(key)
        else:
            self.battle_ticked.discard(key)
        self.b_tree.set(iid, "tick", TICK_ON if on else TICK_OFF)

    def tick_shown_battles(self, on):
        for iid, (_i, j) in self.battle_rows.items():
            if j is None:
                self._set_battle_tick(iid, on)
        self._update_battle_count()

    def _on_battle_click(self, event):
        tree = self.b_tree
        if tree.identify_region(event.x, event.y) == "cell" and tree.identify_column(event.x) == "#1":
            iid = tree.identify_row(event.y)
            if iid:
                self._set_battle_tick(iid, self.battle_keys[iid] not in self.battle_ticked)
                self._update_battle_count()
                return "break"
        return None

    def _on_battle_space(self, _event=None):
        rows = list(self.b_tree.selection())
        if rows:
            on = self.battle_keys[rows[0]] not in self.battle_ticked
            for iid in rows:
                self._set_battle_tick(iid, on)
            self._update_battle_count()
        return "break"

    # ------------------------------------------------------------ selection
    def _on_battle_select(self, _event=None):
        sel = self.b_tree.selection()
        if not sel or not self.model or sel[0] not in self.battle_rows:
            return
        i, j = self.battle_rows[sel[0]]
        if self.selected_battle == (i, j):
            return
        self.selected_battle = (i, j)
        b = self.battle_result.battles[i]
        frame = b.start if j is None else b.phases[j].start
        if len(sel) == 1:
            self.battle_map.zoom_to(b.cars if b.kind != "start" else b.cars[:2], frame,
                                    level=self._zoom_level())
        self._show_battle_detail(b, j)
        self.set_frame(frame)

    def selected_battle_obj(self):
        if self.selected_battle is None or not self.battle_result:
            return None
        i = self.selected_battle[0]
        found = self.battle_result.battles
        return found[i] if 0 <= i < len(found) else None

    def _show_battle_detail(self, b, j):
        cars = self.replay.cars
        fc = analysis.fmt_clock
        t = self.replay.time_s
        name = lambda c: cars[c].driver      # noqa: E731
        lines = []
        if b.kind == "start":
            lines.append("Start: %d cars, %s - %s. Normal battles are searched from %s on." % (
                len(b.cars), fc(b.start_s), fc(b.end_s), fc(b.end_s)))
        else:
            lines.append("%s: close %.0f s of %.0f s, gap min %.2f s / mean %.2f s, side by side %.1f s, "
                         "red %.1f s." % (" vs ".join(map(name, b.cars)), b.close_s, b.duration_s,
                                         b.min_gap_s, b.mean_gap_s, b.side_s, b.red_s))
        if b.lead_changes:
            parts = []
            for f, w, x in b.lead_changes[:10]:
                parts.append("%s %s gets past %s" % (fc(t(f)), name(w), name(x)))
            more = "" if len(b.lead_changes) <= 10 else " (+%d more)" % (len(b.lead_changes) - 10)
            lines.append("Lead changes: " + ";  ".join(parts) + more)
        if b.kind != "start":
            lines.append("Best moment %s, %d red moment(s)." % (
                "-" if b.best_frame is None else fc(t(b.best_frame)), len(b.red_frames)))
        if b.flags:
            lines.append("Notes: " + ", ".join(b.flags))
        if j is not None:
            p = b.phases[j]
            lines.append("Phase: %s %s - %s." % (p.kind, fc(t(p.start)), fc(t(p.end))))
        self.battle_detail.set("\n".join(lines))
        self.best_btn.configure(state="normal" if b.best_frame is not None else "disabled")
        red = "normal" if b.red_frames else "disabled"
        self.prev_red_btn.configure(state=red)
        self.next_red_btn.configure(state=red)

    def battle_best_moment(self):
        b = self.selected_battle_obj()
        if b is not None and b.best_frame is not None:
            self.set_frame(b.best_frame)

    def battle_red(self, direction):
        """Jump to the next (direction 1) or previous (-1) red moment of the battle."""
        b = self.selected_battle_obj()
        if b is None or not b.red_frames:
            return
        margin = 3
        if direction > 0:
            later = [f for f in b.red_frames if f > self.frame + margin]
            target = later[0] if later else b.red_frames[0]          # wrap around
        else:
            earlier = [f for f in b.red_frames if f < self.frame - margin]
            target = earlier[-1] if earlier else b.red_frames[-1]
        self.set_frame(target)

    def update_battle_map(self):
        m = self.battle_map
        m.frame = self.frame
        m.units = self.units
        pick = self.bdriver_var.get()
        m.selected = next((i for i in self.model.active if self.replay.cars[i].driver == pick), None) \
            if pick != ALL_DRIVERS else None
        b = self.selected_battle_obj()
        m.highlight = tuple(b.cars) if (b is not None and b.kind != "start") else ()
        m.draw_cars()
