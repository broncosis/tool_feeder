import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
import logging

from panels.base_panel import ScreenPanel
from panels.tool_routing import detect_tool_count, fetch_toolmap_state, reset_toolmap
from panels.sidebar_declutter import hide_extra_icons, show_extra_icons

logger = logging.getLogger("KlipperScreen")

# Layout constants for the drawn diagram. Box width/gap scale down as
# tool_count grows (see _build_ui) so the whole diagram always fits within
# KlipperScreen's 800x480 screen with no scrolling — some touchscreens
# handle scrolling poorly, so this trades box size for guaranteed fit
# rather than the other way around.
#
# The content area is NOT the full 800px screen width — base_panel.py's
# vertical-sidebar layout puts the action_bar in its own column sized to
# action_bar_width = screen_width * 0.1 (80px on an 800px screen), with
# content occupying the rest. 750 (assuming the full screen was available)
# was still ~90px too wide; 660 leaves headroom for that 80px sidebar
# column plus this panel's own left/right margins.
DIAGRAM_MAX_W = 660
MIN_BOX_W = 46
DEFAULT_BOX_W = 110
BOX_H = 56
DEFAULT_GAP = 30
MIN_GAP = 10
LEFT_MARGIN = 10
ARROW_STEP = 8
ARROWHEAD = 7

FIL_OK_FILL = (0.83, 0.97, 0.87)
FIL_OK_BORDER = (0.18, 0.60, 0.32)
FIL_OUT_FILL = (0.98, 0.85, 0.86)
FIL_OUT_BORDER = (0.75, 0.20, 0.20)
MAP_ARROW = (0.20, 0.45, 0.85)
BACKUP_ARROW = (0.85, 0.55, 0.10)
TEXT_COLOR = (0.10, 0.10, 0.10)


def create_panel(*args, **kwargs):
    return Panel(*args, **kwargs)


class Panel(ScreenPanel):
    """
    Visual overview of every tool's current mapping/backup/filament status
    at once — a diagram equivalent of the console's SHOW_TOOLMAP table.
    Reachable via the "Tool Map" sidebar button, present anywhere in the
    Filament section (added in filament_lanes.py's activate()) — not tied
    to any specific lane's Routing page.
    Also offers Reset All (RESET_TOOLMAP), the one macro with no other UI
    exposure — this all-tools screen is the natural place for an all-tools
    action.
    """

    def __init__(self, screen, title, tool_count=0, **kwargs):
        title = title or _("Tool Map")
        super().__init__(screen, title)

        self.tool_count = tool_count or detect_tool_count(self)
        self.tool_map = {}
        self.backup_map = {}
        self.filament_status = {}

        self._build_ui()
        GLib.idle_add(self._fetch_state)

    # ------------------------------------------------------------------ #
    # Panel lifecycle                                                      #
    # ------------------------------------------------------------------ #

    def activate(self):
        hide_extra_icons(self._screen)

    def deactivate(self):
        show_extra_icons(self._screen)

    # ------------------------------------------------------------------ #
    # UI construction                                                      #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_margin_start(12)
        root.set_margin_end(12)
        root.set_margin_top(12)
        root.set_margin_bottom(12)

        if self.tool_count == 0:
            root.pack_start(
                Gtk.Label(label=_("No tools detected.")), False, False, 0)
            self.content.pack_start(root, True, True, 0)
            self.content.show_all()
            return

        # Shrink box width/gap (down to a readable floor) so N boxes always
        # fit within DIAGRAM_MAX_W — avoids ever needing horizontal scroll.
        n = self.tool_count
        avail = DIAGRAM_MAX_W - 2 * LEFT_MARGIN
        self._box_w = max(MIN_BOX_W, min(DEFAULT_BOX_W,
                           (avail - (n - 1) * MIN_GAP) / n))
        gap_budget = avail - n * self._box_w
        self._gap = max(MIN_GAP, min(DEFAULT_GAP, gap_budget / (n - 1))) if n > 1 else 0
        self._font_size = 20 if self._box_w >= 70 else 15

        top_margin = self._arc_margin()
        bottom_margin = self._arc_margin()
        diagram_w = 2 * LEFT_MARGIN + n * self._box_w + (n - 1) * self._gap
        diagram_h = top_margin + BOX_H + bottom_margin
        self._top_margin = top_margin
        self._bottom_margin = bottom_margin

        self._drawing = Gtk.DrawingArea()
        self._drawing.set_size_request(int(diagram_w), int(diagram_h))
        self._drawing.connect("draw", self._on_draw)
        root.pack_start(self._drawing, False, False, 0)

        legend = Gtk.Label()
        legend.set_markup(
            "<small>"
            + _("Green/red box = filament OK/OUT  ·  ")
            + _("blue arc above = maps to  ·  ")
            + _("orange dashed arc below = backup")
            + "</small>"
        )
        # set_line_wrap(True) alone doesn't actually wrap: a Gtk.Label still
        # requests its full unwrapped width as its natural size unless
        # something caps it, and that request was overflowing the 800px
        # screen (dragging the diagram/titlebar along with it since nothing
        # downstream shrinks below a widget's natural request). Capping the
        # width to the diagram's own computed width forces real wrapping.
        legend.set_line_wrap(True)
        legend.set_size_request(int(diagram_w), -1)
        legend.set_max_width_chars(1)
        legend.get_style_context().add_class("dim-label")
        root.pack_start(legend, False, False, 0)

        reset_btn = self._gtk.Button("refresh", _("Reset All"), "color2")
        reset_btn.connect("clicked", self._on_reset_all)
        root.pack_start(reset_btn, False, False, 0)

        self.content.pack_start(root, True, True, 0)
        self.content.show_all()

    def _arc_margin(self):
        # Taller drawing area for more tools, since arcs between distant
        # boxes need more headroom to stay visually distinct from nearby
        # ones — capped low enough that the whole page still fits KlipperScreen's
        # 480px height alongside the legend/reset button, no scroll needed.
        return min(60, 24 + ARROW_STEP * max(1, self.tool_count - 1))

    # ------------------------------------------------------------------ #
    # Data                                                                 #
    # ------------------------------------------------------------------ #

    def _fetch_state(self):
        def _handle(tool_map, backup_map, filament_status):
            self.tool_map = tool_map
            self.backup_map = backup_map
            self.filament_status = filament_status
            self._drawing.queue_draw()
        fetch_toolmap_state(self._screen, _handle)
        return False  # stop GLib.idle_add from repeating

    # ------------------------------------------------------------------ #
    # Diagram drawing                                                      #
    # ------------------------------------------------------------------ #

    def _box_rect(self, n):
        x = LEFT_MARGIN + n * (self._box_w + self._gap)
        y = self._top_margin
        return x, y, self._box_w, BOX_H

    def _on_draw(self, widget, cr):
        for n in range(self.tool_count):
            x, y, w, h = self._box_rect(n)
            has_fil = self.filament_status.get(n, True)
            fill = FIL_OK_FILL if has_fil else FIL_OUT_FILL
            border = FIL_OK_BORDER if has_fil else FIL_OUT_BORDER

            cr.set_source_rgb(*fill)
            cr.rectangle(x, y, w, h)
            cr.fill_preserve()
            cr.set_source_rgb(*border)
            cr.set_line_width(2)
            cr.rectangle(x, y, w, h)
            cr.stroke()

            cr.set_source_rgb(*TEXT_COLOR)
            cr.select_font_face("sans-serif")
            cr.set_font_size(self._font_size)
            label = f"T{n}"
            extents = cr.text_extents(label)
            cr.move_to(x + w / 2 - extents.width / 2, y + h / 2 + extents.height / 2)
            cr.show_text(label)

        for n in range(self.tool_count):
            target = self.tool_map.get(n, n)
            if target != n and 0 <= target < self.tool_count:
                self._draw_arc(cr, n, target, above=True,
                                color=MAP_ARROW, dashed=False)

            backup = self.backup_map.get(n)
            if backup is not None and 0 <= backup < self.tool_count:
                self._draw_arc(cr, n, backup, above=False,
                                color=BACKUP_ARROW, dashed=True)

        return False

    def _draw_arc(self, cr, n, m, above, color, dashed):
        x1, y1, w1, h1 = self._box_rect(n)
        x2, y2, w2, h2 = self._box_rect(m)
        cx1 = x1 + w1 / 2
        cx2 = x2 + w2 / 2
        distance = abs(m - n)
        arc_height = min(
            (self._top_margin if above else self._bottom_margin) - 10,
            15 + ARROW_STEP * distance,
        )

        if above:
            edge_y = y1
            peak_y = edge_y - arc_height
        else:
            edge_y = y1 + h1
            peak_y = edge_y + arc_height

        cr.save()
        cr.set_source_rgb(*color)
        cr.set_line_width(2.5)
        if dashed:
            cr.set_dash([6, 4])
        else:
            cr.set_dash([])

        cr.move_to(cx1, edge_y)
        cr.curve_to(cx1, peak_y, cx2, peak_y, cx2, edge_y)
        cr.stroke()

        # Fixed-orientation arrowhead: the curve's control points sit
        # directly above/below both endpoints, so it always arrives at the
        # target perpendicular to the box edge — down into a top edge, up
        # into a bottom edge — regardless of arc direction or distance.
        cr.set_dash([])
        tip_y = edge_y + (ARROWHEAD if above else -ARROWHEAD)
        cr.move_to(cx2 - ARROWHEAD, tip_y)
        cr.line_to(cx2 + ARROWHEAD, tip_y)
        cr.line_to(cx2, edge_y)
        cr.close_path()
        cr.fill()
        cr.restore()

    # ------------------------------------------------------------------ #
    # Actions                                                              #
    # ------------------------------------------------------------------ #

    def _on_reset_all(self, widget):
        dialog = Gtk.MessageDialog(
            transient_for=self._screen,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            text=_("Reset all tool mapping and backups to identity?"),
        )
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.YES:
            return

        reset_toolmap(self._screen)
        GLib.timeout_add(300, self._fetch_state)
