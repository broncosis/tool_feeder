#!/usr/bin/env python3
"""
spoolman_lane_sync.py
=====================
Syncs Spoolman spool data into Moonraker's lane_data namespace so
OrcaSlicer automatically sees which filament is loaded in each tool.

Works with toolchangers (KTC, StealthChanger), IDEX, and any
multi-extruder Klipper setup. Hands off automatically if Happy Hare
or AFC is already managing lane_data.

Config (environment variables or .env file next to this script):
  MOONRAKER_URL      Moonraker base URL    (default: http://localhost:7125)
  SPOOLMAN_URL       Spoolman base URL     (default: http://localhost:7912)
  MOONRAKER_API_KEY  Optional API key      (if Moonraker auth is enabled)
  LOG_LEVEL          Logging verbosity     (default: INFO)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    import aiohttp
except ImportError:
    print("ERROR: aiohttp not found. Run:  pip install aiohttp", file=sys.stderr)
    sys.exit(1)

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
LOG = logging.getLogger("spoolman_lane_sync")

# ── Constants ──────────────────────────────────────────────────────────────────
_LANE_NS         = "lane_data"
_SOURCE_KEY      = "source"
_MY_SOURCE       = "spoolman"
_EMPTY_SLOT: dict[str, Any] = {
    "material": "", "color": "", "vendor": "", "filament_id": ""
}
_VAR_RE          = re.compile(r"^t(\d+)__spool_id$", re.IGNORECASE)
_LOC_RE          = re.compile(r"^[Tt](\d+)$")
_RECONNECT_INIT  = 2.0
_RECONNECT_MAX   = 60.0
_POLL_INTERVAL   = 30.0


# ── Service ────────────────────────────────────────────────────────────────────

class SpoolmanLaneSync:
    def __init__(
        self,
        moonraker_url: str,
        spoolman_url: str,
        api_key: str = "",
    ) -> None:
        self._moonraker = moonraker_url.rstrip("/")
        self._spoolman  = spoolman_url.rstrip("/")
        self._mr_headers: dict[str, str] = (
            {"X-Api-Key": api_key} if api_key else {}
        )

    # ── Entry point ────────────────────────────────────────────────────────────

    async def run(self) -> None:
        LOG.info("Moonraker: %s  |  Spoolman: %s", self._moonraker, self._spoolman)

        await self._wait_for_moonraker()

        if not await self._claim_source():
            LOG.info(
                "lane_data is owned by another system — exiting. "
                "Remove the '%s' key from the lane_data namespace to take over.",
                _SOURCE_KEY,
            )
            return

        await asyncio.gather(
            self._spoolman_poll_loop(),
            self._moonraker_ws_loop(),
        )

    # ── Source ownership ───────────────────────────────────────────────────────

    async def _claim_source(self) -> bool:
        source = await self._db_get(_SOURCE_KEY)
        if source is None or source == _MY_SOURCE:
            await self._db_set(_SOURCE_KEY, _MY_SOURCE)
            LOG.info("lane_data source key claimed: '%s'", _MY_SOURCE)
            return True
        LOG.warning(
            "lane_data is managed by '%s' — hands off.",
            source,
        )
        return False

    # ── Sync ──────────────────────────────────────────────────────────────────

    async def _sync(self) -> None:
        """Build lane_data from per-tool spool assignments and push to Moonraker."""
        try:
            num_tools = await self._get_num_tools()
            # Primary: read tN__spool_id from Klipper save_variables
            tool_map = await self._tool_map_from_variables()
            # Fallback: match spools by Spoolman Location field (T0, T1, …)
            if not tool_map:
                LOG.debug("No save_variables assignments found — trying Location fallback")
                tool_map = await self._tool_map_from_locations()
        except Exception as exc:
            LOG.warning("Sync skipped: %s", exc)
            return

        # Write each lane as its own key in the namespace.
        # OrcaSlicer reads the whole namespace and identifies lanes by the
        # "lane" field inside each object — the outer key name doesn't matter.
        errors = 0
        for i in range(num_tools):
            entry = {**tool_map.get(i, _EMPTY_SLOT), "lane": str(i)}
            try:
                await self._db_set(str(i), entry)
            except Exception as exc:
                LOG.warning("Failed to write lane_data/%d: %s", i, exc)
                errors += 1

        if errors:
            return

        # Remove legacy "tools" key written by older versions of this service
        await self._db_delete("tools")

        lane_data = {str(i): tool_map.get(i, _EMPTY_SLOT) for i in range(num_tools)}
        loaded = sum(1 for v in lane_data.values() if v.get("material"))
        LOG.info(
            "Synced — %d/%d tools loaded:  %s",
            loaded, num_tools, _lane_summary(lane_data),
        )

    # ── Primary source: Klipper save_variables ─────────────────────────────────

    async def _tool_map_from_variables(self) -> dict[int, dict]:
        """Read t{n}__spool_id from save_variables, fetch each spool from Spoolman."""
        try:
            async with aiohttp.ClientSession(headers=self._mr_headers) as s:
                async with s.get(
                    f"{self._moonraker}/printer/objects/query",
                    params={"save_variables": ""},
                    raise_for_status=True,
                ) as resp:
                    data = await resp.json()
        except Exception as exc:
            LOG.debug("Could not read save_variables: %s", exc)
            return {}

        variables: dict = (
            data.get("result", {})
                .get("status", {})
                .get("save_variables", {})
                .get("variables", {})
        )

        assignments: dict[int, int] = {}
        for key, val in variables.items():
            m = _VAR_RE.match(key)
            if m:
                try:
                    assignments[int(m.group(1))] = int(val)
                except (TypeError, ValueError):
                    pass

        if not assignments:
            return {}

        LOG.debug("save_variables spool assignments: %s", assignments)

        tool_map: dict[int, dict] = {}
        for tool_num, spool_id in assignments.items():
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(
                        f"{self._spoolman}/api/v1/spool/{spool_id}",
                        raise_for_status=True,
                    ) as resp:
                        spool = await resp.json()
                tool_map[tool_num] = _spool_to_lane(spool)
            except Exception as exc:
                LOG.warning("Could not fetch spool %d for T%d: %s", spool_id, tool_num, exc)

        return tool_map

    # ── Fallback source: Spoolman Location field ───────────────────────────────

    async def _tool_map_from_locations(self) -> dict[int, dict]:
        """Fallback: match active spools by Spoolman Location = T0/T1/…"""
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{self._spoolman}/api/v1/spool",
                    params={"allow_archived": "false"},
                    raise_for_status=True,
                ) as resp:
                    spools: list[dict] = await resp.json()
        except Exception as exc:
            LOG.warning("Could not fetch spools from Spoolman: %s", exc)
            return {}

        tool_map: dict[int, dict] = {}
        for spool in spools:
            loc = (spool.get("location") or "").strip()
            m = _LOC_RE.match(loc)
            if m:
                tool_num = int(m.group(1))
                tool_map[tool_num] = _spool_to_lane(spool)
                spool_id = spool.get("id")
                if spool_id is not None:
                    await self._write_spool_assignment(tool_num, spool_id)
        return tool_map

    async def _write_spool_assignment(self, tool_num: int, spool_id: int) -> None:
        """Mirror a Location-based assignment into Klipper's save_variables, the
        same way KlipperScreen's own assign action does (see _assign() in
        filament_lanes_spoolman.py), so _TOOL_ROUTER and the panel's display see
        it too — not just lane_data/OrcaSlicer. Once this lands in
        save_variables, the next sync cycle uses the primary (save_variables)
        path instead of this fallback, so it's self-healing rather than a
        permanent parallel path."""
        script = (
            f"SET_GCODE_VARIABLE MACRO=T{tool_num} VARIABLE=spool_id VALUE={spool_id}\n"
            f"SAVE_VARIABLE VARIABLE=t{tool_num}__spool_id VALUE={spool_id}"
        )
        try:
            async with aiohttp.ClientSession(headers=self._mr_headers) as s:
                async with s.post(
                    f"{self._moonraker}/printer/gcode/script",
                    params={"script": script},
                    raise_for_status=True,
                ):
                    pass
        except Exception as exc:
            LOG.warning("Could not write save_variables for T%d: %s", tool_num, exc)

    # ── Tool count ─────────────────────────────────────────────────────────────

    async def _get_num_tools(self) -> int:
        """Count extruder* objects in Klipper — works for 1 to 12+ tools."""
        async with aiohttp.ClientSession(headers=self._mr_headers) as s:
            async with s.get(
                f"{self._moonraker}/printer/objects/list",
                raise_for_status=True,
            ) as resp:
                data = await resp.json()

        objects: list[str] = data.get("result", {}).get("objects", [])
        extruders = [o for o in objects if re.match(r"^extruder\d*$", o)]
        count = max(len(extruders), 1)
        LOG.debug("Detected %d extruder(s)", count)
        return count

    # ── Spoolman poll loop ─────────────────────────────────────────────────────

    async def _spoolman_poll_loop(self) -> None:
        """Poll Spoolman every 30 s to keep filament data current.
        Spool assignment changes trigger an immediate re-sync via Moonraker WS."""
        LOG.info("Spoolman poll loop started (interval: %.0fs)", _POLL_INTERVAL)
        while True:
            await self._sync()
            try:
                await asyncio.sleep(_POLL_INTERVAL)
            except asyncio.CancelledError:
                return

    # ── Moonraker WebSocket ────────────────────────────────────────────────────

    async def _moonraker_ws_loop(self) -> None:
        """Connect to Moonraker WS, subscribe to save_variables updates,
        and re-sync whenever spool assignments or Klippy state changes."""
        ws_url = (
            self._moonraker
            .replace("http://", "ws://")
            .replace("https://", "wss://")
            + "/websocket"
        )
        delay = _RECONNECT_INIT
        while True:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.ws_connect(
                        ws_url, headers=dict(self._mr_headers)
                    ) as ws:
                        LOG.info("Moonraker WS connected")
                        delay = _RECONNECT_INIT
                        # Subscribe to save_variables so we get notified when
                        # the user reassigns a spool to a tool slot
                        await ws.send_str(json.dumps({
                            "jsonrpc": "2.0",
                            "method": "printer.objects.subscribe",
                            "params": {"objects": {"save_variables": None}},
                            "id": 1,
                        }))
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await self._on_moonraker_event(json.loads(msg.data))
                            elif msg.type in (
                                aiohttp.WSMsgType.CLOSED,
                                aiohttp.WSMsgType.ERROR,
                            ):
                                break
                LOG.warning("Moonraker WS closed — reconnecting in %.0fs…", delay)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                LOG.warning("Moonraker WS error: %s — reconnecting in %.0fs…", exc, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_MAX)

    async def _on_moonraker_event(self, data: dict) -> None:
        method = data.get("method", "")
        if method == "notify_klippy_ready":
            LOG.info("Klippy ready — re-syncing lane_data")
            await self._sync()
        elif method == "notify_klippy_shutdown":
            LOG.info("Klippy shutdown — will re-sync when it comes back")
        elif method == "notify_status_update":
            # Re-sync whenever a save_variables change is reported
            params = data.get("params", [{}])
            if isinstance(params, list) and params and "save_variables" in params[0]:
                LOG.debug("save_variables changed — re-syncing")
                await self._sync()

    # ── Moonraker database ─────────────────────────────────────────────────────

    async def _db_get(self, key: str) -> Any:
        async with aiohttp.ClientSession(headers=self._mr_headers) as s:
            async with s.get(
                f"{self._moonraker}/server/database/item",
                params={"namespace": _LANE_NS, "key": key},
            ) as resp:
                if resp.status == 404:
                    return None
                resp.raise_for_status()
                data = await resp.json()
                return data.get("result", {}).get("value")

    async def _db_set(self, key: str, value: Any) -> None:
        async with aiohttp.ClientSession(headers=self._mr_headers) as s:
            async with s.post(
                f"{self._moonraker}/server/database/item",
                json={"namespace": _LANE_NS, "key": key, "value": value},
                raise_for_status=True,
            ):
                pass

    async def _db_delete(self, key: str) -> None:
        async with aiohttp.ClientSession(headers=self._mr_headers) as s:
            async with s.delete(
                f"{self._moonraker}/server/database/item",
                params={"namespace": _LANE_NS, "key": key},
            ):
                pass  # ignore 404 if key didn't exist

    # ── Startup wait ───────────────────────────────────────────────────────────

    async def _wait_for_moonraker(self) -> None:
        LOG.info("Waiting for Moonraker…")
        delay = 2.0
        while True:
            try:
                async with aiohttp.ClientSession(headers=self._mr_headers) as s:
                    async with s.get(
                        f"{self._moonraker}/printer/info",
                        raise_for_status=True,
                    ) as resp:
                        info = await resp.json()
                        state = info.get("result", {}).get("state", "unknown")
                        if state == "ready":
                            LOG.info("Moonraker ready.")
                            return
                        LOG.info("Moonraker state: '%s' — waiting…", state)
            except Exception as exc:
                LOG.info("Moonraker not reachable (%s) — retrying in %.0fs…", exc, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)


# ── Pure helpers ───────────────────────────────────────────────────────────────

def _extra_str(extra: dict, key: str) -> str:
    """Read a string extra field, stripping surrounding quotes Spoolman adds."""
    val = extra.get(key) or ""
    if isinstance(val, str) and len(val) >= 2 and val[0] == '"' and val[-1] == '"':
        val = val[1:-1]
    return val


def _spool_to_lane(spool: dict) -> dict:
    filament  = spool.get("filament") or {}
    vendor    = filament.get("vendor") or {}
    extra     = filament.get("extra") or {}
    raw_color = filament.get("color_hex") or ""
    # Some filaments store multiple colours comma-separated; take the first.
    color_hex = raw_color.split(",")[0].replace("#", "").upper()
    return {
        "material":         filament.get("material") or "",
        "color":            color_hex,
        "vendor":           vendor.get("name") or "",
        "filament_id":      _extra_str(extra, "orca_filament_id"),
        "setting_id":       _extra_str(extra, "orca_setting_id"),
        "name":             filament.get("name") or "",
        "remaining_weight": spool.get("remaining_weight"),
    }


def _lane_summary(lane_data: dict) -> str:
    return "  ".join(
        f"T{i}={'empty' if not v.get('material') else v['material']}"
        for i, v in sorted(lane_data.items(), key=lambda x: int(x[0]))
    )


# ── Config (.env loader) ───────────────────────────────────────────────────────

def _load_env() -> None:
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    _load_env()

    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.getLogger().setLevel(getattr(logging, level, logging.INFO))

    service = SpoolmanLaneSync(
        moonraker_url = os.getenv("MOONRAKER_URL", "http://localhost:7125"),
        spoolman_url  = os.getenv("SPOOLMAN_URL",  "http://localhost:7912"),
        api_key       = os.getenv("MOONRAKER_API_KEY", ""),
    )

    try:
        asyncio.run(service.run())
    except KeyboardInterrupt:
        LOG.info("Stopped.")


if __name__ == "__main__":
    main()
