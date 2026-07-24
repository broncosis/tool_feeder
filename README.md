# Tool Feeder

Filament management for Klipper toolchangers — automated per-tool loading,
live filament-lane status on the touchscreen, and Spoolman integration. Pick
any combination of:

- **Filament Feeder** — automated per-tool filament loading (macros + a buffer
  sync module supporting both one- and two-sensor hardware)
- **Spoolman Lane Sync** — background service that syncs Spoolman spool data into
  Moonraker's `lane_data` so OrcaSlicer sees what's loaded in each tool slot
- **KlipperScreen Filament Lanes** — a touchscreen panel showing live filament-lane
  status per tool

Each component installs independently.

---

## Requirements

- Klipper installed (for Filament Feeder)
- Moonraker + Spoolman installed (for Spoolman Lane Sync and richer KlipperScreen data)
- KlipperScreen installed (for the KlipperScreen panel)

None of these are required unless you install the component that needs them —
you'll get a clear message if a prerequisite is missing.

## Install

Run this one-liner on your printer's SSH session — no cloning required:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/broncosis/Tool_feeder/main/install.sh)
```

Or clone and run locally:

```bash
git clone https://github.com/broncosis/Tool_feeder.git
cd Tool_feeder
./install.sh
```

The installer detects your Klipper and config directories, lets you pick which
components to install, and — for Filament Feeder — asks about your setup:

- Whether you run Spoolman
- Number of tools — auto-detected from Klipper when possible, otherwise asked
  with a sensible default
- Load/unload temperature
- Whether you have physical unload buttons at the feeder (default yes — say
  no if you'd rather trigger unloads from the KlipperScreen panel or your own
  macro instead)
- Feeder MCU — picked from detected `/dev/serial/by-id/*` devices

From your answers it **generates** `feeder.cfg` and one `T0.cfg`..`T{n-1}.cfg`
per tool, instead of shipping static examples you'd otherwise hand-copy per
tool. A couple of things default to reasonable values instead of being asked
outright — edit them afterward if yours differ: purge bucket position
(`bucket_x=25`, `bucket_y=-4`) and Bowden tube length (1400mm).

The buffer sync module defaults to two-sensor mode (adds jam detection). If
your hardware only has one sensor, each tool's generated config already
includes a ready single-sensor block, commented out — just swap which one's
commented to switch.

What the installer *can't* know — per-tool pin wiring, TMC UART pins, CAN bus
UUIDs, dock park position, input shaper tuning — comes out as clearly marked
`CHANGE_ME_*` placeholders. Find them all with:

```bash
grep -rn CHANGE_ME_ ~/printer_data/config/feeder.cfg ~/printer_data/config/T*.cfg
```

Re-running the installer never silently overwrites `feeder.cfg` or a `T{n}.cfg`
you already have — it backs the old one up first (`feeder.cfg.bak.<timestamp>`,
next to the original), then offers to carry over any real values you already
filled in (pins, `canbus_uuid`, dock position, etc.) into the newly generated
file, so re-running to change one setting doesn't mean re-entering everything
else from scratch.

For KlipperScreen: if Spoolman isn't installed, the panel's "Assign" button
goes straight to a manual color/material picker instead of a Spoolman browser
— pick a color from a preset swatch grid (or a custom color picker) and a
material from a dropdown backed by `src/printer/materials.cfg` (edit that file
to add more materials). If Spoolman *is* installed, "Assign" offers a choice
between the two, per lane.

The installer also offers to add a single `[update_manager tool_feeder]` entry
to `moonraker.conf` covering whichever components you installed, so a `git
pull` on this repo updates all of them together. Spoolman Lane Sync gets
registered in `moonraker.asvc` so Moonraker is allowed to manage its service.

## Credits

See [CREDITS.md](CREDITS.md) for full attribution.

## License

GPL v3 — see [LICENSE](LICENSE).
