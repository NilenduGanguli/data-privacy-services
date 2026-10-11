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

### Notes (not deviations)

- Vite prints this during the build:
  `<script src="./theme-init.js"> in "/index.html" can't be bundled without type="module" attribute`.
  That is intended. `public/theme-init.js` has to stay a classic, same-origin script so it sets
  `data-theme` before first paint while the CSP stays at `script-src 'self'`. Vite copies it
  unchanged to `dist/theme-init.js`, and the `_spa` catch-all in `dps/app.py` serves it.
- The versions npm resolved from the plan's ranges are vite 5.4.21, @vitejs/plugin-react 4.7.0,
  react and react-dom 18.3.1, react-router-dom 6.30.6, typescript 5.9.3, @types/react 18.3.31 and
  @types/react-dom 18.3.7. All are inside the pinned majors (vite ^5, plugin-react ^4).
