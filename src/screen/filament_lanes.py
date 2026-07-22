import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, GdkPixbuf, Gdk, Pango
import logging
import os

from panels.base_panel import ScreenPanel
from panels.spoolman_common import extract_spool_fields

logger = logging.getLogger("KlipperScreen")

# Based on work by KlipperScreen Contributors (https://github.com/KlipperScreen/KlipperScreen)
# Original license: GPL v3
# Spool SVG color-substitution pattern adapted from panels/spoolman.py

# Minimal spool SVG used when KlipperScreen's own styles/spool.svg isn't found.
# Uses var(--filament-color) as a substitution target, same as KlipperScreen's
# own spool.svg, so the same replacement code works for both.
_FALLBACK_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <circle cx="50" cy="50" r="47" fill="var(--filament-color)" stroke="#33333388" stroke-width="3"/>
  <circle cx="50" cy="50" r="32" fill="none" stroke="#00000028" stroke-width="28"/>
  <circle cx="50" cy="50" r="15" fill="#2a2a2a" stroke="#33333388" stroke-width="2"/>
</svg>"""

# CSS injected once to colour the active-tool indicator bar.
# Guarded by a class variable so it only runs once across panel visits.
_CSS_INJECTED = False
_INDICATOR_CSS = b"""
.lane-active-indicator { background-color: #CC0000; }
"""


def create_panel(*args):
    return Panel(*args)


class Panel(ScreenPanel):

    def __init__(self, screen, title, **kwargs):
        title = title or _("Filament Lanes")
        super().__init__(screen, title)

        self.tool_count = 0
        self.lane_data = {}          # str(n) -> {color, name, material, vendor, spool_id}
        self.active_tool = None
        self.sensor_states = {}      # int(n) -> bool
        self._is_printing = False
        self._refresh_timer = None

        # Per-column widget references
        self._col_wraps = {}         # n -> outer Gtk.Box (wrap + indicator)
        self._col_boxes = {}         # n -> inner Gtk.Box (can be dimmed)
        self._active_indicators = {} # n -> Gtk.Box (red bar)
        self._spool_images = {}      # n -> Gtk.Image inside the spool button
        self._info_labels = {}       # n -> Gtk.Label (name)
        self._detail_labels = {}     # n -> Gtk.Label (material · weight)
        self._id_labels = {}         # n -> Gtk.Label (#spool_id)
        self._unload_btns = {}       # n -> Gtk.Button
        self._load_btns = {}         # n -> Gtk.Button (only when show_load_buttons)

        # KlipperScreen.conf option — set show_load_buttons: true to reveal Load
        cfg = self.ks_printer_cfg
        raw = cfg.get("show_load_buttons", "false") if cfg else "false"
        self.show_load_buttons = raw.strip().lower() in ("true", "1", "yes")

        self._action_buttons = self._parse_action_buttons(cfg)
        self._extra_bar_btns = []   # buttons added to base_panel action_bar

        global _CSS_INJECTED
        if not _CSS_INJECTED:
            provider = Gtk.CssProvider()
            provider.load_from_data(_INDICATOR_CSS)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            _CSS_INJECTED = True

        self._spool_svg_template = self._load_spool_svg()

        self._build_placeholder()
        # Defer first fetch so the panel renders before blocking on HTTP calls
        GLib.idle_add(self._fetch_data)
        # Refresh every 10 seconds to pick up spool assignment changes
        self._refresh_timer = GLib.timeout_add_seconds(10, self._on_refresh_timer)

    # ------------------------------------------------------------------ #
    # SVG template loader                                                  #
    # ------------------------------------------------------------------ #

    def _load_spool_svg(self):
        # Prefer KlipperScreen's own spool.svg so our spools match theirs.
        try:
            from panels.spoolman import SpoolmanSpool
            tpl = getattr(SpoolmanSpool, "_spool_icon", None)
            if tpl:
                return tpl.encode() if isinstance(tpl, str) else tpl
        except Exception:
            pass

        candidates = []
        if hasattr(self._screen, "klipperscreendir"):
            candidates.append(
                os.path.join(self._screen.klipperscreendir, "styles", "spool.svg")
            )
        candidates += [
            os.path.expanduser("~/KlipperScreen/styles/spool.svg"),
            "/home/pi/KlipperScreen/styles/spool.svg",
        ]
        for path in candidates:
            if os.path.isfile(path):
                try:
                    with open(path, "rb") as f:
                        return f.read()
                except Exception:
                    pass

        return _FALLBACK_SVG

    # ------------------------------------------------------------------ #
    # Data layer — Spoolman + Klipper save_variables                       #
    # ------------------------------------------------------------------ #

    def _fetch_data(self):
        """
        Reads t{N}__spool_id from Klipper save_variables via REST, then
        fetches spool details from Spoolman via Moonraker proxy.
        No dependency on spoolman-lane-sync or any external sync service.
        Called on panel open, on activate(), and every 10 s by a timer.
        """
        tool_count = self._detect_tool_count()
        if tool_count == 0:
            logger.warning("filament_lanes: could not determine tool count")
            return False

        changed_tool_count = (tool_count != self.tool_count)
        self.tool_count = tool_count

        # Fetch save_variables + toolchanger state via synchronous REST.
        # KlipperScreen uses the same pattern for its own periodic fetches.
        result = self._screen.apiclient.send_request(
            "printer/objects/query?save_variables&toolchanger"
        )
        variables = {}
        if result and isinstance(result, dict) and "status" in result:
            sv = result["status"].get("save_variables", {})
            variables = sv.get("variables", {})
            tc = result["status"].get("toolchanger", {})
            if "tool_number" in tc:
                self.active_tool = tc["tool_number"]

        # Print state is in the default subscription, so read from cached data.
        if self._printer:
            ps = self._printer.get_stat("print_stats")
            if ps:
                self._is_printing = ps.get("state") == "printing"
            # Seed filament sensor states from cached printer data.
            for n in range(tool_count):
                key = f"filament_switch_sensor filament_sensor_at_tool{n}"
                sensor = self._printer.get_stat(key)
                if sensor:
                    self.sensor_states[n] = sensor.get("filament_detected", False)

        # Map spool IDs from save_variables keys t0__spool_id, t1__spool_id, …
        spool_ids = {}
        for n in range(tool_count):
            sid = variables.get(f"t{n}__spool_id")
            if sid is not None:
                try:
                    sid_int = int(sid)
                    if sid_int > 0:
                        spool_ids[n] = sid_int
                except (ValueError, TypeError):
                    pass

        # Reset lane data; Spoolman details fill in below.
        self.lane_data = {
            str(n): {"name": "", "material": "", "vendor": "", "color": ""}
            for n in range(tool_count)
        }

        resolved = set()
        if spool_ids:
            spools = self._screen.spoolman_api.load_all_spools()
            if spools and isinstance(spools, list):
                spool_by_id = {s["id"]: s for s in spools if "id" in s}
                for n, sid in spool_ids.items():
                    if sid not in spool_by_id:
                        continue
                    fields = extract_spool_fields(spool_by_id[sid])
                    self.lane_data[str(n)] = {
                        "name":             fields["name"],
                        "material":         fields["material"],
                        "vendor":           fields["vendor"],
                        "color":            fields["color_hex"],
                        "spool_id":         fields["spool_id"],
                        "remaining_weight": fields["remaining_weight"],
                    }
                    resolved.add(n)

        # Fallback: lanes not resolved via Spoolman (no spool_id set, spool_id
        # set but Spoolman unreachable/deleted, or Spoolman not installed at
        # all) fall back to a manual color/material assignment made via the
        # filament_lanes_manual panel, stored as t{n}__manual. No API call
        # needed — the dict is used directly.
        for n in range(tool_count):
            if n in resolved:
                continue
            manual = variables.get(f"t{n}__manual")
            if isinstance(manual, dict):
                self.lane_data[str(n)] = {
                    "name":     manual.get("name", "") or "",
                    "material": manual.get("material", "") or "",
                    "vendor":   "",
                    "color":    manual.get("color", "") or "",
                }

        if changed_tool_count:
            self._build_ui()
        else:
            self._update_all_lanes()
        return False  # stop GLib.idle_add from repeating

    def _on_refresh_timer(self):
        self._fetch_data()
        return True  # keep timer running

    def _detect_tool_count(self):
        """Count tools by probing extruder objects (extruder, extruder1, …)."""
        if not self._printer:
            return 0
        if not self._printer.get_stat("extruder"):
            return 0
        count = 1
        while self._printer.get_stat(f"extruder{count}"):
            count += 1
        return count

    # ------------------------------------------------------------------ #
    # UI construction                                                      #
    # ------------------------------------------------------------------ #

    def _clear_content(self):
        for child in self.content.get_children():
            self.content.remove(child)
        self._col_wraps.clear()
        self._col_boxes.clear()
        self._active_indicators.clear()
        self._spool_images.clear()
        self._info_labels.clear()
        self._detail_labels.clear()
        self._id_labels.clear()
        self._unload_btns.clear()
        self._load_btns.clear()

    def _build_placeholder(self):
        self._clear_content()
        lbl = Gtk.Label(label=_("Loading spool data…"))
        lbl.set_valign(Gtk.Align.CENTER)
        lbl.set_halign(Gtk.Align.CENTER)
        self.content.pack_start(lbl, True, True, 0)
        self.content.show_all()

    def _build_ui(self):
        self._clear_content()

        if self.tool_count == 0:
            lbl = Gtk.Label(
                label=_("No tools detected.\n"
                        "Check that extruder objects are configured in Klipper.")
            )
            lbl.set_line_wrap(True)
            lbl.set_justify(Gtk.Justification.CENTER)
            lbl.set_valign(Gtk.Align.CENTER)
            self.content.pack_start(lbl, True, True, 0)
            self.content.show_all()
            return

        cols = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        cols.set_homogeneous(True)
        for n in range(self.tool_count):
            cols.pack_start(self._build_column(n), True, True, 0)

        self.content.add(cols)
        self.content.show_all()

        self._update_all_lanes()

    def _build_column(self, n):
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Active-tool indicator bar (coloured via CSS class)
        indicator = Gtk.Box()
        indicator.set_size_request(-1, 5)
        self._active_indicators[n] = indicator
        wrap.pack_start(indicator, False, False, 0)

        # Inner content box (opacity dimmed when slot is empty)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_start(4)
        box.set_margin_end(4)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        wrap.pack_start(box, True, True, 0)

        # Spool image — tapping fires a tool change (T0, T1, …)
        spool_btn = Gtk.Button()
        spool_btn.set_relief(Gtk.ReliefStyle.NONE)
        spool_btn.set_halign(Gtk.Align.CENTER)
        spool_btn.connect("clicked", self._on_spool_clicked, n)
        spool_img = Gtk.Image()
        spool_btn.add(spool_img)
        box.pack_start(spool_btn, False, False, 0)
        self._spool_images[n] = spool_img

        # Tool label
        tool_lbl = Gtk.Label()
        tool_lbl.set_markup(f"<b>T{n}</b>")
        box.pack_start(tool_lbl, False, False, 0)

        # Filament name
        info_lbl = Gtk.Label(label="")
        info_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        info_lbl.set_max_width_chars(12)
        info_lbl.set_halign(Gtk.Align.CENTER)
        box.pack_start(info_lbl, False, False, 0)
        self._info_labels[n] = info_lbl

        # Material · weight (e.g. "ABS · 1053 g")
        detail_lbl = Gtk.Label(label="")
        detail_lbl.set_halign(Gtk.Align.CENTER)
        detail_lbl.get_style_context().add_class("dim-label")
        box.pack_start(detail_lbl, False, False, 0)
        self._detail_labels[n] = detail_lbl

        # Spool ID (e.g. "#39")
        id_lbl = Gtk.Label(label="")
        id_lbl.set_halign(Gtk.Align.CENTER)
        id_lbl.get_style_context().add_class("dim-label")
        box.pack_start(id_lbl, False, False, 0)
        self._id_labels[n] = id_lbl

        # Assign spool button
        assign_btn = self._gtk.Button("filament", _("Assign"), "color1")
        assign_btn.set_vexpand(False)
        assign_btn.connect("clicked", self._on_assign_clicked, n)
        box.pack_start(assign_btn, False, False, 0)

        # Unload button
        unload_btn = self._gtk.Button("arrow-down", _("Unload"), "color2")
        unload_btn.set_vexpand(False)
        unload_btn.connect("clicked", self._on_unload_clicked, n)
        box.pack_start(unload_btn, False, False, 0)
        self._unload_btns[n] = unload_btn

        # Optional load button (hidden by default)
        if self.show_load_buttons:
            load_btn = self._gtk.Button("arrow-up", _("Load"), "color3")
            load_btn.connect("clicked", self._on_load_clicked, n)
            box.pack_start(load_btn, False, False, 0)
            self._load_btns[n] = load_btn

        self._col_wraps[n] = wrap
        self._col_boxes[n] = box
        return wrap

    # ------------------------------------------------------------------ #
    # State update helpers                                                 #
    # ------------------------------------------------------------------ #

    def _update_all_lanes(self):
        for n in range(self.tool_count):
            self._update_lane(n)
        self._update_active_indicator()
        self._update_print_buttons()

    def _update_lane(self, n):
        data = self.lane_data.get(str(n), {})
        color = data.get("color", "")
        name = data.get("name", "")
        material = data.get("material", "")
        spool_id = data.get("spool_id")
        remaining = data.get("remaining_weight")

        has_spool = bool(color or name or material)
        filament_detected = self.sensor_states.get(n, False)

        self._render_spool(n, color if has_spool else None)

        if has_spool:
            self._info_labels[n].set_text(name or "—")

            detail_parts = [p for p in [material] if p]
            if remaining is not None:
                detail_parts.append(f"{remaining:.0f} g")
            self._detail_labels[n].set_text(" · ".join(detail_parts))

            self._id_labels[n].set_text(f"#{spool_id}" if spool_id else "")
        else:
            self._info_labels[n].set_text(_("Empty"))
            self._detail_labels[n].set_text("")
            self._id_labels[n].set_text("")

        dim = not has_spool and not filament_detected
        self._col_boxes[n].set_opacity(0.35 if dim else 1.0)

    def _render_spool(self, n, color_hex):
        img = self._spool_images[n]

        # Scale spool icon to fit the column width.
        col_px = max(1, self.content.get_allocated_width() // max(1, self.tool_count))
        size = max(40, min(96, int(col_px * 0.55)))

        svg = self._spool_svg_template
        if color_hex:
            svg = svg.replace(b"var(--filament-color)", f"#{color_hex}".encode())
        else:
            svg = svg.replace(b"var(--filament-color)", b"#808080")

        try:
            loader = GdkPixbuf.PixbufLoader()
            loader.set_size(size, size)
            loader.write(svg)
            loader.close()
            img.set_from_pixbuf(loader.get_pixbuf())
            return
        except Exception as e:
            logger.warning("filament_lanes: spool render failed T%d: %s", n, e)

        # Fallback: KlipperScreen's built-in filament icon
        try:
            icon = self._gtk.Image("filament", size)
            if hasattr(icon, "get_pixbuf"):
                img.set_from_pixbuf(icon.get_pixbuf())
        except Exception:
            pass

    def _update_active_indicator(self):
        for n, indicator in self._active_indicators.items():
            ctx = indicator.get_style_context()
            if n == self.active_tool:
                ctx.add_class("lane-active-indicator")
            else:
                ctx.remove_class("lane-active-indicator")

    def _update_print_buttons(self):
        sensitive = not self._is_printing
        for btn in self._unload_btns.values():
            btn.set_sensitive(sensitive)
        for btn in self._load_btns.values():
            btn.set_sensitive(sensitive)

    # ------------------------------------------------------------------ #
    # KlipperScreen update callback                                        #
    # ------------------------------------------------------------------ #

    def process_update(self, action, data):
        if action != "notify_status_update":
            return

        # print_stats and filament sensors are in the default KS subscription.
        if "print_stats" in data:
            state = data["print_stats"].get("state")
            if state is not None:
                self._is_printing = state == "printing"
                self._update_print_buttons()

        for n in range(self.tool_count):
            key = f"filament_switch_sensor filament_sensor_at_tool{n}"
            if key in data:
                detected = data[key].get("filament_detected")
                if detected is not None:
                    self.sensor_states[n] = detected
                    self._update_lane(n)

    # ------------------------------------------------------------------ #
    # Panel lifecycle                                                      #
    # ------------------------------------------------------------------ #

    def activate(self):
        self._fetch_data()
        action_bar = self._screen.base_panel.action_bar
        for macro, label, icon, style in self._action_buttons:
            btn = self._gtk.Button(icon, _(label), style)
            btn.connect("clicked", self._on_action_btn_clicked, macro)
            action_bar.add(btn)
            self._extra_bar_btns.append(btn)
        action_bar.show_all()

    def deactivate(self):
        action_bar = self._screen.base_panel.action_bar
        for btn in self._extra_bar_btns:
            action_bar.remove(btn)
        self._extra_bar_btns.clear()

    # ------------------------------------------------------------------ #
    # Button handlers                                                      #
    # ------------------------------------------------------------------ #

    def _parse_action_buttons(self, cfg):
        """
        Reads action_button_1, action_button_2, … from KlipperScreen.conf.
        Each value is colon-separated:  MACRO:Label:icon:style
        icon and style are optional.  Falls back to built-in defaults if
        no action_button_N keys are present.

        Example KlipperScreen.conf entries:
            action_button_1: CLEAN_NOZZLE:Clean Nozzle:clean:color1
            action_button_2: UNSELECT_TOOL:Unselect Tool:toolchanger:color2
            action_button_3: MY_MACRO:My Button
        """
        _DEFAULTS = [
            ("CLEAN_NOZZLE",   _("Clean Nozzle"),   "fine-tune",   "color1"),
            ("UNSELECT_TOOL",  _("Unselect Tool"),  "toolchanger", "color2"),
        ]
        if not cfg:
            return _DEFAULTS

        buttons = []
        i = 1
        while True:
            val = cfg.get(f"action_button_{i}", "").strip()
            if not val:
                break
            parts = [p.strip() for p in val.split(":")]
            if len(parts) >= 2:
                macro = parts[0]
                label = parts[1]
                icon  = parts[2] if len(parts) > 2 else ""
                style = parts[3] if len(parts) > 3 else "color1"
                buttons.append((macro, label, icon, style))
            else:
                logger.warning("filament_lanes: ignoring malformed action_button_%d: %r", i, val)
            i += 1

        return buttons if buttons else _DEFAULTS

    def _on_action_btn_clicked(self, widget, macro):
        self._screen._send_action(widget, "printer.gcode.script",
                                  {"script": macro})

    def _on_spool_clicked(self, widget, n):
        self._screen._send_action(widget, "printer.gcode.script",
                                  {"script": f"T{n}"})

    def _on_assign_clicked(self, widget, n):
        current_name = self.lane_data.get(str(n), {}).get("name", "")

        if getattr(self._screen, "spoolman_api", None) is None:
            # No Spoolman configured at all — go straight to manual entry.
            self._open_manual_assign(n, current_name)
            return

        # Spoolman is available — let the user choose per lane rather than
        # forcing everyone through Spoolman (or vice versa).
        dialog = Gtk.Dialog(
            title=_("Assign Spool"),
            transient_for=self.get_toplevel(),
            flags=Gtk.DialogFlags.MODAL,
        )
        dialog.add_button(_("Browse Spoolman"), 1)
        dialog.add_button(_("Manual Entry"), 2)
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        box = dialog.get_content_area()
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.add(Gtk.Label(label=_(f"How do you want to assign T{n}?")))
        box.show_all()

        response = dialog.run()
        dialog.destroy()

        if response == 1:
            self._open_spoolman_assign(n, current_name)
        elif response == 2:
            self._open_manual_assign(n, current_name)

    def _open_spoolman_assign(self, n, current_name):
        self._screen.show_panel(
            "filament_lanes_spoolman",
            panel_name=f"filament_lanes_spoolman_{n}",
            title=_(f"Assign Spool — T{n}"),
            lane=n,
            current_name=current_name,
        )

    def _open_manual_assign(self, n, current_name):
        self._screen.show_panel(
            "filament_lanes_manual",
            panel_name=f"filament_lanes_manual_{n}",
            title=_(f"Manual Assign — T{n}"),
            lane=n,
            current_name=current_name,
        )

    def _on_unload_clicked(self, widget, n):
        self._screen._send_action(widget, "printer.gcode.script",
                                  {"script": f"UNLOAD_ANY_TOOL T={n} D=1400 S=30"})

    def _on_load_clicked(self, widget, n):
        self._screen._send_action(widget, "printer.gcode.script",
                                  {"script": f"LOAD_ANY_TOOL_DIST T={n} S=30 D=1400"})
