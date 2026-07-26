import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gdk
import logging
import os

from panels.base_panel import ScreenPanel
from panels.touch_picker import pick_from_list

logger = logging.getLogger("KlipperScreen")

MATERIALS_FILE = os.path.expanduser("~/printer_data/config/materials.cfg")
DEFAULT_MATERIALS = ["PLA", "PETG", "ABS", "ASA", "TPU", "Nylon", "PC", "Other"]

# Common filament colors offered as one-tap presets. "Custom…" (added
# separately in the UI) covers anything not in this list.
PRESET_COLORS = [
    ("Black", "000000"),
    ("White", "FFFFFF"),
    ("Gray", "808080"),
    ("Red", "E53935"),
    ("Orange", "FB8C00"),
    ("Yellow", "FDD835"),
    ("Green", "43A047"),
    ("Blue", "1E88E5"),
    ("Purple", "8E24AA"),
    ("Pink", "EC407A"),
    ("Brown", "6D4C41"),
    ("Silver", "BDBDBD"),
]


def create_panel(*args, **kwargs):
    return Panel(*args, **kwargs)


def _load_materials():
    if not os.path.isfile(MATERIALS_FILE):
        return list(DEFAULT_MATERIALS)
    materials = []
    try:
        with open(MATERIALS_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                materials.append(line)
    except OSError as exc:
        logger.warning("filament_lanes_manual: could not read %s: %s", MATERIALS_FILE, exc)
        return list(DEFAULT_MATERIALS)
    return materials or list(DEFAULT_MATERIALS)


class Panel(ScreenPanel):
    """
    Manual color/material assignment, opened from the Filament Lanes panel
    for lanes not tracked in Spoolman (or when Spoolman isn't installed at
    all). Writes directly to Klipper's save_variables — no Spoolman API
    calls — under a separate t{n}__manual key so it never conflicts with a
    Spoolman-based assignment (see filament_lanes_spoolman.py's _assign(),
    which clears this key on a Spoolman assignment, and this panel's own
    _save(), which clears t{n}__spool_id in turn).
    """

    def __init__(self, screen, title, lane=0, current_name="", **kwargs):
        title = title or _("Manual Assign")
        super().__init__(screen, title)

        self.lane = lane
        self.current_name = current_name
        self.selected_color = None  # hex string, no '#'
        self._other_entry = None

        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI construction                                                      #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_start(12)
        root.set_margin_end(12)
        root.set_margin_top(12)
        root.set_margin_bottom(12)

        lane_lbl = Gtk.Label()
        lane_lbl.set_markup(f"<b>T{self.lane}</b> — {_('Manual Assignment')}")
        lane_lbl.set_halign(Gtk.Align.START)
        root.pack_start(lane_lbl, False, False, 0)

        # ── Material picker ─────────────────────────────────────────────
        root.pack_start(Gtk.Label(label=_("Material"), halign=Gtk.Align.START), False, False, 0)

        # A tap-to-open list dialog, not Gtk.ComboBoxText — see
        # touch_picker.py for why ComboBoxText's popup doesn't survive a
        # touchscreen tap under KlipperScreen's window-manager-less Xorg.
        self._material_options = _load_materials()
        self._material_idx = 0
        self._material_btn = Gtk.Button(label=self._material_options[0])
        self._material_btn.set_halign(Gtk.Align.FILL)
        self._material_btn.connect("clicked", self._on_pick_material)
        root.pack_start(self._material_btn, True, True, 0)

        self._other_entry = Gtk.Entry()
        self._other_entry.set_placeholder_text(_("Enter material"))
        self._other_entry.set_no_show_all(True)
        self._other_entry.hide()
        root.pack_start(self._other_entry, False, False, 0)

        # ── Color presets ───────────────────────────────────────────────
        root.pack_start(Gtk.Label(label=_("Color"), halign=Gtk.Align.START), False, False, 0)

        grid = Gtk.FlowBox()
        grid.set_selection_mode(Gtk.SelectionMode.NONE)
        grid.set_max_children_per_line(6)
        for name, color_hex in PRESET_COLORS:
            btn = self._build_swatch_button(name, color_hex)
            grid.add(btn)
        root.pack_start(grid, False, False, 0)

        custom_btn = self._gtk.Button("color1", _("Custom…"), "color2")
        custom_btn.connect("clicked", self._on_custom_color_clicked)
        root.pack_start(custom_btn, False, False, 0)

        self._color_preview = Gtk.DrawingArea()
        self._color_preview.set_size_request(36, 36)
        self._color_preview.connect("draw", self._draw_preview)
        preview_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        preview_box.pack_start(Gtk.Label(label=_("Selected:")), False, False, 0)
        preview_box.pack_start(self._color_preview, False, False, 0)
        root.pack_start(preview_box, False, False, 0)

        # ── Optional name ───────────────────────────────────────────────
        root.pack_start(Gtk.Label(label=_("Name (optional)"), halign=Gtk.Align.START), False, False, 0)
        self._name_entry = Gtk.Entry()
        self._name_entry.set_text(self.current_name or "")
        self._name_entry.set_placeholder_text(_("e.g. Red PLA"))
        root.pack_start(self._name_entry, False, False, 0)

        # ── Actions ──────────────────────────────────────────────────────
        route_btn = self._gtk.Button("toolchanger", _("Map / Failover"), "color3")
        route_btn.set_size_request(-1, 34)
        route_btn.connect("clicked", self._on_routing_clicked)
        root.pack_start(route_btn, False, False, 0)

        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        clear_btn = self._gtk.Button("cancel", _("Clear"), "color2")
        clear_btn.set_size_request(-1, 34)
        clear_btn.connect("clicked", self._on_clear)
        action_box.pack_start(clear_btn, True, True, 0)

        save_btn = self._gtk.Button("complete", _("Save"), "color1")
        save_btn.connect("clicked", self._on_save)
        action_box.pack_start(save_btn, True, True, 0)
        root.pack_start(action_box, False, False, 0)

        # This panel has more controls (material, color grid, name, routing,
        # clear/save) than fit in KlipperScreen's 480px screen height at
        # once — without scrolling, Name/Map-Failover/Clear/Save all land
        # off-screen and are untappable. Same fix pattern as the toolmap
        # diagram's horizontal scroll in filament_lanes_toolmap.py.
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(root)

        self.content.pack_start(scroll, True, True, 0)
        self.content.show_all()

        # Default selection: first preset, so Save always has a valid color.
        self.selected_color = PRESET_COLORS[0][1]

    def _build_swatch_button(self, name, color_hex):
        btn = Gtk.Button()
        btn.set_tooltip_text(name)
        area = Gtk.DrawingArea()
        area.set_size_request(36, 36)
        r, g, b = self._hex_to_rgb(color_hex)
        area.connect("draw", self._draw_swatch, r, g, b)
        btn.add(area)
        btn.connect("clicked", self._on_preset_clicked, color_hex)
        return btn

    # ------------------------------------------------------------------ #
    # Color helpers                                                        #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _hex_to_rgb(color_hex):
        try:
            r = int(color_hex[0:2], 16) / 255.0
            g = int(color_hex[2:4], 16) / 255.0
            b = int(color_hex[4:6], 16) / 255.0
        except (ValueError, IndexError):
            r = g = b = 0.5
        return r, g, b

    @staticmethod
    def _draw_swatch(widget, cr, r, g, b):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        radius = min(w, h) / 2.0
        cr.arc(w / 2, h / 2, radius - 1, 0, 2 * 3.14159)
        cr.set_source_rgb(r, g, b)
        cr.fill_preserve()
        cr.set_source_rgb(0.2, 0.2, 0.2)
        cr.set_line_width(1.5)
        cr.stroke()

    def _draw_preview(self, widget, cr):
        r, g, b = self._hex_to_rgb(self.selected_color or "808080")
        self._draw_swatch(widget, cr, r, g, b)

    def _on_preset_clicked(self, widget, color_hex):
        self.selected_color = color_hex
        self._color_preview.queue_draw()

    def _on_custom_color_clicked(self, widget):
        dialog = Gtk.ColorChooserDialog(title=_("Choose a color"), transient_for=self._screen)
        if self.selected_color:
            r, g, b = self._hex_to_rgb(self.selected_color)
            rgba = Gdk.RGBA(r, g, b, 1.0)
            dialog.set_rgba(rgba)
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            rgba = dialog.get_rgba()
            self.selected_color = "{:02X}{:02X}{:02X}".format(
                round(rgba.red * 255), round(rgba.green * 255), round(rgba.blue * 255)
            )
            self._color_preview.queue_draw()
        dialog.destroy()

    # ------------------------------------------------------------------ #
    # Material picker                                                       #
    # ------------------------------------------------------------------ #

    def _on_pick_material(self, widget):
        idx = pick_from_list(self._screen, _("Material"), self._material_options, self._material_idx)
        if idx is not None:
            self._material_idx = idx
            self._material_btn.set_label(self._material_options[idx])
            self._on_material_changed()

    def _on_material_changed(self):
        is_other = self._material_options[self._material_idx] == "Other"
        self._other_entry.set_visible(is_other)
        self._other_entry.set_no_show_all(not is_other)
        if is_other:
            self._other_entry.show()

    def _selected_material(self):
        text = self._material_options[self._material_idx] if self._material_options else ""
        if text == "Other":
            return self._other_entry.get_text().strip()
        return text

    # ------------------------------------------------------------------ #
    # Actions                                                              #
    # ------------------------------------------------------------------ #

    def _on_clear(self, widget):
        script = f"SAVE_VARIABLE VARIABLE=t{self.lane}__manual VALUE=None"
        self._screen._send_action(None, "printer.gcode.script", {"script": script})
        self._screen._menu_go_back()

    def _on_routing_clicked(self, widget):
        self._screen.show_panel(
            "filament_lanes_routing",
            panel_name=f"filament_lanes_routing_{self.lane}",
            title=_(f"T{self.lane} Routing"),
            lane=self.lane,
        )

    @staticmethod
    def _gcode_value(py_value):
        # Klipper's extended-param parser (gcode.py's _get_extended_params)
        # tokenizes the line with shlex before SAVE_VARIABLE's own
        # ast.literal_eval — a bare dict repr()'s spaces/quotes split into
        # bogus tokens ("Malformed command"). A shlex-escaped double-quote
        # wrapper makes the whole repr one token again.
        text = repr(py_value)
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return '"' + escaped + '"'

    def _on_save(self, widget):
        material = self._selected_material()
        if not material:
            logger.warning("filament_lanes_manual: no material selected/entered, not saving")
            return

        entry = {
            "color": self.selected_color or "808080",
            "material": material,
            "name": self._name_entry.get_text().strip(),
        }

        # Mirrors filament_lanes_spoolman.py's _assign() gcode shape exactly
        # (SET_GCODE_VARIABLE + SAVE_VARIABLE), but writes t{n}__manual
        # instead of t{n}__spool_id, and clears spool_id/spool_id-variable
        # so a stale Spoolman assignment can't take precedence.
        script = (
            f"SAVE_VARIABLE VARIABLE=t{self.lane}__manual VALUE={self._gcode_value(entry)}\n"
            f"SAVE_VARIABLE VARIABLE=t{self.lane}__spool_id VALUE=None\n"
            f"SET_GCODE_VARIABLE MACRO=T{self.lane} VARIABLE=spool_id VALUE=None"
        )
        self._screen._send_action(None, "printer.gcode.script", {"script": script})
        self._screen._menu_go_back()
