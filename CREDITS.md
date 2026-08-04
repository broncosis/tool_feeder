# Credits

## Component origins (Broncosis)

- **Filament_feeder** — https://github.com/broncosis/Filament_feeder — License: GPL v3 (confirmed — see the `dev_router` branch's `LICENSE` file)
- **spoolman-lane-sync** — https://github.com/broncosis/spoolman-lane-sync — License: MIT (confirmed)
- **KlipperScreen-filament-lanes** — https://github.com/broncosis/KlipperScreen-filament-lanes — License: GPL v3 (confirmed)

## Third-party inspiration and hardware compatibility

### Annex Engineering — Belay
- **Source:** https://github.com/Annex-Engineering/Belay
- **License:** CC BY-NC-SA 4.0 (verified directly from the repo's `LICENSE.md` — this is *more restrictive* than GPL v3 due to the NonCommercial clause)
- **Used for:** Hardware/sensor-compatibility reference only. Tool Feeder's own `feeder_buffer.py` includes a `sensor_mode: single` mode that's wiring-compatible with Belay's single-sensor hardware, for users who already have that sensor installed — but it's an independent implementation. No Belay code is installed, cloned, or redistributed by Tool Feeder anywhere.

### Armoured Turtle / AFC
- **Source:** https://github.com/AFCProject/AFC-Klipper-Add-On, https://github.com/ArmoredTurtle/AFC-Klipper-Screen-Add-On
- **License:** GPL v3 (confirmed)
- **Used for:** Two-sensor buffer concept and hardware that `feeder_buffer.py` is designed to work with. The two-sensor approach and AFC's config-parameter naming convention are used as inspiration, not copied code.

### CapTightpants — SIFM (Spoolman Interactive Filament Manager)
- **Source:** https://github.com/CapTightpants/SIFM
- **License:** Unknown — used with explicit permission from the author
- **Used for:** The tip-forming wiggle sequence in `UNLOAD_ANY_TOOL` is based on `_SIFM_LOAD_FINISH` from SIFM.

### N3MI-DG — Prime Lines Macro
- **Source:** No public repo/link — direct community sharing
- **License:** Unknown — used with explicit permission from the author
- **Used for:** `prime_purge.cfg`'s `PRIME_PURGE` macro (per-tool prime/purge
  sequence run at print start) started from N3MI-DG's prime lines macro,
  since heavily modified — only loosely based on the original at this point.

### Nic335 — Tool Router
- **Source:** https://github.com/nic335
- **License:** Unknown — used with explicit permission from the author
- **Used for:** Tool remapping and spool-failover logic (`toolmap.cfg`: `_TOOLMAP`, `_TOOL_ROUTER`, `SET_TOOLMAP`, `SET_TOOL_FILAMENT_STATUS`, `RESET_TOOLMAP`, `SHOW_TOOLMAP`).

### KlipperScreen
- **Source:** https://github.com/KlipperScreen/KlipperScreen
- **License:** GPL v3
- **Used for:** The spool-icon SVG color-substitution pattern in `panels/filament_lanes.py` is adapted from KlipperScreen's own `panels/spoolman.py`. The `ScreenPanel` base class and GTK helper infrastructure (`_gtk`, `_screen`, `_printer`) are part of KlipperScreen.

### Klipper / Moonraker
- **Source:** https://github.com/Klipper3d/klipper, https://github.com/Arksine/moonraker
- **License:** GPL v3
- **Used for:** The `save_variables` persistence mechanism, Moonraker's database namespace API (`lane_data`), Spoolman proxy API, and `update_manager`/systemd service conventions.

### Spoolman
- **Source:** https://github.com/Donkie/Spoolman
- **License:** MIT
- **Used for:** Spool/filament/material data model and the `Location` field convention (`T0`, `T1`, ... → tool slot).
