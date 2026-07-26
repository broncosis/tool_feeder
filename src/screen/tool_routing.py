import logging

logger = logging.getLogger("KlipperScreen")

# Plain module, no Panel class — shared by filament_lanes_routing.py and
# filament_lanes_toolmap.py, same pattern as spoolman_common.py (KlipperScreen
# loads one Panel per file keyed by filename, so anything imported by more
# than one panel has to live outside any of them).


def detect_tool_count(panel):
    """Same probe filament_lanes.py's own _detect_tool_count uses (extruder,
    extruder1, extruder2, ...) — kept in sync with that implementation since
    all three copies in this project (installer, sync service, screen) agree
    on the same convention."""
    printer = getattr(panel, "_printer", None)
    if not printer or not printer.get_stat("extruder"):
        return 0
    count = 1
    while printer.get_stat(f"extruder{count}"):
        count += 1
    return count


def fetch_toolmap_state(screen, callback):
    """Synchronous REST query for the full _TOOLMAP state (map, backup_map,
    filament_status — all tools). Not part of KlipperScreen's default
    subscription set, so this needs an explicit fetch, same pattern
    filament_lanes.py's own _fetch_data uses for save_variables/toolchanger.
    Calls callback(map_dict, backup_map_dict, filament_status_dict) — all
    three empty dicts on failure, so callers don't need to special-case None.
    """
    result = screen.restApi.send_request(
        "printer/objects/query?gcode_macro%20_TOOLMAP"
    )
    if not result or not isinstance(result, dict) or "status" not in result:
        logger.warning("tool_routing: could not fetch _TOOLMAP state")
        callback({}, {}, {})
        return

    toolmap = result["status"].get("gcode_macro _TOOLMAP", {})
    tool_map = toolmap.get("map", {}) or {}
    backup_map = toolmap.get("backup_map", {}) or {}
    filament_status = toolmap.get("filament_status", {}) or {}

    # Moonraker's JSON round-trip turns int dict keys into strings.
    def _intkeys(d):
        out = {}
        for k, v in d.items():
            try:
                out[int(k)] = v
            except (TypeError, ValueError):
                continue
        return out

    callback(_intkeys(tool_map), _intkeys(backup_map), _intkeys(filament_status))


def apply_toolmap(screen, lane, target, backup):
    """Sends SET_TOOLMAP. backup=None means "no backup" (BACKUP=-1, see the
    Tool Feeder addition to SET_TOOLMAP in tool_feeder_macros.cfg)."""
    backup_val = backup if backup is not None else -1
    script = f"SET_TOOLMAP T={lane} EFF={target} BACKUP={backup_val}"
    screen._send_action(None, "printer.gcode.script", {"script": script})


def reset_toolmap(screen):
    screen._send_action(None, "printer.gcode.script", {"script": "RESET_TOOLMAP"})
