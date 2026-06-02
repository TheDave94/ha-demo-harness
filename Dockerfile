# ha-demo-harness — a pre-baked, populated demo Home Assistant for e2e testing.
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │ HA IS PINNED TO 2026.5.x BY DIGEST — DO NOT BUMP CASUALLY.               │
# │ The fixture's configuration.yaml uses LEGACY `template:` entities        │
# │ (per-area temp/humidity sensors + door/window/motion binary_sensors)     │
# │ which are REMOVED in HA 2026.6. Bumping HA past 2026.5 is a BREAKING      │
# │ change that requires migrating those template entities FIRST.            │
# │ The pin also freezes the recorder schema (v53) that harness_seed writes  │
# │ backdated history against. See README "HA version lock" + FINDINGS.md.   │
# └─────────────────────────────────────────────────────────────────────────┘
# Digest = ghcr.io/home-assistant/home-assistant:stable as of HA 2026.5.4.
FROM ghcr.io/home-assistant/home-assistant@sha256:ceb1202133a5a036e8b03e20a10eb113186cc2f871968323c6fc6c3fc4205716

# Bake the populated demo config into /config. The base image declares no VOLUME
# on /config, so a fresh container boots directly from this image layer — no
# named volume, no hand-staging. (.dockerignore keeps caches + the token note out.)
COPY seed/ /config/

# HA expects the auth secrets to be owner-read-only.
RUN chmod 600 /config/.storage/auth /config/.storage/auth_provider.homeassistant \
    && chmod 644 /config/.storage/onboarding /config/.storage/core.uuid

EXPOSE 8123
