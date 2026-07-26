import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
import logging

from panels.base_panel import ScreenPanel
from panels.tool_routing import detect_tool_count, fetch_toolmap_state, apply_toolmap
from panels.touch_picker import pick_from_list

logger = logging.getLogger("KlipperScreen")


def create_panel(*args, **kwargs):
    return Panel(*args, **kwargs)


class Panel(ScreenPanel):
    """
    Per-lane tool remapping/failover control, opened from either assign
    screen's "Map / Failover" button. Lets the user change which physical
    tool T{lane} actually selects (SET_TOOLMAP's EFF) and which tool to fall
    back to if that one is unavailable (BACKUP) — the same state
    SET_TOOLMAP/SHOW_TOOLMAP/RESET_TOOLMAP already manage from the console
    (see tool_feeder_macros.cfg), just exposed on-screen.
    """

    def __init__(self, screen, title, lane=0, tool_count=0, **kwargs):
        title = title or _(f"T{lane} Routing")
        super().__init__(screen, title)

        self.lane = lane
        self.tool_count = tool_count or detect_tool_count(self)

        self._build_ui()
        GLib.idle_add(self._fetch_state)

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
        lane_lbl.set_markup(f"<b>T{self.lane}</b> — {_('Tool Routing')}")
        lane_lbl.set_halign(Gtk.Align.START)
        root.pack_start(lane_lbl, False, False, 0)

        self._status_lbl = Gtk.Label(label=_("Loading current mapping…"))
        self._status_lbl.set_halign(Gtk.Align.START)
        self._status_lbl.set_line_wrap(True)
        self._status_lbl.get_style_context().add_class("dim-label")
        root.pack_start(self._status_lbl, False, False, 0)

        if self.tool_count == 0:
            root.pack_start(
                Gtk.Label(label=_("No tools detected.")), False, False, 0)
            self.content.pack_start(root, True, True, 0)
            self.content.show_all()
            return

        # ── Maps to ─────────────────────────────────────────────────────
        # A tap-to-open list dialog, not Gtk.ComboBoxText — see
        # touch_picker.py for why ComboBoxText's popup doesn't survive a
        # touchscreen tap under KlipperScreen's window-manager-less Xorg.
        self._target_options = [f"T{n}" for n in range(self.tool_count)]
        self._target_idx = self.lane
        root.pack_start(Gtk.Label(label=_("Maps to"), halign=Gtk.Align.START), False, False, 0)
        self._target_btn = Gtk.Button(label=self._target_options[self._target_idx])
        self._target_btn.set_halign(Gtk.Align.FILL)
        self._target_btn.connect("clicked", self._on_pick_target)
        root.pack_start(self._target_btn, True, True, 0)

        # ── Backup ──────────────────────────────────────────────────────
        self._backup_options = [_("None")] + [f"T{n}" for n in range(self.tool_count)]
        self._backup_idx = 0
        root.pack_start(Gtk.Label(label=_("Backup"), halign=Gtk.Align.START), False, False, 0)
        self._backup_btn = Gtk.Button(label=self._backup_options[self._backup_idx])
        self._backup_btn.set_halign(Gtk.Align.FILL)
        self._backup_btn.connect("clicked", self._on_pick_backup)
        root.pack_start(self._backup_btn, True, True, 0)

        apply_btn = self._gtk.Button("complete", _("Apply"), "color1")
        apply_btn.connect("clicked", self._on_apply)
        root.pack_start(apply_btn, False, False, 0)

        root.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)

        overview_btn = self._gtk.Button("toolchanger", _("Tool Map Overview"), "color2")
        overview_btn.connect("clicked", self._on_overview_clicked)
        root.pack_start(overview_btn, False, False, 0)

        self.content.pack_start(root, True, True, 0)
        self.content.show_all()

    # ------------------------------------------------------------------ #
    # Data                                                                 #
    # ------------------------------------------------------------------ #

    def _fetch_state(self):
        def _handle(tool_map, backup_map, filament_status):
            self._render_state(tool_map, backup_map, filament_status)
        fetch_toolmap_state(self._screen, _handle)
        return False  # stop GLib.idle_add from repeating

    def _render_state(self, tool_map, backup_map, filament_status):
        target = tool_map.get(self.lane, self.lane)
        backup = backup_map.get(self.lane)
        has_filament = filament_status.get(self.lane, True)

        if self.tool_count:
            if 0 <= target < self.tool_count:
                self._target_idx = target
                self._target_btn.set_label(self._target_options[target])
            backup_idx = (backup + 1) if backup is not None else 0
            if 0 <= backup_idx < len(self._backup_options):
                self._backup_idx = backup_idx
                self._backup_btn.set_label(self._backup_options[backup_idx])

        backup_text = f"T{backup}" if backup is not None else _("none")
        fil_text = _("OK") if has_filament else _("OUT")
        self._status_lbl.set_text(
            _(f"Currently: T{self.lane} → T{target}  ·  backup: {backup_text}  ·  filament: {fil_text}")
        )

    # ------------------------------------------------------------------ #
    # Actions                                                              #
    # ------------------------------------------------------------------ #

    def _on_pick_target(self, widget):
        idx = pick_from_list(self._screen, _("Maps to"), self._target_options, self._target_idx)
        if idx is not None:
            self._target_idx = idx
            self._target_btn.set_label(self._target_options[idx])

    def _on_pick_backup(self, widget):
        idx = pick_from_list(self._screen, _("Backup"), self._backup_options, self._backup_idx)
        if idx is not None:
            self._backup_idx = idx
            self._backup_btn.set_label(self._backup_options[idx])

    def _on_apply(self, widget):
        target = self._target_idx
        backup_idx = self._backup_idx
        backup = None if backup_idx <= 0 else backup_idx - 1

        apply_toolmap(self._screen, self.lane, target, backup)

        # Reflect the change immediately rather than round-tripping through
        # another fetch — we already know exactly what we just set.
        backup_text = f"T{backup}" if backup is not None else _("none")
        self._status_lbl.set_text(
            _(f"Currently: T{self.lane} → T{target}  ·  backup: {backup_text}")
        )

    def _on_overview_clicked(self, widget):
        self._screen.show_panel(
            "filament_lanes_toolmap",
            panel_name="filament_lanes_toolmap",
            title=_("Tool Map"),
            tool_count=self.tool_count,
        )
