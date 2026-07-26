import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

# Plain module, no Panel class — shared by filament_lanes_routing.py and
# filament_lanes_manual.py, same pattern as spoolman_common.py/tool_routing.py
# (KlipperScreen loads one Panel per file keyed by filename, so anything
# imported by more than one panel has to live outside any of them).
#
# Gtk.ComboBoxText's popup relies on an implicit Gdk pointer grab. Under
# KlipperScreen's raw Xorg session (no window manager), a touchscreen tap's
# synthetic button-release fires immediately after the press that opens the
# popup, which the grab reads as "clicked outside" — the dropdown closes
# before a row can be tapped. A modal Gtk.Dialog with plain buttons doesn't
# have this problem (already proven working elsewhere in this project, e.g.
# the Assign chooser in filament_lanes.py), so that's what backs this
# touch-friendly replacement for ComboBoxText.


def pick_from_list(screen, title, options, active_index=0):
    """Shows a modal list of options, one row per tap. Returns the selected
    index, or None if cancelled or options is empty."""
    if not options:
        return None

    dialog = Gtk.Dialog(title=title, transient_for=screen, modal=True)
    dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)

    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_min_content_height(300)

    listbox = Gtk.ListBox()
    listbox.set_selection_mode(Gtk.SelectionMode.NONE)
    listbox.set_activate_on_single_click(True)

    for i, text in enumerate(options):
        row = Gtk.ListBoxRow()
        lbl = Gtk.Label(label=text)
        lbl.set_halign(Gtk.Align.START)
        lbl.set_margin_top(10)
        lbl.set_margin_bottom(10)
        lbl.set_margin_start(8)
        row.add(lbl)
        listbox.add(row)
        if i == active_index:
            listbox.select_row(row)

    result = {"index": None}

    def _on_row_activated(_listbox, row):
        result["index"] = row.get_index()
        dialog.response(Gtk.ResponseType.OK)

    listbox.connect("row-activated", _on_row_activated)
    scroll.add(listbox)

    box = dialog.get_content_area()
    box.set_margin_start(12)
    box.set_margin_end(12)
    box.set_margin_top(12)
    box.set_margin_bottom(12)
    box.add(scroll)
    box.show_all()

    response = dialog.run()
    dialog.destroy()

    return result["index"] if response == Gtk.ResponseType.OK else None
