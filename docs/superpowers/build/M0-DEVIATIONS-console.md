# M0 deviations: Privacy Console (Tasks 25 and 26)

This file lists every place where the console build differs from
`docs/superpowers/plans/2026-10-11-m0-foundation-engine-api-playground.md` (Tasks 25 and 26), and why.
Each file not listed below matches the plan's text exactly.

## Task 25

### D1: Stylesheet step deletes only the two mock-banner lines

The plan says to run:

```bash
sed -i '' '/^\.mock-banner/,/^}/d' frontend/src/styles.css
sed -i '' '/^\.mock-banner a/d' frontend/src/styles.css
```

In `docs/design/mockups/vault.css`, both `.mock-banner` rules sit on one line each, and no later line
starts with `}`. Because the range never finds its closing line, the first command deletes from
line 122 to the end of the file. That also deletes the closing
`@media (max-width: 1100px) { ... }` rule, which collapses the grid and hides the sidebar on
narrow screens. I checked this on a scratch copy: after the plan's command, the file ends at the
`.legend i` rule.

What I ran instead:

```bash
cp docs/design/mockups/vault.css frontend/src/styles.css
sed -i '' '/^\.mock-banner/d' frontend/src/styles.css
```

The result has no mock-banner rules and keeps the responsive rule. After that I appended the
plan's "console additions" block exactly as written.

### D2: Sidebar navigation shows the mockups' line icons

In the approved mockups (`vault.js` `renderSidebar`), every nav item has a 16px line icon, and
`.nav-item svg` in the stylesheet sizes it. The plan's `Layout.tsx` leaves the icons out. To make
the console look like the mockups, `Layout.tsx` adds a small `Icon` component. It draws the same
SVG paths as JSX (`overview`, `play`, `policy`, `db`, `scan`, `report`, `admin`), uses no
`innerHTML`, and sets `aria-hidden`. Each `NAV` item now has an `icon` field. Routes, labels, the
topbar, settings handling and behaviour are unchanged.

### Notes on Task 25 (not deviations)

- Vite prints this during the build:
  `<script src="./theme-init.js"> in "/index.html" can't be bundled without type="module" attribute`.
  That is intended. `public/theme-init.js` has to stay a classic, same-origin script so it sets
  `data-theme` before first paint while the CSP stays at `script-src 'self'`. Vite copies it
  unchanged to `dist/theme-init.js`, and the `_spa` catch-all in `dps/app.py` serves it.
- The versions npm resolved from the plan's ranges are vite 5.4.21, @vitejs/plugin-react 4.7.0,
  react and react-dom 18.3.1, react-router-dom 6.30.6, typescript 5.9.3, @types/react 18.3.31 and
  @types/react-dom 18.3.7. All are inside the pinned majors (vite ^5, plugin-react ^4).

## Task 26

`frontend/src/pages/Playground.tsx` was taken word for word from the plan's code block (plan
lines 7353 to 7503) and compiles under strict `tsc` with no changes. A browser render test then
found two layout bugs in the Task 25 stylesheet. Both are fixed in `frontend/src/styles.css`
(console additions only; the Vault token block is untouched).

### D3: The report grid in the Output card can shrink, and chips inside it wrap

The plan has `.kv { grid-template-columns: 140px 1fr; ... }`. A `1fr` track cannot get narrower
than its widest content, and `.chip` has `white-space: nowrap`. In the narrow Output column of the
1440px layout, the verification chip ("leakage sweep passed · 0 originals in output") ran 42px
past the card's right edge. Measured: chip right edge at 1458px, card right edge at 1416px,
`.kv` scrollWidth 435 against clientWidth 374. The fix:

```css
.kv { display: grid; grid-template-columns: 140px minmax(0, 1fr); gap: 6px 10px; font-size: 12.5px; }
.kv .chip { white-space: normal; }
```

After the fix, overflow is 0px in both obfuscate and synthesize output, and scrollWidth equals
clientWidth.

### D4: The topbar wraps on phone-width screens

The topbar has two fixed 220px inputs (tenant and API key) and the theme button, and it never
wraps. At 375px the document was 563px wide, so the page scrolled sideways. Only the topbar
overflowed: the field labels and the theme button. The Vault media query hides the sidebar below
1100px but has no rule for the topbar. I added this at the end of the stylesheet:

```css
@media (max-width: 640px) {
  .topbar { height: auto; flex-wrap: wrap; padding: 10px 16px; gap: 10px; }
  .topbar .field { flex: 1 1 140px; }
  .topbar .key-wrap, .input-inline { width: 100%; }
  .page { padding: 16px 16px 32px; }
  .page-head { flex-wrap: wrap; align-items: flex-start; }
}
```

After the fix, document width equals viewport width at 375, 768, 1280 and 1440px. The topbar is
still 60px tall at 768px and wider, so the desktop look is unchanged.

### Plan step 3 (live API check) is deferred

The backend was being built at the same time, so the plan's step 3 (check against the live API on
:8400) could not run yet. That check belongs to the later verification stage. In its place I ran
these:

- `vite preview` and curl: `/` returns 200 with `<div id="root">`, the hashed JS and CSS assets
  return 200, `/theme-init.js` returns 200, and the SPA fallback for `/playground` returns 200.
- A render test in a headless Chromium (gstack browse). It used a throwaway mock of the Layer 1
  response shapes on port 18400, proxied by a preview-only Vite config kept in the session
  scratchpad, not in the repo. Results:
  - Overview shows readiness and the metric tiles. `metricTotal` ignores `*_created` samples.
  - Theme toggle works and persists across reloads.
  - Without a key, the presets call returns 401 and the page falls back to the current policy.
    Detect then shows `401: missing or invalid API key`.
  - With a key and tenant, the `X-API-KEY` and `X-DPS-Tenant` headers are sent, and presets load.
  - Detect, Obfuscate, then Reveal with key all work in JSON and in Text mode.
  - Synthesize shows the "no path back" badge, the seed callout, no key card and no Reveal button.
  - Invalid JSON shows the error banner, and the buttons become usable again.
  - The console logs no errors apart from the expected 401 network entries.
