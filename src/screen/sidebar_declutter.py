from gi.repository import GLib

# Plain module, no Panel class — shared by every filament_lanes*.py panel,
# same pattern as spoolman_common.py/tool_routing.py/touch_picker.py.
#
# KlipperScreen's show_panel() deactivates whichever panel is currently on
# top before activating the next one, on every transition (forward *or*
# back) — not just when truly leaving a section. So hiding the sidebar's
# notification/shutdown icons for the whole Filament section only works if
# every one of its panels hides them again in its own activate() and shows
# them back in deactivate(): deactivate() is always immediately followed,
# synchronously, either by another filament panel's activate() re-hiding
# them (no visible flicker — GTK doesn't repaint mid-callback-chain), or a
# non-filament panel's activate() where showing them back is correct.
#
# The hide has to be deferred a tick past the current call stack: screen.py's
# attach_panel() calls the *outer* Gtk.Widget.show_all() right after our
# activate() returns, and "shortcut" (unlike "shutdown"/"estop") has no
# no_show_all guard, so that trailing show_all() would immediately undo a
# synchronous .hide(). GLib.idle_add runs after that settles, so it wins.


def hide_extra_icons(screen):
    def _hide():
        control = screen.base_panel.control
        control["shortcut"].hide()
        control["shutdown"].hide()
        # back/home are also vexpand=True by construction (base_panel.py,
        # stock KlipperScreen) — with fewer visible siblings to share the
        # action_bar's full-height allocation, they were stretching to
        # fill it, pushing everything below them down. Only 2 fixed-size
        # widgets to reach into, so no need to patch the stock file itself.
        control["back"].set_vexpand(False)
        control["home"].set_vexpand(False)
        return False
    GLib.idle_add(_hide)


def show_extra_icons(screen):
    control = screen.base_panel.control
    control["shortcut"].show()
    control["shutdown"].show()
    control["back"].set_vexpand(True)
    control["home"].set_vexpand(True)
