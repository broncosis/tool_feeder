import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
import logging

from panels.base_panel import ScreenPanel
from panels.spoolman_common import extract_spool_fields

logger = logging.getLogger("KlipperScreen")


def create_panel(*args, **kwargs):
    return Panel(*args, **kwargs)


class Panel(ScreenPanel):
    """
    Spoolman spool browser opened from the Filament Lanes panel.
    Lets the user pick a spool to assign to a specific lane, or clear the
    current assignment.  On selection, writes the spool_id to Klipper's
    save_variables via SAVE_VARIABLE gcode so it persists across restarts.
    """

    def __init__(self, screen, title, lane=0, current_name="", **kwargs):
        title = title or _("Assign Spool")
        super().__init__(screen, title)

        self.lane = lane
        self.current_name = current_name
        self.spools = []

        self._build_ui()
        GLib.idle_add(self._fetch_spools)

    # ------------------------------------------------------------------ #
    # UI construction                                                      #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        # Two-column layout: left = lane info + clear button, right = spool list
        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        # ── Left panel ────────────────────────────────────────────────────
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        left.set_margin_start(8)
        left.set_margin_end(8)
        left.set_margin_top(8)
        left.set_size_request(160, -1)

        cur_name = self.current_name or _("None")
        lane_lbl = Gtk.Label()
        lane_lbl.set_markup(f"<b>T{self.lane}</b>")
        lane_lbl.set_halign(Gtk.Align.START)
        left.pack_start(lane_lbl, False, False, 0)

        cur_lbl = Gtk.Label(label=cur_name)
        cur_lbl.set_halign(Gtk.Align.START)
        cur_lbl.set_line_wrap(True)
        cur_lbl.get_style_context().add_class("dim-label")
        left.pack_start(cur_lbl, False, False, 0)

        clear_btn = self._gtk.Button("cancel", _("Clear"), "color2")
        clear_btn.set_size_request(-1, 34)
        clear_btn.connect("clicked", self._on_clear)
        left.pack_start(clear_btn, False, False, 0)

        route_btn = self._gtk.Button("toolchanger", _("Map / Failover"), "color3")
        route_btn.set_size_request(-1, 34)
        route_btn.connect("clicked", self._on_routing_clicked)
        left.pack_start(route_btn, False, False, 0)

        root.pack_start(left, False, False, 0)

        # Vertical separator between columns
        root.pack_start(
            Gtk.Separator(orientation=Gtk.Orientation.VERTICAL),
            False, False, 0
        )

        # ── Right panel: scrollable spool list ────────────────────────────
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._listbox.set_activate_on_single_click(True)
        self._listbox.connect("row-activated", self._on_row_activated)
        scroll.add(self._listbox)
        root.pack_start(scroll, True, True, 0)

        # Loading spinner shown until fetch completes
        self._spinner_row = Gtk.ListBoxRow()
        self._spinner_row.set_activatable(False)
        spinner_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        spinner_box.set_halign(Gtk.Align.CENTER)
        spinner_box.set_margin_top(16)
        spinner = Gtk.Spinner()
        spinner.start()
        spinner_box.pack_start(spinner, False, False, 0)
        spinner_box.pack_start(Gtk.Label(label=_("Loading spools…")), False, False, 0)
        self._spinner_row.add(spinner_box)
        self._listbox.add(self._spinner_row)

        self.content.pack_start(root, True, True, 0)
        self.content.show_all()

    # ------------------------------------------------------------------ #
    # Data fetch                                                           #
    # ------------------------------------------------------------------ #

    def _fetch_spools(self):
        # load_all_spools() is async (a Moonraker JSON-RPC call under the
        # hood) — it returns immediately with None, the real list arrives
        # later via this callback.
        def _handle_spools(spools):
            if not spools or not isinstance(spools, list):
                logger.warning("filament_lanes_spoolman: spool fetch returned nothing")
                GLib.idle_add(self._show_error, _("Could not fetch spool list."))
                return
            self.spools = spools
            GLib.idle_add(self._populate_list)

        self._screen.spoolman_api.load_all_spools(callback=_handle_spools)

    def _populate_list(self):
        self._listbox.remove(self._spinner_row)

        if not self.spools:
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            lbl = Gtk.Label(label=_("No spools found in Spoolman."))
            lbl.set_margin_top(12)
            row.add(lbl)
            self._listbox.add(row)
            self._listbox.show_all()
            return

        for spool in self.spools:
            row = self._build_spool_row(spool)
            if row:
                self._listbox.add(row)

        self._listbox.show_all()

    def _build_spool_row(self, spool):
        fields = extract_spool_fields(spool)
        name = fields["name"] or _("Unknown")
        material = fields["material"] or ""
        vendor = fields["vendor"] or ""
        color_hex = fields["color_hex"] or ""
        spool_id = fields["spool_id"]

        if spool_id is None:
            return None

        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(6)
        box.set_margin_end(6)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        # Color swatch
        swatch = Gtk.DrawingArea()
        swatch.set_size_request(28, 28)
        try:
            r = int(color_hex[0:2], 16) / 255.0 if color_hex else 0.5
            g = int(color_hex[2:4], 16) / 255.0 if color_hex else 0.5
            b = int(color_hex[4:6], 16) / 255.0 if color_hex else 0.5
        except (ValueError, IndexError):
            r = g = b = 0.5
        swatch.connect("draw", self._draw_swatch, r, g, b)
        box.pack_start(swatch, False, False, 0)

        # Text: name + material · vendor
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        name_lbl = Gtk.Label()
        name_lbl.set_markup(f"<b>{GLib.markup_escape_text(name)}</b>")
        name_lbl.set_halign(Gtk.Align.START)
        text_box.pack_start(name_lbl, False, False, 0)

        sub_parts = [p for p in [material, vendor] if p]
        sub_parts.append(f"#{spool_id}")
        sub_lbl = Gtk.Label(label=" · ".join(sub_parts))
        sub_lbl.set_halign(Gtk.Align.START)
        sub_lbl.get_style_context().add_class("dim-label")
        text_box.pack_start(sub_lbl, False, False, 0)

        box.pack_start(text_box, True, True, 0)
        row.add(box)

        row._spool_id = spool_id
        return row

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

    def _show_error(self, message):
        self._listbox.remove(self._spinner_row)
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        lbl = Gtk.Label(label=message)
        lbl.set_line_wrap(True)
        lbl.set_margin_top(12)
        row.add(lbl)
        self._listbox.add(row)
        self._listbox.show_all()

    # ------------------------------------------------------------------ #
    # Actions                                                              #
    # ------------------------------------------------------------------ #

    def _on_row_activated(self, listbox, row):
        self._assign(row._spool_id)

    def _on_clear(self, widget):
        self._assign(0)  # 0 = no spool assigned

    def _on_routing_clicked(self, widget):
        self._screen.show_panel(
            "filament_lanes_routing",
            panel_name=f"filament_lanes_routing_{self.lane}",
            title=_(f"T{self.lane} Routing"),
            lane=self.lane,
        )

    def _assign(self, spool_id):
        # Write to Klipper save_variables so the assignment persists across
        # restarts and is the same source the main panel reads from.
        # Also mirrors what Klipper macros do when scanning a spool tag.
        # Clears t{n}__manual so a prior manual assignment (see
        # filament_lanes_manual.py) doesn't linger once this lane is tracked
        # in Spoolman instead — the two modes are mutually exclusive per lane.
        script = (
            f"SET_GCODE_VARIABLE MACRO=T{self.lane} VARIABLE=spool_id VALUE={spool_id}\n"
            f"SAVE_VARIABLE VARIABLE=t{self.lane}__spool_id VALUE={spool_id}\n"
            f"SAVE_VARIABLE VARIABLE=t{self.lane}__manual VALUE=None"
        )
        self._screen._send_action(None, "printer.gcode.script", {"script": script})
        self._screen._menu_go_back()
