#!/usr/bin/env python3
"""Assert the demo HA fixture invariants against the KNOWN S1.5 baseline (164).

This is the regression guard: if a seed/fixture change breaks an invariant, this
fails in the harness repo's own CI (self-test.yml) — not downstream in a consumer.

It asserts against committed constants, NOT against the :8126 reference (which is
no longer a valid baseline for entity-count or pollen — see FINDINGS.md § S1.5).

Usage:
    HA_URL=http://localhost:8127 python scripts/assert_fixture.py
Token: env HA_TOKEN, else the baked public token at seed/.storage/.PUBLIC_TOKEN.
The token is intentionally public (disposable fake container) — no secret needed.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import aiohttp

HA_URL = os.environ.get("HA_URL", "http://localhost:8127").rstrip("/")
_REPO = Path(__file__).resolve().parent.parent
HA_TOKEN = (
    os.environ.get("HA_TOKEN")
    or (_REPO / "seed" / ".storage" / ".PUBLIC_TOKEN").read_text().strip()
)

# ---- KNOWN baseline (post-S1.5) ---------------------------------------------
EXPECTED_TOTAL = 164
EXPECTED_PLATFORMS = {
    "demo": 69,
    "template": 26,
    "pollenwatch": 24,
    "oriel_demo": 12,
    "sun": 9,
    "input_boolean": 8,
    "backup": 5,
    "input_select": 4,
    "person": 2,
    "script": 2,
    "shopping_list": 1,
    "google_translate": 1,
    "met": 1,
}
EXPECTED_AREAS = {
    "Bathroom", "Bedroom", "Garage", "Hallway", "Kitchen", "Living Room", "Office",
}
EXPECTED_STRATEGY_TYPE = "custom:oriel"
EXPECTED_TOGGLES = {
    "show_pollen": True,
    "show_plants_section": True,
    "show_persons_section": True,
    "show_routines_section": True,
    "use_bubble_drawers": True,
    "house_mode_entity": "input_select.house_mode",
    "favorite_entities": ["light.bed_light", "climate.heatpump", "cover.living_room_window"],
}
EXPECTED_POLLEN = {
    "grass": "high", "alder": "low", "birch": "low",
    "ragweed": "mixed", "mugwort": "none", "olive": "unknown",
}
SPARKLINE_SENSOR = "sensor.living_room_temperature"
DASHBOARD_URL_PATH = "oriel-demo"

# Fixture-ready gate. harness_seed runs on homeassistant_started in this order:
# neutralize coordinators -> stage states (pollen) -> inject recorder history.
# So we must wait for BOTH the pollen spread AND the sparkline history (the LAST
# step) before asserting — gating only on pollen races the history injection.
READY_SENTINEL = "sensor.pollenwatch_analytics_grass_consensus"
READY_VALUE = "high"
READY_TIMEOUT_S = 120


def _hdr() -> dict[str, str]:
    return {"Authorization": f"Bearer {HA_TOKEN}"}


async def _ws(session: aiohttp.ClientSession, messages: list[dict]) -> list:
    out = []
    async with session.ws_connect(f"{HA_URL}/api/websocket") as ws:
        await ws.receive_json()  # auth_required
        await ws.send_json({"type": "auth", "access_token": HA_TOKEN})
        await ws.receive_json()  # auth_ok
        for i, m in enumerate(messages, start=1):
            await ws.send_json({"id": i, **m})
            while True:
                r = await ws.receive_json()
                if r.get("id") == i and r.get("type") == "result":
                    if not r.get("success", False):
                        raise RuntimeError(f"WS {m['type']} failed: {r.get('error')}")
                    out.append(r["result"])
                    break
    return out


async def _rest(session: aiohttp.ClientSession, path: str):
    async with session.get(f"{HA_URL}{path}", headers=_hdr()) as r:
        r.raise_for_status()
        return await r.json()


async def _sparkline_points(session: aiohttp.ClientSession) -> int:
    hist = await _rest(
        session,
        f"/api/history/period?filter_entity_id={SPARKLINE_SENSOR}&minimal_response",
    )
    return len(hist[0]) if hist and hist[0] else 0


async def _wait_ready(session: aiohttp.ClientSession) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_S
    pollen = "<unset>"
    pts = 0
    while time.monotonic() < deadline:
        try:
            s = await _rest(session, f"/api/states/{READY_SENTINEL}")
            pollen = s.get("state")
            pts = await _sparkline_points(session)
            if pollen == READY_VALUE and pts > 1:
                return
        except Exception as e:  # noqa: BLE001
            pollen = f"<{type(e).__name__}>"
        await asyncio.sleep(3)
    raise TimeoutError(
        f"fixture not ready within {READY_TIMEOUT_S}s: "
        f"{READY_SENTINEL}={pollen!r} (want {READY_VALUE!r}), "
        f"sparkline points={pts} (want >1) — harness_seed didn't finish?"
    )


async def main() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {name}{(' — ' + detail) if detail and not cond else ''}")
        if not cond:
            failures.append(f"{name}{(': ' + detail) if detail else ''}")

    async with aiohttp.ClientSession() as session:
        print(f"Waiting for fixture-ready signal at {HA_URL} ...")
        await _wait_ready(session)
        print("Fixture ready. Asserting invariants against the S1.5 baseline (164):\n")

        ents, areas, lova = await _ws(session, [
            {"type": "config/entity_registry/list"},
            {"type": "config/area_registry/list"},
            {"type": "lovelace/config", "url_path": DASHBOARD_URL_PATH},
        ])

        # 1. total + per-platform counts
        check("entity count == 164", len(ents) == EXPECTED_TOTAL, f"got {len(ents)}")
        plat: dict[str, int] = {}
        for e in ents:
            plat[e["platform"]] = plat.get(e["platform"], 0) + 1
        for p, n in EXPECTED_PLATFORMS.items():
            check(f"platform {p} == {n}", plat.get(p, 0) == n, f"got {plat.get(p, 0)}")
        extra = set(plat) - set(EXPECTED_PLATFORMS)
        check("no unexpected platforms", not extra, f"extra: {sorted(extra)}")

        # 2. areas
        got_areas = {a["name"] for a in areas}
        check("7 area names", got_areas == EXPECTED_AREAS,
              f"got {sorted(got_areas)}")

        # 3. strategy dashboard + toggles
        strat = (lova or {}).get("strategy", {}) or {}
        check("strategy.type == custom:oriel",
              strat.get("type") == EXPECTED_STRATEGY_TYPE, f"got {strat.get('type')}")
        for k, v in EXPECTED_TOGGLES.items():
            check(f"toggle {k}", strat.get(k) == v, f"got {strat.get(k)!r}")

        # 4. pollen consensus spread (deterministic post-S1.5)
        states = await _rest(session, "/api/states")
        by_id = {s["entity_id"]: s["state"] for s in states}
        for sp, exp in EXPECTED_POLLEN.items():
            eid = f"sensor.pollenwatch_analytics_{sp}_consensus"
            check(f"pollen {sp} == {exp}", by_id.get(eid) == exp, f"got {by_id.get(eid)!r}")

        # 5. sparkline has plottable history
        hist = await _rest(
            session,
            f"/api/history/period?filter_entity_id={SPARKLINE_SENSOR}&minimal_response",
        )
        pts = len(hist[0]) if hist and hist[0] else 0
        check("sparkline history plottable (>1 pts)", pts > 1, f"got {pts}")

    print()
    if failures:
        print(f"FIXTURE ASSERTION FAILED — {len(failures)} invariant(s) broken:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL FIXTURE INVARIANTS HOLD ✓ (baseline 164)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
