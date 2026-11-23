"""The Battles tab: cars that drive close together for a while (see battles.py).

A mixin for the App window: it uses the App's time bar (set_frame), its config
helpers and its map options. Every battle is one row that opens into its
phases (close / pause). Battles and phases can each be ticked, with their own
clip length before and after; ticks and clip lengths follow the battles when
the settings change, and they also hold for rows hidden by the driver filter.
Ticked rows become clips in the Save tab, together with the ticked overtakes.
"""
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

import analysis
import battlechart
import battles
import clips
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
BATTLE_TRAIL_S = 4        # defaults of the Battles map (the Overtakes map has its own)
BATTLE_AHEAD_S = 2
GAP_ALL, GAP_SELECTED, GAP_OFF = "all", "selected", "off"
GAP_MODES = ((GAP_ALL, "All cars"), (GAP_SELECTED, "Selected battle / duel only"), (GAP_OFF, "Off"))


def _seconds(value, fallback):
    try:
        return max(0, min(20, int(round(float(value)))))
    except (TypeError, ValueError):
        return fallback


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
        self.battle_items = {}         # key -> (battle index, phase index or None), all battles
        self.battle_rows = {}          # iid -> (battle index, phase index or None), rows shown
        self.battle_keys = {}          # iid -> key (see _key)
        self.battle_ticked = set()     # keys
        self.battle_clip_override = {}  # key -> (before, after) set by hand
        self.selected_battle = None    # (battle index, phase index or None)
        self._battle_sash_set = False
        self._bsyncing = False
        self.battle_before = clips.clamp_seconds(cfg.get("battle_before_s"), clips.BATTLE_BEFORE_S)
        self.battle_after = clips.clamp_seconds(cfg.get("battle_after_s"), clips.BATTLE_AFTER_S)

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
        ttk.Label(row1, text="Default clip:  before").pack(side="left", padx=(12, 0))
        self.bbefore_var = tk.StringVar(value=str(self.battle_before))
        self.bafter_var = tk.StringVar(value=str(self.battle_after))
        for var, text in ((self.bbefore_var, "s   after"), (self.bafter_var, "s")):
            spin = ttk.Spinbox(row1, from_=0, to=clips.MAX_CLIP_S, width=4, textvariable=var,
                               command=self._on_battle_default)
            spin.pack(side="left", padx=4)
            spin.bind("<Return>", lambda e: self._on_battle_default())
            spin.bind("<FocusOut>", lambda e: self._on_battle_default())
            ttk.Label(row1, text=text).pack(side="left")

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
        self.battle_start_info = tk.StringVar(value="")
        bold = tkfont.nametofont("TkDefaultFont").copy()
        bold.configure(weight="bold")
        info = ttk.Label(parent, textvariable=self.battle_start_info, foreground="#0d47a1",
                         font=bold, cursor="hand2", wraplength=1400, justify="left")
        info.pack(fill="x", pady=(2, 2))
        info.bind("<Button-1>", lambda e: self._jump_to_search_start())
        self.battle_count = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.battle_count).pack(fill="x")
        self.battle_note = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.battle_note, foreground="#666666").pack(fill="x")

        self.bwork = ttk.Panedwindow(parent, orient="vertical")
        self.bwork.pack(fill="both", expand=True)
        self.bupper = ttk.Panedwindow(self.bwork, orient="horizontal")
        self._cfg_bsash = (cfg.get("sash_bh"), cfg.get("sash_bv"))

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
        self.bclip_panel = ttk.Frame(detail, padding=(0, 4, 0, 0))
        self.bclip_panel.pack(fill="x")
        ttk.Label(self.bclip_panel, text="Clip of the selected row(s):").pack(side="left")
        self.bsel_before = self._battle_slider(self.bclip_panel, "Before", 0)
        self.bsel_after = self._battle_slider(self.bclip_panel, "After", 1)
        ttk.Button(self.bclip_panel, text="Reset to default", command=self.reset_selected_battle_clips).pack(
            side="left", padx=10)
        self.battle_detail = tk.StringVar(value="Select a battle to see its details.")
        self.bdetail_btn = ttk.Button(detail, text="", width=12, command=self.toggle_battle_detail)
        self.bdetail_btn.pack(anchor="w", pady=(4, 0))
        self.bdetail_label = ttk.Label(detail, textvariable=self.battle_detail, justify="left", wraplength=620)
        self.bdetail_open = False          # closed every time the program starts
        self._apply_battle_detail()

        cols = ("tick", "time", "length", "cars", "laps", "close", "gap", "side", "leads", "score",
                "before", "after", "notes")
        self.b_tree = self._scrolled_tree(left, horizontal=True, columns=cols, show="tree headings",
                                          height=8, selectmode="extended")
        self.b_tree.column("#0", width=30, minwidth=30, stretch=False)
        for key, text, width in (("tick", "Use", 40), ("time", "Replay time", 120), ("length", "Length", 60),
                                 ("cars", "Cars", 260), ("laps", "Lap", 50), ("close", "Close", 60),
                                 ("gap", "Gap min / mean", 110), ("side", "Side by side", 80),
                                 ("leads", "Lead changes", 85), ("score", "Score", 50),
                                 ("before", "Before (s)", 70), ("after", "After (s)", 70), ("notes", "Notes", 260)):
            self.b_tree.heading(key, text=text)
            self.b_tree.column(key, width=width, stretch=key in ("cars", "notes"),
                               anchor="w" if key in ("cars", "notes") else "center")
        self.b_tree.tag_configure("start", foreground="#555555")
        self.b_tree.tag_configure("pause", foreground="#777777")
        self.b_tree.tag_configure("group", foreground="#4a148c")
        self.b_tree.bind("<<TreeviewSelect>>", self._on_battle_select)
        self.b_tree.bind("<Button-1>", self._on_battle_click)
        self.b_tree.bind("<space>", self._on_battle_space)
        self.bupper.add(left, weight=5)

        map_box = ttk.Labelframe(self.bupper, text="Track map (wheel = zoom, drag = move, double-click = reset)",
                                 padding=4)
        mbar = ttk.Frame(map_box)
        mbar.pack(fill="x")
        self.btrail_var = tk.StringVar(value=str(_seconds(cfg.get("battle_map_trail_s"), BATTLE_TRAIL_S)))
        self.bahead_var = tk.StringVar(value=str(_seconds(cfg.get("battle_map_ahead_s"), BATTLE_AHEAD_S)))
        ttk.Label(mbar, text="Trail behind").pack(side="left")
        for var, text in ((self.btrail_var, "s   Look ahead"), (self.bahead_var, "s")):
            spin = ttk.Spinbox(mbar, from_=0, to=20, width=3, textvariable=var, command=self._on_battle_map_options)
            spin.pack(side="left", padx=4)
            spin.bind("<Return>", lambda e: self._on_battle_map_options())
            spin.bind("<FocusOut>", lambda e: self._on_battle_map_options())
            ttk.Label(mbar, text=text).pack(side="left")
        ttk.Label(mbar, text="Gap colours:").pack(side="left", padx=(14, 4))
        saved_gap = cfg.get("battle_map_gaps")
        self.bgap_var = tk.StringVar(value=dict(GAP_MODES).get(saved_gap, GAP_MODES[0][1]))
        gbox = ttk.Combobox(mbar, textvariable=self.bgap_var, state="readonly", width=24,
                            values=[label for _k, label in GAP_MODES])
        gbox.pack(side="left")
        gbox.bind("<<ComboboxSelected>>", lambda e: self._on_battle_map_options())
        self.bmap_canvas = tk.Canvas(map_box, background=mapview.GRASS, height=200, width=200, highlightthickness=0)
        self.bmap_canvas.pack(fill="both", expand=True)
        self.battle_map = mapview.MapView(self.bmap_canvas, self.colors_list)
        self.battle_map.initials = self.map.initials
        self._on_battle_map_options(redraw=False)
        self.bupper.add(map_box, weight=4)
        self.bwork.add(self.bupper, weight=4)

        chart_box = ttk.Labelframe(self.bwork, text="Gap of the selected battle (click = jump, wheel = zoom)",
                                   padding=4)
        self.battle_chart = battlechart.BattleChart(chart_box, self)
        self.bwork.add(chart_box, weight=2)

    def _apply_battle_detail(self):
        self.bdetail_btn.configure(text=("v Details" if self.bdetail_open else "> Details"))
        if self.bdetail_open:
            self.bdetail_label.pack(anchor="w", pady=(2, 0))
        else:
            self.bdetail_label.pack_forget()

    def toggle_battle_detail(self):
        self.bdetail_open = not self.bdetail_open
        self._apply_battle_detail()

    def _gap_mode(self):
        return next((k for k, label in GAP_MODES if label == self.bgap_var.get()), GAP_ALL)

    def _on_battle_map_options(self, redraw=True):
        m = self.battle_map
        m.trail_s = _seconds(self.btrail_var.get(), BATTLE_TRAIL_S)
        m.ahead_s = _seconds(self.bahead_var.get(), BATTLE_AHEAD_S)
        self.btrail_var.set(str(m.trail_s))
        self.bahead_var.set(str(m.ahead_s))
        mode = self._gap_mode()
        m.gap_lines = m.gap_label = mode != GAP_OFF
        self._save_config(battle_map_trail_s=m.trail_s, battle_map_ahead_s=m.ahead_s, battle_map_gaps=mode)
        if redraw and self.model:
            self.update_battle_map()

    def _battle_slider(self, parent, title, which):
        """[-] slider [+] and the value, for the clip length before/after."""
        box = ttk.Frame(parent)
        box.pack(side="left", padx=(10, 0))
        ttk.Label(box, text=title).pack(side="left")
        ttk.Button(box, text="-", width=2, command=lambda: self._battle_nudge(which, -1)).pack(side="left", padx=(4, 0))
        scale = ttk.Scale(box, from_=0, to=clips.MAX_CLIP_S, orient="horizontal", length=140,
                          command=lambda v, w=which: self._on_battle_slider(w, v))
        scale.pack(side="left")
        ttk.Button(box, text="+", width=2, command=lambda: self._battle_nudge(which, 1)).pack(side="left")
        label = tk.StringVar(value="- s")
        ttk.Label(box, textvariable=label, width=7).pack(side="left", padx=(4, 0))
        return {"scale": scale, "label": label}

    def _place_battle_sash(self):
        try:
            w, h = self.bupper.winfo_width(), self.bwork.winfo_height()
            if w < 300 or h < 200:
                self._bsash_tries = getattr(self, "_bsash_tries", 0) + 1
                if self._bsash_tries < 20:
                    self.after(250, self._place_battle_sash)
                return
            fh, fv = self._cfg_bsash
            fh = fh if isinstance(fh, (int, float)) and 0.15 < fh < 0.9 else 0.55
            fv = fv if isinstance(fv, (int, float)) and 0.2 < fv < 0.9 else 0.66
            self.bupper.sashpos(0, int(w * fh))
            self.bwork.sashpos(0, int(h * fv))
            self._battle_sash_set = True
        except (tk.TclError, AttributeError, TypeError):
            pass

    def battle_sash_shares(self):
        """{config key: share} of the Battles tab sashes (empty when not placed yet)."""
        out = {}
        try:
            w, h = self.bupper.winfo_width(), self.bwork.winfo_height()
            if self._battle_sash_set and w > 300:
                out["sash_bh"] = round(self.bupper.sashpos(0) / w, 3)
            if self._battle_sash_set and h > 200:
                out["sash_bv"] = round(self.bwork.sashpos(0) / h, 3)
        except (tk.TclError, AttributeError, ZeroDivisionError):
            pass
        return out

    # ------------------------------------------------------------ data
    def battles_loaded(self, measured):
        """A new replay: measurement done by the loader, now find the battles."""
        self.battle_measured = measured
        self.battle_ticked = set()
        self.battle_clip_override = {}
        self.selected_battle = None
        names = sorted({self.replay.cars[i].driver for i in self.model.active})
        self.bdriver_box.configure(values=[ALL_DRIVERS] + names)
        self.bdriver_var.set(ALL_DRIVERS)
        self.battle_map.share_from(self.map)
        for m in (self.map, self.battle_map):
            m.gaps = measured.gaps
            m.close_s = self.battle_settings.close_s
        self._find_battles()
        self.fill_battles()
        self.battle_chart.show(None)

    def _find_battles(self):
        self.battle_items = {}
        if self.model is None or self.battle_measured is None:
            self.battle_result = None
            return
        self.battle_result = battles.find_battles(self.model, settings=self.battle_settings,
                                                  measured=self.battle_measured)
        for i, b in enumerate(self.battle_result.battles):
            self.battle_items[_key("b", b.cars, b.start, b.end)] = (i, None)
            if b.kind != "start":
                for j, p in enumerate(b.phases):
                    self.battle_items[_key("p", b.cars, p.start, p.end)] = (i, j)
        self.battle_start_info.set(self._start_text())
        notes = list(self.battle_result.notes)
        self.battle_note.set("  ".join(notes) + ("  " if notes else "") +
                             "Gap = time gap (how long ago the car in front was where the car behind is now).")

    def _start_text(self):
        """From when battles are searched, and why (shown above the list)."""
        r = self.battle_result
        fc = analysis.fmt_clock
        t = self.replay.time_s
        if r is None or r.start_frame is None or r.start_end <= r.start_frame:
            return "Battles are searched over the whole replay."
        start, end = fc(t(r.start_frame)), fc(t(r.start_end))
        head = "Battles are searched from %s on (click to go there). At the start (%s) the field drives " \
               "too close together: " % (end, start)
        if r.start_reason == battles.START_LAP1:
            why = "it stayed together until the leader finished lap 1 at %s." % end
        elif r.start_reason == battles.START_MAX:
            why = "without lap data the start counts as over after %.0f s, at %s." % (battles.START_MAX_S, end)
        else:
            why = "at %s it had broken up (no group of %d or more cars within %s s of each other)." % (
                end, battles.START_GROUP, _num(self.battle_settings.battle_s))
        return head + why + " Everything before that is in the Start row."

    def _jump_to_search_start(self):
        if self.model and self.battle_result:
            self.set_frame(self.battle_result.start_end)

    def _carry_over(self, old_ticked, old_override):
        """After new settings: keep ticks and hand-set clips on the same battles/phases."""
        self.battle_ticked, self.battle_clip_override = set(), {}
        for key in self.battle_items:
            if any(_same(o, key) for o in old_ticked):
                self.battle_ticked.add(key)
            for o, value in old_override.items():
                if _same(o, key):
                    self.battle_clip_override[key] = value
                    break

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
        for m in (self.map, self.battle_map):
            m.close_s = st.close_s
        old = (self.battle_ticked, self.battle_clip_override)
        self._find_battles()
        self._carry_over(*old)
        self.selected_battle = None
        self.battle_chart.show(None)
        self.fill_battles()
        if self.model:
            self.set_frame(self.frame)          # standings and maps follow the new limits

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

    # ------------------------------------------------------------ clips
    def battle_clip_span(self, key):
        return self.battle_clip_override.get(key, (self.battle_before, self.battle_after))

    def _item_range(self, key):
        """(first, last) frames of a battle or phase, without the extra seconds."""
        i, j = self.battle_items[key]
        b = self.battle_result.battles[i]
        return (b.start, b.end) if j is None else (b.phases[j].start, b.phases[j].end)

    def battle_clip_items(self, keys=None):
        """Clips of the ticked battles and phases (or of `keys`): dicts with first,
        last (frames incl. before/after), cars, pairs, label, start, end."""
        out = []
        if not self.model or not self.battle_result:
            return out
        cars = self.replay.cars
        t = self.replay.time_s
        for key in (self.battle_ticked if keys is None else keys):
            if key not in self.battle_items:
                continue
            i, j = self.battle_items[key]
            b = self.battle_result.battles[i]
            start, end = self._item_range(key)
            before, after = self.battle_clip_span(key)
            first, last = clips.stretch(self.model, start, end, before, after)
            if b.kind == "start":
                label = "Start"
            else:
                label = " vs ".join(cars[c].driver for c in b.cars)
                if b.kind == "group":
                    label = "train " + label
                if j is not None:
                    label += " (%s phase)" % b.phases[j].kind
            label += " %s-%s" % (analysis.fmt_clock(t(start)), analysis.fmt_clock(t(end)))
            out.append({"first": first, "last": last, "cars": list(b.cars), "pairs": list(b.pairs),
                        "label": label, "start": start, "end": end})
        out.sort(key=lambda d: d["first"])
        return out

    def selected_battle_clips(self):
        keys = [self.battle_keys[iid] for iid in self.b_tree.selection() if iid in self.battle_keys]
        return clips.merge((d["first"], d["last"]) for d in self.battle_clip_items(keys))

    def _on_battle_default(self):
        self.battle_before = clips.clamp_seconds(self.bbefore_var.get(), self.battle_before)
        self.battle_after = clips.clamp_seconds(self.bafter_var.get(), self.battle_after)
        self.bbefore_var.set(str(self.battle_before))
        self.bafter_var.set(str(self.battle_after))
        self._save_config(battle_before_s=self.battle_before, battle_after_s=self.battle_after)
        default = (self.battle_before, self.battle_after)
        self.battle_clip_override = {k: v for k, v in self.battle_clip_override.items() if v != default}
        self._battle_clips_changed()

    def _set_battle_clip(self, key, which, value):
        span = list(self.battle_clip_span(key))
        span[which] = value
        if tuple(span) == (self.battle_before, self.battle_after):
            self.battle_clip_override.pop(key, None)
        else:
            self.battle_clip_override[key] = tuple(span)

    def _selected_battle_keys(self):
        return [self.battle_keys[iid] for iid in self.b_tree.selection() if iid in self.battle_keys]

    def _on_battle_slider(self, which, value):
        if self._bsyncing or not self.model:
            return
        keys = self._selected_battle_keys()
        if not keys:
            return
        for key in keys:
            self._set_battle_clip(key, which, clips.clamp_seconds(value))
        self._battle_clips_changed()

    def _battle_nudge(self, which, delta):
        keys = self._selected_battle_keys()
        if not keys or not self.model:
            return
        value = clips.clamp_seconds(self.battle_clip_span(keys[0])[which] + delta)
        for key in keys:
            self._set_battle_clip(key, which, value)
        self._battle_clips_changed()

    def reset_selected_battle_clips(self):
        for key in self._selected_battle_keys():
            self.battle_clip_override.pop(key, None)
        self._battle_clips_changed()

    def _battle_clips_changed(self):
        for iid, key in self.battle_keys.items():
            b, a = self.battle_clip_span(key)
            mark = "*" if key in self.battle_clip_override else ""
            self.b_tree.set(iid, "before", "%d%s" % (b, mark))
            self.b_tree.set(iid, "after", "%d%s" % (a, mark))
        self._sync_battle_sliders()
        self._update_battle_count()

    def _sync_battle_sliders(self):
        keys = self._selected_battle_keys()
        self._bsyncing = True
        try:
            for box, k in ((self.bsel_before, 0), (self.bsel_after, 1)):
                if keys:
                    value = self.battle_clip_span(keys[0])[k]
                    box["scale"].set(value)
                    box["label"].set("%d s" % value + ("" if len(keys) == 1 else " (+%d)" % (len(keys) - 1)))
                else:
                    box["label"].set("- s")
        finally:
            self._bsyncing = False

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
        rest = [i for i, b in enumerate(found) if b.kind != "start" and b.parent is None]
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

    def _clip_cells(self, key):
        b, a = self.battle_clip_span(key)
        mark = "*" if key in self.battle_clip_override else ""
        return "%d%s" % (b, mark), "%d%s" % (a, mark)

    def fill_battles(self):
        tree = self.b_tree
        tree.delete(*tree.get_children())
        self.battle_rows, self.battle_keys = {}, {}
        if self.selected_battle is None:
            self.battle_detail.set("Select a battle to see its details.")
            for btn in (self.best_btn, self.prev_red_btn, self.next_red_btn):
                btn.configure(state="disabled")
        if not self.model or not self.battle_result:
            self._update_battle_count()
            return
        found = self.battle_result.battles
        for i in self._sorted_battles():
            b = found[i]
            if not self._battle_visible(b):
                continue
            iid = "b%d" % i
            self._insert_battle("", iid, i)
            if b.kind == "group":
                for d in b.duels:
                    if self._battle_visible(found[d]):
                        self._insert_battle(iid, "g%dd%d" % (i, d), d)
        self._update_battle_count()

    def _insert_battle(self, parent, iid, i):
        """One row of the list (a battle, group or duel) with its phases under it."""
        tree = self.b_tree
        b = self.battle_result.battles[i]
        cars = self.replay.cars
        fc = analysis.fmt_clock
        t = self.replay.time_s
        key = _key("b", b.cars, b.start, b.end)
        self.battle_rows[iid], self.battle_keys[iid] = (i, None), key
        when = "%s - %s" % (fc(b.start_s), fc(b.end_s))
        if b.kind == "start":
            values = (self._tick(key), when, fc(b.duration_s), "Start (%d cars)" % len(b.cars),
                      self._laps_text(b), "", "", "", len(b.lead_changes) or "", "",
                      *self._clip_cells(key), ", ".join(b.flags))
            tree.insert(parent, "end", iid=iid, values=values, tags=("start",))
            return
        names = " vs ".join(cars[c].driver for c in b.cars)
        if b.kind == "group":
            names = "Train of %d: %s" % (len(b.cars), names)
        elif parent:
            names = "Duel: " + names
        gap = "-" if b.min_gap_s is None else "%.2f / %.2f s" % (b.min_gap_s, b.mean_gap_s)
        values = (self._tick(key), when, fc(b.duration_s), names,
                  self._laps_text(b), "%.0f s" % b.close_s, gap, "%.1f s" % b.side_s if b.side_s else "-",
                  len(b.lead_changes) or "-", "%.0f" % b.score, *self._clip_cells(key), ", ".join(b.flags))
        tree.insert(parent, "end", iid=iid, values=values, tags=(b.kind,))
        if b.kind == "group":
            return                   # a group opens into its duels, not into phases
        for j, p in enumerate(b.phases):
            pkey = _key("p", b.cars, p.start, p.end)
            pid = "%s.%d" % (iid, j)
            self.battle_rows[pid], self.battle_keys[pid] = (i, j), pkey
            pgap = "" if p.min_gap_s is None else "%.2f - %.2f s" % (p.min_gap_s, p.max_gap_s)
            leads = sum(1 for f, _w, _x in b.lead_changes if p.start <= f <= p.end)
            tree.insert(iid, "end", iid=pid, tags=(p.kind,), values=(
                self._tick(pkey), "%s - %s" % (fc(t(p.start)), fc(t(p.end))), fc(t(p.end) - t(p.start)),
                p.kind, "", "", pgap, "%.1f s" % p.side_s if p.side_s else "", leads or "", "",
                *self._clip_cells(pkey), ""))

    def _tick(self, key):
        return TICK_ON if key in self.battle_ticked else TICK_OFF

    def _update_battle_count(self):
        """Ticks or clip lengths changed: refresh every count, overview and chart."""
        if self.model:
            self._update_tick_count()       # also calls _battle_count_text
            self.battle_chart.draw()
        else:
            self._battle_count_text()

    def _battle_count_text(self):
        found = self.battle_result.battles if self.battle_result else []
        shown = sum(1 for iid, (_i, j) in self.battle_rows.items() if j is None and iid.startswith("b")
                    and "." not in iid)
        found = [b for b in found if b.parent is None]
        n_b = sum(1 for k in self.battle_ticked if k[0] == "b")
        n_p = len(self.battle_ticked) - n_b
        spans = self.clip_ranges() if self.model else []
        seconds = sum(b - a + 1 for a, b in spans) * (self.replay.interval_ms / 1000.0) if spans else 0
        self.battle_count.set("%d shown of %d battles   |   ticked: %d battle(s), %d phase(s)   |   new replay "
                              "(with the ticked overtakes): %d clip(s), %s   (* = clip length set by hand)" % (
                                  shown, len(found), n_b, n_p, len(spans), analysis.fmt_clock(seconds)))

    def _set_battle_tick(self, iid, on):
        key = self.battle_keys.get(iid)
        if key is None:
            return
        if on:
            self.battle_ticked.add(key)
        else:
            self.battle_ticked.discard(key)
        for other, k in self.battle_keys.items():     # the same duel can be shown twice
            if k == key:
                self.b_tree.set(other, "tick", TICK_ON if on else TICK_OFF)

    def tick_shown_battles(self, on):
        for iid in self.b_tree.get_children():          # the rows at the top level
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
        self._sync_battle_sliders()
        if not sel or not self.model or sel[0] not in self.battle_rows:
            return
        i, j = self.battle_rows[sel[0]]
        if self.selected_battle == (i, j):
            self.battle_chart.draw()
            return
        new_battle = self.selected_battle is None or self.selected_battle[0] != i
        self.selected_battle = (i, j)
        b = self.battle_result.battles[i]
        frame = b.start if j is None else b.phases[j].start
        if len(sel) == 1:
            self.battle_map.zoom_to(b.cars if b.kind != "start" else b.cars[:2], frame,
                                    level=self._zoom_level())
        self._show_battle_detail(b, j)
        if new_battle:
            self.battle_chart.show(b)
        self.set_frame(frame)
        self.battle_chart.draw()

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
            if b.kind == "group":
                lines.append("Train of %d cars, %d duel(s) inside: open the row to pick a duel, its map, "
                             "chart and camera then follow only those two cars." % (len(b.cars), len(b.duels)))
            lines.append("%s: close %.0f s of %.0f s, gap min %.2f s / mean %.2f s, side by side %.1f s, "
                         "red %.1f s." % (" vs ".join(map(name, b.cars)), b.close_s, b.duration_s,
                                         b.min_gap_s, b.mean_gap_s, b.side_s, b.red_s))
        if b.lead_changes:
            parts = ["%s %s gets past %s" % (fc(t(f)), name(w), name(x)) for f, w, x in b.lead_changes[:10]]
            more = "" if len(b.lead_changes) <= 10 else " (+%d more)" % (len(b.lead_changes) - 10)
            lines.append("Lead changes: " + ";  ".join(parts) + more)
        if b.kind != "start":
            lines.append("Best moment %s, %d red moment(s)." % (
                "-" if b.best_frame is None else fc(t(b.best_frame)), len(b.red_frames)))
        if b.flags:
            lines.append("Notes: " + ", ".join(b.flags))
        for others, lo, hi in b.trains:
            lines.append("In a train %s - %s with %s" % (fc(t(lo)), fc(t(hi)), ", ".join(map(name, others))))
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
        # "selected battle only": no lines at all while no battle is selected
        m.gap_cars = set(m.highlight) if self._gap_mode() == GAP_SELECTED else None
        m.draw_cars()
        self.battle_chart.follow()
