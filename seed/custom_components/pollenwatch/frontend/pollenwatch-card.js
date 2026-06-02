/**
 * pollenwatch-card.js — Lovelace custom card for PollenWatch.
 *
 * Renders the categorical severity gauge defined in brand/GAUGE_SPEC.md:
 * six states (none / low / high / mixed / unknown / nodata) with the
 * core honesty rules:
 *   - needle only at segment centers; removed for mixed/unknown/nodata
 *   - gray-never-green for empty readings (unknown/nodata)
 *   - higher is worse; no celebratory motion
 *
 * Auto-registered by the integration's __init__.py (one install delivers
 * both). Config: { type: 'custom:pollenwatch-card', species: 'grass',
 * show_mixed_span?: false, expanded_default?: false }.
 */
(() => {
  const CARD_VERSION = '0.6.0';  // v2.4 — bars + compact + tiles multi-species overview layouts

  // Resolved layout. 'gauge' is the pre-v2.4 single-species view, unchanged.
  // 'bars', 'compact', and 'tiles' are the v2.4 multi-species overview
  // layouts (Stages 2/3/4 of docs/MULTISPECIES_CARD_PLAN.md). Every member
  // of ALLOWED_LAYOUTS has a dedicated handler — there is no placeholder
  // fallthrough anymore.
  const LAYOUT_GAUGE = 'gauge';
  const LAYOUT_BARS = 'bars';
  const LAYOUT_COMPACT = 'compact';
  const LAYOUT_TILES = 'tiles';
  const ALLOWED_LAYOUTS = new Set([
    LAYOUT_GAUGE, LAYOUT_BARS, LAYOUT_COMPACT, LAYOUT_TILES,
  ]);
  const OVERVIEW_LAYOUTS = new Set([LAYOUT_BARS, LAYOUT_COMPACT, LAYOUT_TILES]);

  // Icon URL pattern. Icons live in custom_components/pollenwatch/frontend/
  // icons/{canonical_key}.svg and are served via the integration's static
  // path registered in __init__.py. SVGs use --pw-grain-stroke /
  // --pw-grain-fill CSS vars for per-state tinting.
  const ICON_URL = (species) =>
    `/pollenwatch_card_static/icons/${species}.svg`;

  // Module-scoped cache so multiple card instances for the same species
  // only fetch the SVG once.
  const ICON_CACHE = new Map();
  async function loadIcon(species) {
    if (ICON_CACHE.has(species)) return ICON_CACHE.get(species);
    try {
      const r = await fetch(ICON_URL(species));
      if (!r.ok) {
        ICON_CACHE.set(species, null);
        return null;
      }
      const svg = await r.text();
      ICON_CACHE.set(species, svg);
      return svg;
    } catch (e) {
      ICON_CACHE.set(species, null);
      return null;
    }
  }

  // ── Geometry (matches brand/gauge/gauge-gen.js, viewBox 0 0 120 92) ──
  const CX = 60, CY = 60, R = 44, W = 11;
  const SEG_BOUNDS = [[-78, -28], [-24, 24], [28, 78]];
  const SEG_COLORS = ['#3DAE5A', '#F2A516', '#E0492E'];
  const CENTERS = [-52, 0, 52];
  const PETAL_COLORS = ['#F2B705', '#EC7A1C', '#E0492E', '#D94F87', '#7FB23E', '#F2B705'];
  const PETAL_R = 13;
  const GRAINS = [
    { cx: CX + 27, cy: CY + 4,  body: 3.2, spike: 2.2, n: 9 },
    { cx: CX + 18, cy: CY + 22, body: 2.7, spike: 1.9, n: 8 },
    { cx: CX,      cy: CY + 30, body: 2.8, spike: 2.0, n: 8 },
    { cx: CX - 18, cy: CY + 22, body: 2.7, spike: 1.9, n: 8 },
    { cx: CX - 27, cy: CY + 4,  body: 3.2, spike: 2.2, n: 9 },
  ];

  const pt = (cx, cy, r, deg) => {
    const a = (deg - 90) * Math.PI / 180;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  };
  const f2 = (n) => Math.round(n * 100) / 100;

  const arcPath = (a0, a1, r) => {
    const [x0, y0] = pt(CX, CY, r, a0);
    const [x1, y1] = pt(CX, CY, r, a1);
    return `M${f2(x0)} ${f2(y0)} A${r} ${r} 0 0 1 ${f2(x1)} ${f2(y1)}`;
  };

  const grainPath = (g) => {
    let s = '';
    for (let i = 0; i < g.n; i++) {
      const ang = (360 / g.n) * i;
      const [tx, ty] = pt(g.cx, g.cy, g.body + g.spike, ang);
      const [b1x, b1y] = pt(g.cx, g.cy, g.body - 0.4, ang - 360 / g.n / 2.4);
      const [b2x, b2y] = pt(g.cx, g.cy, g.body - 0.4, ang + 360 / g.n / 2.4);
      s += `M${f2(b1x)} ${f2(b1y)} L${f2(tx)} ${f2(ty)} L${f2(b2x)} ${f2(b2y)} Z`;
    }
    return s;
  };

  // Static SVG template: all elements rendered once, CSS drives state.
  function gaugeSvg() {
    const parts = ['<svg class="pwgauge state-unknown" viewBox="0 0 120 92" xmlns="http://www.w3.org/2000/svg">'];
    // Color segments
    SEG_BOUNDS.forEach((b, i) => {
      parts.push(`<path class="seg seg-${i}" d="${arcPath(b[0], b[1], R)}" stroke="${SEG_COLORS[i]}" stroke-width="${W}" fill="none" stroke-linecap="round"/>`);
    });
    // Gray arcs for unknown (dashed) + nodata (faint), and nodata slash
    parts.push(`<path class="unknown-arc" d="${arcPath(-78, 78, R)}" stroke="#AEB7C0" stroke-width="${W}" fill="none" stroke-linecap="round" stroke-dasharray="1.5 5"/>`);
    parts.push(`<path class="nodata-arc" d="${arcPath(-78, 78, R)}" stroke="#E6E9ED" stroke-width="${W}" fill="none" stroke-linecap="round"/>`);
    parts.push(`<path class="nodata-slash" d="M30 86 L90 34" stroke="#8B96A1" stroke-width="3" stroke-linecap="round" opacity="0.85"/>`);
    // Bloom
    parts.push('<g class="bloom">');
    for (let i = 0; i < 6; i++) {
      const ang = i * 60;
      const [px, py] = pt(CX, CY, PETAL_R, ang);
      parts.push(`<ellipse class="petal" cx="${f2(px)}" cy="${f2(py)}" rx="6" ry="8.8" transform="rotate(${ang} ${f2(px)} ${f2(py)})" fill="${PETAL_COLORS[i]}"/>`);
    }
    parts.push(`<circle class="bloom-center" cx="${CX}" cy="${CY}" r="5.5" fill="#F6D45E"/>`);
    parts.push('</g>');
    // Pollen grains (5; CSS toggles per state — none=0, low=g0/g2/g4, high+mixed=all)
    GRAINS.forEach((g, i) => {
      parts.push(`<g class="grain g${i}" fill="#E8920C"><path d="${grainPath(g)}"/><circle cx="${g.cx}" cy="${g.cy}" r="${g.body}"/></g>`);
    });
    // Mixed-state rim marks
    CENTERS.forEach(c => {
      const [x, y] = pt(CX, CY, R, c);
      parts.push(`<circle class="mixed-mark" cx="${f2(x)}" cy="${f2(y)}" r="3.4" fill="#33414F"/>`);
    });
    // Needle (pointing straight up; rotated via CSS per state)
    const [, ny] = pt(CX, CY, R - 7, 0);
    parts.push(`<path class="needle" d="M${CX} ${CY} L${CX} ${f2(ny)}" stroke="#33414F" stroke-width="3.6" stroke-linecap="round"/>`);
    // Hub
    parts.push(`<circle class="hub" cx="${CX}" cy="${CY}" r="4.5" fill="#33414F"/>`);
    parts.push('</svg>');
    return parts.join('');
  }

  // ── State recipes (captions) ──────────────────────────────────────
  const STATE_RECIPE = {
    none:    { label: 'None',             sub: 'Below onset' },
    low:     { label: 'In season',        sub: 'Pollen active' },
    high:    { label: 'At peak',          sub: 'At / above peak' },
    mixed:   { label: 'Sources disagree', sub: 'No single level' },
    unknown: { label: 'Unknown',          sub: 'Not enough history yet' },
    nodata:  { label: 'No data',          sub: 'Source unavailable' },
  };
  const LEVEL_NAMES = ['none', 'low', 'high'];
  const LEVEL_COLORS = ['#3DAE5A', '#F2A516', '#E0492E'];

  // v2.0+: card accepts any species the integration knows about. The
  // hard-coded gate that limited the card to the v1 6 is dropped — the
  // canonical-species set is defined in species_registry.py (Python),
  // not duplicated here. Typos surface as "nodata" (the consensus
  // sensor entity_id doesn't resolve), which is the honest failure mode.

  const SOURCE_LABELS = {
    open_meteo: 'Open-Meteo',
    polleninformation: 'polleninformation.at',
    dwd: 'DWD',
    meteoswiss: 'MeteoSwiss',
    epin: 'ePIN',
    google: 'Google',
  };

  // ── State resolution (per GAUGE_SPEC) ────────────────────────────
  function resolveState(hass, species) {
    const eid = `sensor.pollenwatch_analytics_${species}_consensus`;
    const ent = hass?.states?.[eid];
    if (!ent) return {
      state: 'nodata', source_levels: {},
      source_count: 0, max_possible: 0,
    };
    const src = ent.attributes?.source_levels || {};
    // v2.0+ attributes — first-class data for the n/m badge.
    const source_count = ent.attributes?.source_count ?? Object.keys(src).length;
    const max_possible = ent.attributes?.max_possible_sources ?? source_count;
    if (ent.state === 'unavailable' || ent.state === 'unknown') {
      return { state: 'unknown', source_levels: src, source_count, max_possible };
    }
    if (['none', 'low', 'high', 'mixed'].includes(ent.state)) {
      return {
        state: ent.state, source_levels: src, source_count, max_possible,
        last_changed: ent.last_changed,
      };
    }
    return { state: 'unknown', source_levels: src, source_count, max_possible };
  }

  function perSourceRows(hass, species, source_levels) {
    return Object.entries(source_levels).map(([src, level]) => {
      const eid = `sensor.pollenwatch_${src}_${species}`;
      const ent = hass.states[eid];
      const label = SOURCE_LABELS[src] || src;
      if (!ent) return { src, level, label, value: '', unit: '', missing: true };
      const unit = ent.attributes.unit_of_measurement || '';
      let value = ent.state;
      // DWD: prefer the 7-point native string over the float
      if (src === 'dwd' && ent.attributes.native_value !== undefined) {
        value = ent.attributes.native_value;
      }
      return { src, level, label, value, unit, missing: false };
    });
  }

  function relTime(iso) {
    if (!iso) return '';
    const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
  }

  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

  // ── Threshold provenance marker (v2.3+) ───────────────────────────
  // Reads the derived `threshold_basis` attribute on the consensus
  // sensor and decides whether to render a small neutral marker in the
  // header. SINGLE source of truth for the basis -> (marked, tooltip)
  // mapping — the only place a card-side branch on threshold_basis
  // lives. Glance rule: "species" -> unmarked (default); "family" |
  // "estimated" -> marker visible. Severity colours are never used
  // here — provenance is orthogonal to severity.
  const PROVENANCE_MESSAGES = {
    family: "Threshold inherited from EAACI's defined family group, not species-specific.",
    estimated: 'Estimated bracket; no per-species threshold published.',
  };

  function provenanceMarker(basis) {
    const text = PROVENANCE_MESSAGES[basis];
    if (!text) return null;
    return { tooltip: text, ariaLabel: text };
  }

  // ── Card CSS ──────────────────────────────────────────────────────
  const CARD_CSS = `
    :host { display: block; }
    .card {
      background: var(--ha-card-background, var(--card-background-color, white));
      border: 1px solid var(--divider-color, #ECE4D6);
      border-radius: var(--ha-card-border-radius, 16px);
      padding: 16px;
      color: var(--primary-text-color, #2A3540);
      box-shadow: var(--ha-card-box-shadow, none);
    }
    .header { display: flex; align-items: center; gap: 10px; }
    .species-icon { width: 28px; height: 28px; flex-shrink: 0; display: inline-flex; align-items: center; justify-content: center; }
    .species-icon svg { width: 100%; height: 100%; }
    .title { font-weight: 600; font-size: 18px; letter-spacing: -0.015em; }
    .meta { display: flex; align-items: baseline; gap: 8px; margin-left: auto; }
    /* n/m badge — Q5 decision: numeric only, no dots. Tabular-nums so n
       and m line up across renders. */
    .badge {
      font-variant-numeric: tabular-nums;
      font-weight: 600; font-size: 13px;
      color: var(--primary-text-color, #2A3540);
      padding: 1px 6px; border-radius: 4px;
      background: var(--divider-color, #ECE4D6);
    }
    .meta-time { color: var(--secondary-text-color, #7C8794); font-size: 12px; }
    .gauge-wrap { display: flex; justify-content: center; padding: 6px 0; }
    .pwgauge { width: 240px; max-width: 100%; height: auto; }
    .reading { text-align: center; padding: 0 0 6px; }
    .reading-label { font-weight: 700; font-size: 26px; letter-spacing: -0.015em; transition: color 200ms; line-height: 1.15; }
    .reading-sub { color: var(--secondary-text-color, #7C8794); font-size: 12px; letter-spacing: 0.06em; text-transform: uppercase; margin-top: 3px; }
    .toggle {
      display: block; background: transparent; border: none;
      color: var(--secondary-text-color, #7C8794); cursor: pointer;
      padding: 6px 10px; margin: 6px auto 0; font: inherit; font-size: 11px;
      letter-spacing: 0.08em; text-transform: uppercase;
    }
    .toggle:hover { color: var(--primary-text-color, #2A3540); }
    .breakdown { display: none; border-top: 1px solid var(--divider-color, #ECE4D6); padding-top: 10px; margin-top: 6px; }
    .breakdown.open { display: block; }
    .source-row { display: flex; align-items: center; gap: 10px; padding: 5px 0; font-size: 14px; }
    .level-dot { width: 10px; height: 10px; border-radius: 999px; flex-shrink: 0; }
    .source-label { font-weight: 500; }
    .source-value { margin-left: auto; font-variant-numeric: tabular-nums; color: var(--secondary-text-color, #7C8794); }
    .source-value.missing { font-style: italic; opacity: 0.7; }

    /* === Gauge state styles === */
    .pwgauge .seg { transition: stroke-width 200ms, opacity 200ms; }
    .pwgauge .needle {
      transition: transform 320ms cubic-bezier(.32,.72,.30,1), opacity 200ms;
      transform-origin: ${CX}px ${CY}px;
      transform: rotate(0deg);
      opacity: 0;
    }
    .pwgauge .hub { transition: fill 200ms, stroke 200ms; }
    .pwgauge .bloom { transition: opacity 200ms; }
    .pwgauge .bloom-center, .pwgauge .petal { transition: fill 200ms; }
    .pwgauge .grain { transition: opacity 200ms; opacity: 0; }
    .pwgauge .mixed-mark, .pwgauge .unknown-arc, .pwgauge .nodata-arc, .pwgauge .nodata-slash {
      transition: opacity 200ms; opacity: 0;
    }
    .pwgauge .unknown-arc { opacity: 0; }

    /* none/low/high — needle visible, active segment thicker, hub+center take color */
    .pwgauge.state-none  .needle { transform: rotate(-52deg); opacity: 1; }
    .pwgauge.state-low   .needle { transform: rotate(0deg);   opacity: 1; }
    .pwgauge.state-high  .needle { transform: rotate(52deg);  opacity: 1; }
    .pwgauge.state-none  .seg-0  { stroke-width: ${W + 3}; }
    .pwgauge.state-low   .seg-1  { stroke-width: ${W + 3}; }
    .pwgauge.state-high  .seg-2  { stroke-width: ${W + 3}; }
    .pwgauge.state-none  .hub, .pwgauge.state-none  .bloom-center { fill: #3DAE5A; }
    .pwgauge.state-low   .hub, .pwgauge.state-low   .bloom-center { fill: #F2A516; }
    .pwgauge.state-high  .hub, .pwgauge.state-high  .bloom-center { fill: #E0492E; }

    /* Pollen grains scale with level */
    .pwgauge.state-low  .grain.g0,
    .pwgauge.state-low  .grain.g2,
    .pwgauge.state-low  .grain.g4 { opacity: 1; }
    .pwgauge.state-high .grain    { opacity: 1; }
    .pwgauge.state-mixed .grain   { opacity: 1; }

    /* Mixed — needle hidden, rim marks visible, hollow hub */
    .pwgauge.state-mixed .mixed-mark { opacity: 1; }
    .pwgauge.state-mixed .hub { fill: #fff; stroke: #33414F; stroke-width: 2; }

    /* Unknown — color segments hidden, dashed gray arc visible, bloom desaturated, hollow hub */
    .pwgauge.state-unknown .seg { opacity: 0; }
    .pwgauge.state-unknown .unknown-arc { opacity: 0.9; }
    .pwgauge.state-unknown .petal,
    .pwgauge.state-unknown .bloom-center { fill: #AEB7C0; }
    .pwgauge.state-unknown .bloom { opacity: 0.3; }
    .pwgauge.state-unknown .hub { fill: #fff; stroke: #AEB7C0; stroke-width: 2; }

    /* Nodata — color segments hidden, faint gray arc + diagonal slash, hollow hub */
    .pwgauge.state-nodata .seg { opacity: 0; }
    .pwgauge.state-nodata .nodata-arc { opacity: 1; }
    .pwgauge.state-nodata .nodata-slash { opacity: 0.85; }
    .pwgauge.state-nodata .petal,
    .pwgauge.state-nodata .bloom-center { fill: #AEB7C0; }
    .pwgauge.state-nodata .bloom { opacity: 0.3; }
    .pwgauge.state-nodata .hub { fill: #fff; stroke: #AEB7C0; stroke-width: 2; }

    /* Default bloom opacity baseline (color states) */
    .pwgauge .bloom { opacity: 0.6; }
    .pwgauge.state-unknown .bloom, .pwgauge.state-nodata .bloom { opacity: 0.3; }

    /* === Species icon tinting per severity state ===
       Icons declare var(--pw-grain-stroke,#2A3540) and
       var(--pw-grain-fill,#E8EBEE); setting the vars on .species-icon
       cascades into the inlined SVG. Duotone approach:
         - stroke = always theme-text-color (reads against any bg)
         - fill   = state-tinted (subtle severity signal)
       Earlier amber-stroke-on-cream was hard to read at 28px. */
    .species-icon {
      --pw-grain-stroke: var(--primary-text-color, #2A3540);
      --pw-grain-fill: var(--divider-color, #E8EBEE);
    }
    .card.state-none .species-icon { --pw-grain-fill: #C8E6CF; }
    .card.state-low  .species-icon { --pw-grain-fill: #FCE5B8; }
    .card.state-high .species-icon { --pw-grain-fill: #F4C6BA; }
    .card.state-mixed .species-icon,
    .card.state-unknown .species-icon,
    .card.state-nodata .species-icon {
      --pw-grain-fill: var(--divider-color, #ECE4D6);
    }

    /* === Single-source humbling — must be VISIBLE, not just polite ===
       The badge number was carrying the whole single-source signal and a
       number is easy to miss. Real treatment:
         1. Desaturate the gauge (filter) — same severity colour, less
            vivid. Eye reads "less certain" honestly.
         2. Replace the n/m badge with explicit text "single source" so
            the provenance caveat is a word, not a digit.
         3. Genuinely thin needle (1.8px vs 3.6px), lower hub opacity.
         4. Reading-label drops to 0.82 opacity. */
    .single-source-label {
      display: none;
      font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase;
      color: var(--secondary-text-color, #7C8794);
      font-style: italic;
    }
    .card.source-count-1 .badge { display: none; }
    .card.source-count-1 .single-source-label { display: inline; }
    .card.source-count-1 .pwgauge { filter: saturate(0.55); }
    .card.source-count-1 .pwgauge .needle { stroke-width: 1.8; opacity: 0.7; }
    .card.source-count-1 .pwgauge .hub { opacity: 0.7; }
    .card.source-count-1 .reading-label { opacity: 0.82; }

    /* === Threshold provenance marker (v2.3+) ===
       Small neutral dot in the meta cluster signalling "this species'
       threshold is on an EAACI family bracket or an estimated working
       bracket, not species-specific peer-reviewed evidence." Uses
       --secondary-text-color (the same muted-text token already
       carrying the single-source label, meta-time, and reading-sub) —
       theme-aware in both HA light and dark themes. NEVER uses a
       severity colour. Visibility is toggled via the [hidden] attr
       from JS; an sr-only child mirrors the tooltip text so screen
       readers can read the provenance even though the visible mark
       is wordless. tabindex="0" makes the tooltip keyboard-reachable
       (browsers surface title= on focus). */
    .provenance-marker {
      display: none;
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: var(--secondary-text-color, #7C8794);
      flex-shrink: 0;
      cursor: help;
      align-self: center;
      outline-offset: 2px;
    }
    .provenance-marker:not([hidden]) { display: inline-block; }
    .provenance-marker .sr-only {
      position: absolute;
      width: 1px; height: 1px;
      padding: 0; margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }

    /* Reading label color per state — gray-never-green for empty */
    .reading.state-none .reading-label  { color: #3DAE5A; }
    .reading.state-low  .reading-label  { color: #F2A516; }
    .reading.state-high .reading-label  { color: #E0492E; }
    .reading.state-mixed .reading-label { color: var(--primary-text-color, #2A3540); }
    .reading.state-unknown .reading-label,
    .reading.state-nodata  .reading-label { color: var(--secondary-text-color, #7C8794); }

    @media (prefers-reduced-motion: reduce) {
      .pwgauge *, .reading-label { transition: none !important; }
    }

    /* === Bars overview layout (v2.4+) ============================
       Two-column auto-grid of rows. Each row: species name on the
       left, severity-filled track in the middle, level word + tiny
       provenance dot on the right. Track LENGTH double-codes the
       same tier the COLOUR encodes; mixed is hatched-neutral (no
       length claim) and unknown/nodata are a faint gray stub
       (gray-never-green for empty, the v2.3.0 honesty rule).
       Rows are clickable -> hass-more-info on the consensus
       entity, same delegation as the gauge's per-source breakdown. */
    .bars-empty {
      padding: 16px;
      color: var(--secondary-text-color, #7C8794);
      font-size: 13px;
      text-align: center;
    }
    .bars-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: 4px 12px;
    }
    .bar-row {
      display: grid;
      grid-template-columns: minmax(72px, 30%) 1fr auto;
      align-items: center;
      gap: 8px;
      padding: 4px 6px;
      min-height: 26px;
      border-radius: 6px;
      cursor: pointer;
      background: transparent;
      border: none;
      color: inherit;
      font: inherit;
      text-align: left;
      width: 100%;
    }
    .bar-row:hover, .bar-row:focus-visible {
      background: var(--secondary-background-color, rgba(0,0,0,0.04));
      outline: none;
    }
    .bar-name {
      font-size: 13px;
      font-weight: 500;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      text-transform: capitalize;
    }
    .bar-track {
      position: relative;
      height: 8px;
      border-radius: 999px;
      background: var(--divider-color, #ECE4D6);
      overflow: hidden;
    }
    .bar-fill {
      position: absolute;
      inset: 0 auto 0 0;
      width: 0%;
      background: var(--disabled-color, #AEB7C0);
      border-radius: 999px;
      transition: width 240ms cubic-bezier(.32,.72,.30,1),
                  background-color 200ms;
    }
    /* Mixed: hatched stripe in neutral muted token. Rhymes with the
       gauge's mixed-mark (3 gray dots + hollow hub) — neutral colour,
       no magnitude claim. Track fully filled so the stripe is visible. */
    .bar-row.state-mixed .bar-fill {
      width: 100%;
      background: repeating-linear-gradient(
        45deg,
        var(--secondary-text-color, #7C8794) 0 4px,
        var(--divider-color, #ECE4D6) 4px 8px
      );
      opacity: 0.85;
    }
    /* Unknown / nodata: faint gray stub (5%), never green. Honest
       absence — the gauge's dashed/faint arc analogue. */
    .bar-row.state-unknown .bar-fill,
    .bar-row.state-nodata .bar-fill {
      width: 5%;
      background: var(--disabled-color, #AEB7C0);
      opacity: 0.6;
    }
    /* Visible-but-empty 'none' row: no fill width, but the row still
       reads as "actively zero" via the green level word. show_inactive
       gates whether this row renders at all; when it does, it stays
       visually present without a coloured bar. */
    .bar-row.state-none .bar-fill { width: 0%; }
    .bar-level {
      font-size: 12px;
      color: var(--secondary-text-color, #7C8794);
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    /* Level-word colour echoes the gauge's reading-label rule. */
    .bar-row.state-none  .bar-level-word { color: #3DAE5A; }
    .bar-row.state-low   .bar-level-word { color: #F2A516; }
    .bar-row.state-high  .bar-level-word { color: #E0492E; }
    .bar-row.state-mixed .bar-level-word { color: var(--primary-text-color, #2A3540); }
    /* unknown/nodata: secondary-text (default) — no override needed. */

    /* === Compact overview layout (v2.4+) =========================
       Dense dot-grid: severity DOT + species name + level word.
       Denser than bars (more columns, smaller rows) for users with
       many configured species. The dot is the magnitude channel;
       the level word is the redundant text channel. show_inactive
       gates the same way (hide none, keep unknown/nodata).
       Mixed-dot hatch RHYMES with bars' hatched fill (same 45°
       direction, same --secondary-text-color × --divider-color
       neutral pair) — finer pitch because the surface is ~11px,
       not full-width. Cross-layout consistency: a user reading
       'stripes = sources disagree' in bars sees the same word
       in compact. */
    .compact-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
      gap: 2px 12px;
    }
    .compact-row {
      display: grid;
      grid-template-columns: auto 1fr auto;
      align-items: center;
      gap: 8px;
      padding: 3px 6px;
      min-height: 22px;
      border-radius: 4px;
      cursor: pointer;
      background: transparent;
      border: none;
      color: inherit;
      font: inherit;
      text-align: left;
      width: 100%;
    }
    .compact-row:hover, .compact-row:focus-visible {
      background: var(--secondary-background-color, rgba(0,0,0,0.04));
      outline: none;
    }
    .compact-dot {
      width: 11px;
      height: 11px;
      border-radius: 999px;
      flex-shrink: 0;
      background: var(--disabled-color, #AEB7C0);
    }
    /* Severity-tinted dots reuse the gauge/bars palette — single source
       of truth for the severity colour scale. */
    .compact-row.state-none  .compact-dot { background: #3DAE5A; }
    .compact-row.state-low   .compact-dot { background: #F2A516; }
    .compact-row.state-high  .compact-dot { background: #E0492E; }
    /* Mixed: hatched stripe. Same 45° + neutral token pair as bars,
       at 3px period (1.5px stripe / 1.5px gap) so the stripe direction
       is legible on the 11px dot. If this reads as muddy gray rather
       than crisp stripes at real size, bump the dot to ~13px OR coarsen
       to 4px period — do NOT abandon the hatch (cross-layout consistency
       point). 0.85 opacity matches bars. */
    .compact-row.state-mixed .compact-dot {
      background: repeating-linear-gradient(
        45deg,
        var(--secondary-text-color, #7C8794) 0 1.5px,
        var(--divider-color, #ECE4D6) 1.5px 3px
      );
      opacity: 0.85;
    }
    /* Unknown / nodata: faint gray dot, never green. The v2.3.0
       honesty rule — empty readings are gray, not the green of
       'actively zero'. */
    .compact-row.state-unknown .compact-dot,
    .compact-row.state-nodata  .compact-dot {
      background: var(--disabled-color, #AEB7C0);
      opacity: 0.6;
    }
    .compact-name {
      font-size: 13px;
      font-weight: 500;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      text-transform: capitalize;
    }
    .compact-level {
      font-size: 12px;
      color: var(--secondary-text-color, #7C8794);
      white-space: nowrap;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    /* Level-word colour mirrors bars + the gauge's reading-label rule. */
    .compact-row.state-none  .compact-level-word { color: #3DAE5A; }
    .compact-row.state-low   .compact-level-word { color: #F2A516; }
    .compact-row.state-high  .compact-level-word { color: #E0492E; }
    .compact-row.state-mixed .compact-level-word { color: var(--primary-text-color, #2A3540); }
    /* unknown/nodata: secondary-text (default) — no override needed. */

    /* === Tiles overview layout (v2.4+) ===========================
       Per-species tile: severity-tinted icon + species name + level
       word, in a grid of clickable cards. Denser than bars (visual,
       parallel scan) and more visual than compact (icon-bearing).

       DELIBERATE OVERLAP — this layout knowingly resembles
       oriel-dashboard's consensus_tiles. Intentional, per
       docs/MULTISPECIES_CARD_PLAN.md: standalone completeness for
       the HACS user without oriel. Zero shared code (vanilla JS
       here, TS/Lit in oriel) — parity by design, not duplication.
       Don't "fix" this.

       Severity routing (Option 2, consistent with bars+compact):
         - Tile background stays NEUTRAL so the icon reads clearly.
         - Severity rides a 4px left-edge accent stripe (single-source
           palette: same SEG_COLORS / bars-fill / compact-dot tokens),
           the icon's --pw-grain-fill tint (same pastel palette as the
           gauge's .card.state-* rules), and the level-word colour.
         - Mixed = hatched accent stripe, 45° in the same neutral
           token pair as bars (#secondary-text x #divider), 4px period
           (matches bars' track-fill stripe — same surface scale, same
           hatch reading). Rhymes with bars+compact's "stripes =
           sources disagree" word.
         - Unknown/nodata = faint gray stripe + neutral icon tint,
           never green (v2.3.0 honesty rule). */
    .tiles-empty {
      padding: 16px;
      color: var(--secondary-text-color, #7C8794);
      font-size: 13px;
      text-align: center;
    }
    .tiles-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
      gap: 8px;
    }
    .tile {
      position: relative;
      display: grid;
      grid-template-columns: 28px 1fr;
      grid-template-areas:
        "icon name"
        "icon level";
      align-items: center;
      gap: 2px 10px;
      padding: 10px 12px 10px 14px;
      min-height: 56px;
      border: 1px solid var(--divider-color, #ECE4D6);
      border-radius: 10px;
      background: var(--ha-card-background, var(--card-background-color, white));
      color: inherit;
      font: inherit;
      text-align: left;
      cursor: pointer;
      overflow: hidden;
    }
    .tile:hover, .tile:focus-visible {
      outline: none;
      border-color: var(--secondary-text-color, #7C8794);
      background: var(--secondary-background-color, rgba(0,0,0,0.03));
    }
    /* Accent stripe — runs full tile height at the left edge.
       Default token is the neutral disabled-color (a tile with no
       resolved state class is treated as nodata-equivalent). */
    .tile::before {
      content: "";
      position: absolute;
      left: 0;
      top: 0;
      bottom: 0;
      width: 4px;
      background: var(--disabled-color, #AEB7C0);
    }
    .tile .species-icon {
      grid-area: icon;
      width: 28px;
      height: 28px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      --pw-grain-stroke: var(--primary-text-color, #2A3540);
      --pw-grain-fill: var(--divider-color, #E8EBEE);
    }
    .tile .species-icon svg { width: 100%; height: 100%; }
    .tile-name {
      grid-area: name;
      font-size: 13px;
      font-weight: 600;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      text-transform: capitalize;
    }
    .tile-level {
      grid-area: level;
      font-size: 12px;
      color: var(--secondary-text-color, #7C8794);
      white-space: nowrap;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    /* Severity tints — single source of truth for the severity scale.
       Stripe colour = SEG_COLORS (same as bars-fill + compact-dot).
       Icon fill = same pastel palette as gauge's .card.state-* rules. */
    .tile.state-none::before { background: #3DAE5A; }
    .tile.state-low::before  { background: #F2A516; }
    .tile.state-high::before { background: #E0492E; }
    .tile.state-none  .species-icon { --pw-grain-fill: #C8E6CF; }
    .tile.state-low   .species-icon { --pw-grain-fill: #FCE5B8; }
    .tile.state-high  .species-icon { --pw-grain-fill: #F4C6BA; }
    /* Mixed: hatched accent stripe in neutral token pair. Same 45° as
       bars+compact, 4px period (matches bars exactly — accent stripe
       and bars' track-fill are the same "thin coloured band" surface
       so the hatch reads the same way). */
    .tile.state-mixed::before {
      background: repeating-linear-gradient(
        45deg,
        var(--secondary-text-color, #7C8794) 0 2px,
        var(--divider-color, #ECE4D6) 2px 4px
      );
      opacity: 0.9;
    }
    .tile.state-mixed .species-icon {
      --pw-grain-fill: var(--divider-color, #ECE4D6);
    }
    /* Unknown / nodata: faint gray stripe, never green. Honesty rule. */
    .tile.state-unknown::before,
    .tile.state-nodata::before {
      background: var(--disabled-color, #AEB7C0);
      opacity: 0.6;
    }
    .tile.state-unknown .species-icon,
    .tile.state-nodata  .species-icon {
      --pw-grain-fill: var(--divider-color, #ECE4D6);
      opacity: 0.7;
    }
    /* Level-word colour — mirrors bars + compact + gauge reading-label. */
    .tile.state-none  .tile-level-word { color: #3DAE5A; }
    .tile.state-low   .tile-level-word { color: #F2A516; }
    .tile.state-high  .tile-level-word { color: #E0492E; }
    .tile.state-mixed .tile-level-word { color: var(--primary-text-color, #2A3540); }
    /* unknown/nodata: secondary-text (default) — no override needed. */

    @media (prefers-reduced-motion: reduce) {
      .bar-fill { transition: none !important; }
    }
  `;

  // ── Bars layout (v2.4+) ──────────────────────────────────────────
  // Multi-species overview: one row per species — species name + filled
  // severity track + level word + provenance marker (reused from v2.3).
  //
  // Fill is severity-tier-fill, NOT a measurement (the consensus is a
  // 4-state enum). The bar is DOUBLE-CODED: both length and colour
  // signal the same tier, so the bar is a sort-cue plus colour redundancy,
  // never a false-precision magnitude claim. Mixed gets a hatched stripe
  // in a neutral token — rhymes with the gauge's mixed-mark (3 gray
  // dots, hollow hub) by being neutral and length-less.
  //
  // Discovery + render order: explicit YAML species[] > WS-discovered
  // CONF_SELECTED_SPECIES > hass.states scan. show_inactive (default
  // false) hides species at none — unknown/nodata still render because
  // no-data is information, not absence (matches gauge's gray-never-green
  // for empty rule).
  const BARS_FILL_PCT = {
    // Severity tier → bar fill percentage. Fixed steps. The exact numbers
    // are chosen for visual differentiation, not numeric correctness — see
    // the comment above; this is presentational only.
    none: 0,
    low: 34,
    high: 100,
  };
  // Same severity palette the gauge uses (SEG_COLORS). Keeping it
  // duplicated as named constants here makes the bar -> colour
  // intention explicit at the call site.
  const BARS_FILL_COLOR = {
    none: '#3DAE5A',  // green
    low: '#F2A516',   // amber
    high: '#E0492E',  // red
  };
  const BARS_LEVEL_LABEL = {
    none: 'None',
    low: 'In season',
    high: 'At peak',
    mixed: 'Sources disagree',
    unknown: 'Unknown',
    nodata: 'No data',
  };

  // ── Card class ────────────────────────────────────────────────────
  class PollenWatchCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: 'open' });
    }

    setConfig(config) {
      if (!config || typeof config !== 'object') {
        throw new Error('pollenwatch-card: config object required');
      }
      // YAML override of layout — explicit beats the integration default.
      // Unknown values are rejected here so a typo (`layout: tile`) surfaces
      // as a setup error rather than a silent fallback to gauge.
      const yamlLayout = config.layout;
      if (yamlLayout !== undefined && !ALLOWED_LAYOUTS.has(yamlLayout)) {
        throw new Error(
          `pollenwatch-card: layout must be one of ${[...ALLOWED_LAYOUTS].join(', ')} ` +
          `(got ${JSON.stringify(yamlLayout)})`
        );
      }
      // Gauge mode keeps its v0.3.0 contract: `species` is required because
      // there is no discovery — a single sensor per card. Overview modes
      // may omit `species` (discovery fills it in) but accept it as a
      // curated override.
      const isOverviewYaml = OVERVIEW_LAYOUTS.has(yamlLayout);
      if (!isOverviewYaml && !config.species) {
        // Gauge layout (default OR explicit) requires species.
        throw new Error(
          'pollenwatch-card: species is required for layout: gauge ' +
          '(any canonical species from species_registry; e.g. grass, birch)'
        );
      }
      const explicitSpecies = Array.isArray(config.species)
        ? config.species.slice()
        : (typeof config.species === 'string' ? [config.species] : null);
      this._config = {
        show_mixed_span: false,
        expanded_default: false,
        show_inactive: false,
        ...config,
        // Normalised forms — single source of truth downstream.
        _yamlLayout: yamlLayout || null,
        _explicitSpecies: explicitSpecies,
      };
      this._expanded = !!this._config.expanded_default;
      // Discovery state: filled in on first `set hass` either from WS or
      // from a hass.states scan. Null until then; gauge mode doesn't need it.
      this._discoveredLayout = null;     // integration's CONF_DEFAULT_LAYOUT
      this._discoveredSpecies = null;    // CONF_SELECTED_SPECIES (overview only)
      this._discoveryPromise = null;     // single in-flight WS call
      this._resolvedLayout = this._resolveLayout();  // best-effort sync
      this._build();
    }

    // Resolution order (locked, per docs/MULTISPECIES_CARD_PLAN.md):
    //   1. explicit per-card YAML `layout:` — power-user, per-card control
    //   2. integration's CONF_DEFAULT_LAYOUT (from pollenwatch/config WS)
    //   3. 'gauge' — final fallback so a fresh install or WS failure
    //      keeps the pre-v2.4 visual the user already knows.
    // Mirrors the species override pattern (explicit YAML > discovery > default).
    _resolveLayout() {
      if (this._config._yamlLayout) return this._config._yamlLayout;
      if (this._discoveredLayout && ALLOWED_LAYOUTS.has(this._discoveredLayout)) {
        return this._discoveredLayout;
      }
      return LAYOUT_GAUGE;
    }

    // Species list for overview mode. Same layered rule: explicit YAML
    // species: [...] beats WS-discovered selected_species beats the
    // hass.states scan (older integration / WS failure fallback).
    _resolveSpecies() {
      if (this._config._explicitSpecies) return this._config._explicitSpecies;
      if (this._discoveredSpecies) return this._discoveredSpecies;
      return this._scanSpecies();
    }

    // Fallback discovery: scan hass.states for analytics-consensus sensor IDs.
    // Used when the WS command isn't available (older integration) OR has
    // errored. Merges species across multiple config entries (cannot
    // distinguish entries here); the YAML override is the documented
    // disambiguation for multi-entry installs.
    _scanSpecies() {
      const states = this._hass?.states;
      if (!states) return [];
      const prefix = 'sensor.pollenwatch_analytics_';
      const suffix = '_consensus';
      const out = [];
      for (const id of Object.keys(states)) {
        if (id.startsWith(prefix) && id.endsWith(suffix)) {
          out.push(id.slice(prefix.length, id.length - suffix.length));
        }
      }
      out.sort();
      return out;
    }

    // Async one-shot resolution of the integration's CONF_SELECTED_SPECIES
    // and CONF_DEFAULT_LAYOUT via the pollenwatch/config WS command. Runs
    // on first hass-set per session; subsequent state pushes use cached
    // values. Failure (no command, no matching entry) is silent — the
    // resolvers fall back per their layered rule.
    async _ensureDiscovery() {
      if (this._discoveryPromise) return this._discoveryPromise;
      if (!this._hass?.callWS) {
        // No callWS — pre-modern HA. Discovery is the scan fallback only.
        this._discoveryPromise = Promise.resolve();
        return this._discoveryPromise;
      }
      // Pick an entry_id: prefer the first PollenWatch config entry visible
      // via hass.entries. HA exposes entries on `hass.config.entries`? No —
      // they're on `hass.entries` keyed by entry_id, OR fetched via the
      // config_entries/get WS command. Cleanest: ask for the list via WS,
      // pick the first pollenwatch entry. If none, give up gracefully.
      this._discoveryPromise = (async () => {
        try {
          const entries = await this._hass.callWS({
            type: 'config_entries/get',
            domain: 'pollenwatch',
          });
          if (!Array.isArray(entries) || entries.length === 0) return;
          const entryId = entries[0].entry_id;
          const result = await this._hass.callWS({
            type: 'pollenwatch/config',
            entry_id: entryId,
          });
          if (result?.selected_species) {
            this._discoveredSpecies = result.selected_species.slice();
          }
          if (result?.default_layout) {
            this._discoveredLayout = result.default_layout;
          }
          // Re-evaluate the resolved layout; if it changed, rebuild.
          const next = this._resolveLayout();
          if (next !== this._resolvedLayout) {
            this._resolvedLayout = next;
            this._build();
          }
        } catch (_e) {
          // Endpoint absent / errored — fall back to scan + gauge per the
          // resolution rules. Deliberately silent; this is expected on
          // older integrations or transient WS failures.
        }
      })();
      return this._discoveryPromise;
    }

    set hass(hass) {
      const wasUnset = !this._hass;
      this._hass = hass;
      // Kick off discovery once per session. Overview mode needs it for
      // species + layout resolution; gauge mode benefits too because a
      // future YAML edit can flip the layout without re-fetching. Silent
      // on failure — _resolveLayout / _resolveSpecies degrade gracefully.
      if (wasUnset) this._ensureDiscovery();
      if (this.shadowRoot.childElementCount > 0) this._render();
    }

    getCardSize() { return 4; }

    static getStubConfig() {
      return { type: 'custom:pollenwatch-card', species: 'grass' };
    }

    _build() {
      // Layout dispatch. Every ALLOWED_LAYOUT has its own handler now;
      // the trailing _buildGauge() is a belt-and-braces default for an
      // unrecognised layout string slipping past setConfig + _resolveLayout
      // (both of which gate on ALLOWED_LAYOUTS).
      const layout = this._resolvedLayout;
      if (layout === LAYOUT_BARS) {
        this._buildBars();
        return;
      }
      if (layout === LAYOUT_COMPACT) {
        this._buildCompact();
        return;
      }
      if (layout === LAYOUT_TILES) {
        this._buildTiles();
        return;
      }
      this._buildGauge();
    }

    _buildGauge() {
      const species = this._config.species;
      this.shadowRoot.innerHTML = `
        <style>${CARD_CSS}</style>
        <ha-card class="card" data-card>
          <div class="header">
            <span class="species-icon" data-icon aria-hidden="true"></span>
            <div class="title">${cap(species)} pollen</div>
            <div class="meta">
              <span class="badge" data-badge></span>
              <span class="single-source-label">single source</span>
              <span class="provenance-marker" data-provenance hidden role="img" tabindex="0" title="" aria-label=""><span class="sr-only" data-provenance-text></span></span>
              <span class="meta-time" data-time></span>
            </div>
          </div>
          <div class="gauge-wrap">${gaugeSvg()}</div>
          <div class="reading" data-reading>
            <div class="reading-label" data-label>—</div>
            <div class="reading-sub" data-sub>Loading…</div>
          </div>
          <button class="toggle" data-toggle aria-expanded="${this._expanded}">${this._expanded ? 'Hide' : 'Show'} sources</button>
          <div class="breakdown${this._expanded ? ' open' : ''}" data-breakdown></div>
        </ha-card>
      `;
      const btn = this.shadowRoot.querySelector('[data-toggle]');
      const bd = this.shadowRoot.querySelector('[data-breakdown]');
      btn.addEventListener('click', () => {
        this._expanded = !this._expanded;
        bd.classList.toggle('open', this._expanded);
        btn.textContent = (this._expanded ? 'Hide' : 'Show') + ' sources';
        btn.setAttribute('aria-expanded', String(this._expanded));
      });
      // Async-load species icon. Inlined so CSS vars cascade for tinting.
      const iconHolder = this.shadowRoot.querySelector('[data-icon]');
      loadIcon(species).then((svg) => {
        if (svg && iconHolder) iconHolder.innerHTML = svg;
      });
    }

    _render() {
      if (!this._hass || !this._config) return;
      if (this._resolvedLayout === LAYOUT_BARS) {
        this._renderBars();
        return;
      }
      if (this._resolvedLayout === LAYOUT_COMPACT) {
        this._renderCompact();
        return;
      }
      if (this._resolvedLayout === LAYOUT_TILES) {
        this._renderTiles();
        return;
      }
      this._renderGauge();
    }

    _renderGauge() {
      const species = this._config.species;
      const {
        state, source_levels = {}, source_count, max_possible, last_changed,
      } = resolveState(this._hass, species);
      const recipe = STATE_RECIPE[state];

      // Card-level state + source-count classes drive icon tint, single-
      // source humbling, and badge styling. One class for state, one for
      // source-count (1 vs >1 — only the 1-case gets explicit humbling
      // per the locked principle "fewer sources = humbler treatment").
      const card = this.shadowRoot.querySelector('[data-card]');
      const sc = source_count ?? 0;
      const scClass = sc === 1 ? 'source-count-1' : `source-count-multi`;
      card.setAttribute('class', `card state-${state} ${scClass}`);

      // Gauge state
      const gauge = this.shadowRoot.querySelector('.pwgauge');
      gauge.setAttribute('class', `pwgauge state-${state}`);

      // Reading
      const reading = this.shadowRoot.querySelector('[data-reading]');
      reading.setAttribute('class', `reading state-${state}`);
      this.shadowRoot.querySelector('[data-label]').textContent = recipe.label;

      let sub = recipe.sub;
      if (state === 'mixed' && this._config.show_mixed_span) {
        const levels = Object.values(source_levels);
        if (levels.length) {
          const lo = Math.min(...levels), hi = Math.max(...levels);
          sub = `${LEVEL_NAMES[lo]}–${LEVEL_NAMES[hi]} · across ${levels.length} sources`;
        }
      }
      this.shadowRoot.querySelector('[data-sub]').textContent = sub;

      // Badge: numeric n/m (Q5 decision — no dot row). Empty if there's
      // no consensus sensor (state == nodata with no source_count).
      const badgeEl = this.shadowRoot.querySelector('[data-badge]');
      const mp = max_possible || sc;
      badgeEl.textContent = (sc > 0 && mp > 0) ? `${sc}/${mp}` : '';

      // Relative time stays as a separate meta element.
      const timeEl = this.shadowRoot.querySelector('[data-time]');
      const ago = relTime(last_changed);
      timeEl.textContent = ago || '';

      // v2.3+: threshold provenance marker. The helper is the single
      // place that branches on threshold_basis; this site only writes
      // DOM. Missing consensus entity -> basis undefined -> no marker
      // (same nodata-style fallback as the rest of the card).
      const consensusEnt = this._hass.states[
        `sensor.pollenwatch_analytics_${species}_consensus`
      ];
      const basis = consensusEnt?.attributes?.threshold_basis;
      const marker = provenanceMarker(basis);
      const markerEl = this.shadowRoot.querySelector('[data-provenance]');
      const markerText = this.shadowRoot.querySelector('[data-provenance-text]');
      if (marker) {
        markerEl.hidden = false;
        markerEl.title = marker.tooltip;
        markerEl.setAttribute('aria-label', marker.ariaLabel);
        markerText.textContent = marker.ariaLabel;
      } else {
        markerEl.hidden = true;
        markerEl.title = '';
        markerEl.setAttribute('aria-label', '');
        markerText.textContent = '';
      }

      // Per-source breakdown
      const rows = perSourceRows(this._hass, species, source_levels);
      const bd = this.shadowRoot.querySelector('[data-breakdown]');
      bd.innerHTML = rows.length === 0
        ? `<div class="source-row"><span class="source-value missing">No source data</span></div>`
        : rows.map(r => `
          <div class="source-row">
            <span class="level-dot" style="background: ${LEVEL_COLORS[r.level] || '#AEB7C0'}"></span>
            <span class="source-label">${r.label}</span>
            <span class="source-value${r.missing ? ' missing' : ''}">${
              r.missing
                ? 'missing'
                : (r.value + (r.unit ? ' <small>' + r.unit + '</small>' : ''))
            }</span>
          </div>
        `).join('');
    }

    // ── Bars layout ────────────────────────────────────────────────
    _buildBars() {
      // Single grid container; rows are rendered in _renderBars() and
      // re-rendered on every state push. Keeping the DOM cheap: no
      // per-row event listeners attached here — delegated via the
      // grid's click handler.
      this.shadowRoot.innerHTML = `
        <style>${CARD_CSS}</style>
        <ha-card class="card">
          ${this._config.title ? `<div class="header"><div class="title">${this._config.title}</div></div>` : ''}
          <div class="bars-grid" data-bars></div>
        </ha-card>
      `;
      const grid = this.shadowRoot.querySelector('[data-bars]');
      grid.addEventListener('click', (e) => {
        const row = e.target.closest('.bar-row[data-entity]');
        if (!row) return;
        this._fireMoreInfo(row.getAttribute('data-entity'));
      });
      grid.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        const row = e.target.closest('.bar-row[data-entity]');
        if (!row) return;
        e.preventDefault();
        this._fireMoreInfo(row.getAttribute('data-entity'));
      });
    }

    _renderBars() {
      const grid = this.shadowRoot.querySelector('[data-bars]');
      if (!grid) return;
      const species = this._resolveSpecies();
      if (species.length === 0) {
        // Pre-discovery (WS in-flight, scan empty) OR a truly empty
        // install. Render a soft hint, not a broken layout — discovery
        // will re-trigger _build() once it resolves and replace this.
        grid.innerHTML = `<div class="bars-empty">No PollenWatch species found yet.</div>`;
        return;
      }
      // Map species → consensus state (uses the existing gauge resolver
      // so the bars layout sees the SAME state machine as the gauge —
      // single source of truth for none/low/high/mixed/unknown/nodata).
      const rows = species.map((s) => {
        const { state, source_count, max_possible } = resolveState(this._hass, s);
        const consensusId = `sensor.pollenwatch_analytics_${s}_consensus`;
        const consensus = this._hass.states[consensusId];
        const basis = consensus?.attributes?.threshold_basis;
        return { species: s, state, source_count, max_possible, consensusId, basis };
      });
      // show_inactive: false (default) hides ONLY `none` rows. unknown
      // and nodata still render — no-data is information, not absence
      // (matches the gauge's reading-label gray-never-green rule).
      const visible = this._config.show_inactive
        ? rows
        : rows.filter((r) => r.state !== 'none');
      if (visible.length === 0) {
        grid.innerHTML = `<div class="bars-empty">All clear — no active pollen today.</div>`;
        return;
      }
      grid.innerHTML = visible.map((r) => this._renderBarRow(r)).join('');
    }

    _renderBarRow({ species, state, consensusId, basis }) {
      const levelWord = BARS_LEVEL_LABEL[state] || state;
      // Fill width + colour are tier-coded (severity tokens). Mixed
      // and unknown/nodata override via the CSS state class — leave the
      // inline style empty for those so the stylesheet rules apply.
      const isPlain = state === 'none' || state === 'low' || state === 'high';
      const widthPct = isPlain ? BARS_FILL_PCT[state] : 0;
      const fillColor = isPlain ? BARS_FILL_COLOR[state] : '';
      const fillStyle = isPlain
        ? `width:${widthPct}%;background:${fillColor};`
        : '';
      // Provenance marker — reuse the v2.3 helper + strings. Rendered
      // INSIDE the .bar-level span so it sits next to the level word
      // visually, same family/estimated -> marker rule as the gauge.
      const marker = provenanceMarker(basis);
      const markerHtml = marker
        ? `<span class="provenance-marker" role="img" tabindex="0" ` +
          `title="${this._escAttr(marker.tooltip)}" ` +
          `aria-label="${this._escAttr(marker.ariaLabel)}">` +
          `<span class="sr-only">${this._escText(marker.ariaLabel)}</span>` +
          `</span>`
        : '';
      return `
        <button class="bar-row state-${state}" data-entity="${consensusId}"
                role="button" tabindex="0"
                aria-label="${this._escAttr(cap(species))}: ${this._escAttr(levelWord)}">
          <span class="bar-name">${cap(species)}</span>
          <span class="bar-track"><span class="bar-fill" style="${fillStyle}"></span></span>
          <span class="bar-level">
            <span class="bar-level-word">${levelWord}</span>
            ${markerHtml}
          </span>
        </button>
      `;
    }

    // ── Compact layout ─────────────────────────────────────────────
    // Mirrors _buildBars/_renderBars structurally — grid container
    // built once, rows re-rendered on each state push, single delegated
    // click/keydown handler. Compact's only differences from bars are
    // (a) the dot replaces the track-and-fill and (b) denser grid
    // columns. All discovery + filtering + state-resolution + marker +
    // more-info delegation is reused unchanged.
    _buildCompact() {
      this.shadowRoot.innerHTML = `
        <style>${CARD_CSS}</style>
        <ha-card class="card">
          ${this._config.title ? `<div class="header"><div class="title">${this._config.title}</div></div>` : ''}
          <div class="compact-grid" data-compact></div>
        </ha-card>
      `;
      const grid = this.shadowRoot.querySelector('[data-compact]');
      grid.addEventListener('click', (e) => {
        const row = e.target.closest('.compact-row[data-entity]');
        if (!row) return;
        this._fireMoreInfo(row.getAttribute('data-entity'));
      });
      grid.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        const row = e.target.closest('.compact-row[data-entity]');
        if (!row) return;
        e.preventDefault();
        this._fireMoreInfo(row.getAttribute('data-entity'));
      });
    }

    _renderCompact() {
      const grid = this.shadowRoot.querySelector('[data-compact]');
      if (!grid) return;
      const species = this._resolveSpecies();
      if (species.length === 0) {
        grid.innerHTML = `<div class="bars-empty">No PollenWatch species found yet.</div>`;
        return;
      }
      const rows = species.map((s) => {
        const { state } = resolveState(this._hass, s);
        const consensusId = `sensor.pollenwatch_analytics_${s}_consensus`;
        const consensus = this._hass.states[consensusId];
        const basis = consensus?.attributes?.threshold_basis;
        return { species: s, state, consensusId, basis };
      });
      // Same show_inactive rule as bars: hide ONLY level=none rows;
      // unknown/nodata still render because no-data is information,
      // not absence (gauge's gray-never-green-for-empty rule).
      const visible = this._config.show_inactive
        ? rows
        : rows.filter((r) => r.state !== 'none');
      if (visible.length === 0) {
        grid.innerHTML = `<div class="bars-empty">All clear — no active pollen today.</div>`;
        return;
      }
      grid.innerHTML = visible.map((r) => this._renderCompactRow(r)).join('');
    }

    _renderCompactRow({ species, state, consensusId, basis }) {
      const levelWord = BARS_LEVEL_LABEL[state] || state;
      const marker = provenanceMarker(basis);
      const markerHtml = marker
        ? `<span class="provenance-marker" role="img" tabindex="0" ` +
          `title="${this._escAttr(marker.tooltip)}" ` +
          `aria-label="${this._escAttr(marker.ariaLabel)}">` +
          `<span class="sr-only">${this._escText(marker.ariaLabel)}</span>` +
          `</span>`
        : '';
      return `
        <button class="compact-row state-${state}" data-entity="${consensusId}"
                role="button" tabindex="0"
                aria-label="${this._escAttr(cap(species))}: ${this._escAttr(levelWord)}">
          <span class="compact-dot" aria-hidden="true"></span>
          <span class="compact-name">${cap(species)}</span>
          <span class="compact-level">
            <span class="compact-level-word">${levelWord}</span>
            ${markerHtml}
          </span>
        </button>
      `;
    }

    // ── Tiles layout ───────────────────────────────────────────────
    // Mirrors _buildBars / _buildCompact structurally — grid built
    // once, tiles re-rendered on each state push, single delegated
    // click + keydown. Tiles bear icons (the gauge's loadIcon path);
    // discovery, filtering, state resolution, provenance markers,
    // and more-info delegation are all reused unchanged.
    //
    // DELIBERATE OVERLAP — see the .tiles-grid CSS comment for why
    // this layout intentionally rhymes with oriel-dashboard's
    // consensus_tiles. Zero shared code; parity by design.
    _buildTiles() {
      this.shadowRoot.innerHTML = `
        <style>${CARD_CSS}</style>
        <ha-card class="card">
          ${this._config.title ? `<div class="header"><div class="title">${this._config.title}</div></div>` : ''}
          <div class="tiles-grid" data-tiles></div>
        </ha-card>
      `;
      const grid = this.shadowRoot.querySelector('[data-tiles]');
      grid.addEventListener('click', (e) => {
        const tile = e.target.closest('.tile[data-entity]');
        if (!tile) return;
        this._fireMoreInfo(tile.getAttribute('data-entity'));
      });
      grid.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        const tile = e.target.closest('.tile[data-entity]');
        if (!tile) return;
        e.preventDefault();
        this._fireMoreInfo(tile.getAttribute('data-entity'));
      });
    }

    _renderTiles() {
      const grid = this.shadowRoot.querySelector('[data-tiles]');
      if (!grid) return;
      const species = this._resolveSpecies();
      if (species.length === 0) {
        grid.innerHTML = `<div class="tiles-empty">No PollenWatch species found yet.</div>`;
        return;
      }
      const rows = species.map((s) => {
        const { state } = resolveState(this._hass, s);
        const consensusId = `sensor.pollenwatch_analytics_${s}_consensus`;
        const consensus = this._hass.states[consensusId];
        const basis = consensus?.attributes?.threshold_basis;
        return { species: s, state, consensusId, basis };
      });
      // Same show_inactive rule as bars + compact: hide ONLY level=none
      // tiles; unknown/nodata still render (no-data is information).
      const visible = this._config.show_inactive
        ? rows
        : rows.filter((r) => r.state !== 'none');
      if (visible.length === 0) {
        grid.innerHTML = `<div class="tiles-empty">All clear — no active pollen today.</div>`;
        return;
      }
      grid.innerHTML = visible.map((r) => this._renderTile(r)).join('');
      // Tiles bear icons. Inline each one async, reusing the gauge's
      // module-scoped ICON_CACHE — one fetch per species across the
      // whole page, regardless of how many cards exist.
      visible.forEach((r) => {
        const holder = grid.querySelector(`[data-tile-icon="${r.species}"]`);
        if (!holder) return;
        loadIcon(r.species).then((svg) => {
          if (svg && holder) holder.innerHTML = svg;
        });
      });
    }

    _renderTile({ species, state, consensusId, basis }) {
      const levelWord = BARS_LEVEL_LABEL[state] || state;
      const marker = provenanceMarker(basis);
      const markerHtml = marker
        ? `<span class="provenance-marker" role="img" tabindex="0" ` +
          `title="${this._escAttr(marker.tooltip)}" ` +
          `aria-label="${this._escAttr(marker.ariaLabel)}">` +
          `<span class="sr-only">${this._escText(marker.ariaLabel)}</span>` +
          `</span>`
        : '';
      return `
        <button class="tile state-${state}" data-entity="${consensusId}"
                role="button" tabindex="0"
                aria-label="${this._escAttr(cap(species))}: ${this._escAttr(levelWord)}">
          <span class="species-icon" data-tile-icon="${this._escAttr(species)}" aria-hidden="true"></span>
          <span class="tile-name">${cap(species)}</span>
          <span class="tile-level">
            <span class="tile-level-word">${levelWord}</span>
            ${markerHtml}
          </span>
        </button>
      `;
    }

    _fireMoreInfo(entityId) {
      const event = new Event('hass-more-info', { bubbles: true, composed: true });
      event.detail = { entityId };
      this.dispatchEvent(event);
    }

    // Minimal HTML escaping for attribute / text content inserted via
    // innerHTML. The set of values is small and trusted (level words,
    // capitalized species names, provenance strings from a constant) but
    // species can be user-supplied via YAML so we escape defensively.
    _escAttr(s) {
      return String(s ?? '')
        .replaceAll('&', '&amp;').replaceAll('"', '&quot;')
        .replaceAll('<', '&lt;').replaceAll('>', '&gt;');
    }
    _escText(s) {
      return String(s ?? '')
        .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
    }
  }

  if (!customElements.get('pollenwatch-card')) {
    customElements.define('pollenwatch-card', PollenWatchCard);
  }

  // Register with HA's custom-card catalog so it shows up in the card picker
  window.customCards = window.customCards || [];
  if (!window.customCards.some(c => c.type === 'pollenwatch-card')) {
    window.customCards.push({
      type: 'pollenwatch-card',
      name: 'PollenWatch',
      description: 'Combined-consensus severity gauge per allergen.',
      preview: false,
    });
  }

  /* eslint-disable no-console */
  console.info(
    `%c pollenwatch-card %c v${CARD_VERSION} `,
    'background:#E8920C;color:#FBF7F0;font-weight:600;padding:2px 6px;border-radius:3px 0 0 3px;',
    'background:#33414F;color:#FBF7F0;padding:2px 6px;border-radius:0 3px 3px 0;'
  );
})();
