# spoolman_common.py — shared Spoolman spool-field extraction.
#
# filament_lanes.py and filament_lanes_spoolman.py each independently pulled
# the same fields out of a raw Spoolman spool dict (name, material, vendor,
# color_hex, id). This is the deduplicated version both import. It is a
# plain module, not a KlipperScreen Panel — KlipperScreen's panel loader
# expects one `Panel` class per file, keyed by filename, so this file must
# stay import-only and never define a Panel itself.
#
# What's NOT here: how each panel consumes color_hex differs (filament_lanes.py
# substitutes it into an SVG string, filament_lanes_spoolman.py parses it into
# RGB floats for a Cairo swatch draw) — that stays in each panel file.


def extract_spool_fields(spool):
    """Given a raw Spoolman spool dict, return the fields both panels need:
    name, material, vendor, color_hex, spool_id, remaining_weight."""
    filament = spool.get("filament") or {}
    vendor = (filament.get("vendor") or {}).get("name", "")
    return {
        "name":             filament.get("name", ""),
        "material":         filament.get("material", ""),
        "vendor":           vendor,
        "color_hex":        filament.get("color_hex", ""),
        "spool_id":         spool.get("id"),
        "remaining_weight": spool.get("remaining_weight"),
    }
