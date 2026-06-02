# lib/ — shared HA test client (Session 2)

Reserved for the small, reusable HA client that consuming projects use to talk to
the demo HA: a WebSocket client (auth handshake, id-matched RPC), a REST client
(`wait_until_up`, `wait_for_component`), and settle-poll helpers.

These will be **ported from `pollenwatch/cleanroom/lib/`** (`ha_ws.py`,
`ha_api.py`) when a consumer actually needs shared helpers (S2b/S3).

**S2a decision (kept self-contained):** the fixture self-test
(`scripts/assert_fixture.py`, driven by `self-test.yml`) does NOT use this dir. It
is a single self-contained script using `aiohttp` (WS + REST) + the baked public
token — one `pip install aiohttp` in CI, no shared-lib dependency, no `pollenwatch`
`websockets` coupling. Porting `lib/` now would add a dependency the self-test
doesn't need; defer it to when oriel's consumer tests (S2b) want the WS helpers.

See `DESIGN.md` §1.1 (the shared client is the one piece of test plumbing both
oriel and pollenwatch genuinely share).
