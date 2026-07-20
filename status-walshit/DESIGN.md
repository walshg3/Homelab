---
version: alpha
name: Walshit Family — status.walshit.com (House Report)
description: >
  Shared visual-identity specification that makes status.walshit.com an
  unmistakable member of the walshit.com family. The global chrome (lockup,
  header, shell, focus, footer, grain, motion) is canonical and must match
  walshit.com exactly; the House Report grammar (overall field, ruled ledger,
  ticket event, filed history, coverage note) is the status-page content
  layer inside that shared frame. Derived from
  walshit-landing/hugo/static/styles.css and the walshit-landing Hugo
  partials, not from the first status prototype.
colors:
  paper: "#f2ead9"
  paperSecondary: "#e5d8bd"
  ink: "#171713"
  red: "#c93424"
  yellow: "#f1bd38"
  paperOnInk: "#f2ead9"
  statusGreen: "#39754d"
  white: "#ffffff"
  primary: "#171713"
  secondary: "#c93424"
  tertiary: "#f1bd38"
typography:
  base:
    fontFamily: '"Arial Narrow", "Aptos Narrow", "Helvetica Neue", Arial, sans-serif'
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.45
  serifDisplay:
    fontFamily: 'Georgia, "Times New Roman", serif'
    fontSize: 60px
    fontWeight: 400
    letterSpacing: "-0.06em"
    lineHeight: 0.9
  wordmark:
    fontFamily: '"Arial Narrow", "Aptos Narrow", "Helvetica Neue", Arial, sans-serif'
    fontSize: 30px
    fontWeight: 900
    letterSpacing: "-0.07em"
    lineHeight: 1
  wordmarkSlash:
    fontFamily: 'Georgia, "Times New Roman", serif'
    fontSize: 30px
    fontWeight: 400
    lineHeight: 1
  descriptor:
    fontFamily: '"Arial Narrow", "Aptos Narrow", "Helvetica Neue", Arial, sans-serif'
    fontSize: 9px
    fontWeight: 700
    letterSpacing: 0.13em
    lineHeight: 1.25
  navLabel:
    fontFamily: '"Arial Narrow", "Aptos Narrow", "Helvetica Neue", Arial, sans-serif'
    fontSize: 11.5px
    fontWeight: 800
    letterSpacing: 0.1em
    lineHeight: 1.2
  eyebrow:
    fontFamily: '"Arial Narrow", "Aptos Narrow", "Helvetica Neue", Arial, sans-serif'
    fontSize: 11px
    fontWeight: 900
    letterSpacing: 0.15em
    lineHeight: 1.2
  buttonLabel:
    fontFamily: '"Arial Narrow", "Aptos Narrow", "Helvetica Neue", Arial, sans-serif'
    fontSize: 12px
    fontWeight: 900
    letterSpacing: 0.08em
    lineHeight: 1.2
  stamp:
    fontFamily: '"Arial Narrow", "Aptos Narrow", "Helvetica Neue", Arial, sans-serif'
    fontSize: 10px
    fontWeight: 900
    letterSpacing: 0.1em
    lineHeight: 1.2
  bodySmall:
    fontFamily: '"Arial Narrow", "Aptos Narrow", "Helvetica Neue", Arial, sans-serif'
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.45
  footerMark:
    fontFamily: 'Georgia, "Times New Roman", serif'
    fontSize: 32px
    fontWeight: 400
    letterSpacing: "-0.04em"
    lineHeight: 1.2
rounded:
  none: 0px
  full: 999px
spacing:
  hairline: 1px
  ruleHeavy: 2px
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
  gutterMobile: 32px
  gutterDesktop: 48px
  headerMobile: 76px
  headerDesktop: 90px
components:
  shell:
    width: 1280px
    padding: 0px
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
  headerDesktop:
    height: 90px
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: 0px
  headerMobile:
    height: 76px
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: 16px
  brandLockup:
    typography: "{typography.wordmark}"
    textColor: "{colors.ink}"
    backgroundColor: "{colors.paper}"
    rounded: "{rounded.none}"
    padding: 0px
  brandSlash:
    typography: "{typography.wordmarkSlash}"
    textColor: "{colors.red}"
    rounded: "{rounded.none}"
    padding: 0px
  brandDescriptor:
    typography: "{typography.descriptor}"
    textColor: "{colors.ink}"
    backgroundColor: "{colors.paper}"
    rounded: "{rounded.none}"
    width: 144px
    padding: 0px
  navLinkDesktop:
    typography: "{typography.navLabel}"
    textColor: "{colors.ink}"
    backgroundColor: "{colors.paper}"
    rounded: "{rounded.none}"
    height: 44px
    padding: 9px
  navRowMobile:
    typography: "{typography.navLabel}"
    textColor: "{colors.ink}"
    backgroundColor: "{colors.paper}"
    rounded: "{rounded.none}"
    height: 48px
    padding: 12px
  navToggle:
    size: 44px
    textColor: "{colors.ink}"
    backgroundColor: "{colors.paper}"
    rounded: "{rounded.none}"
    padding: 0px
  buttonPrimary:
    typography: "{typography.buttonLabel}"
    textColor: "{colors.paperOnInk}"
    backgroundColor: "{colors.ink}"
    rounded: "{rounded.none}"
    height: 52px
    padding: 14px
  buttonSecondary:
    typography: "{typography.buttonLabel}"
    textColor: "{colors.ink}"
    backgroundColor: "{colors.paper}"
    rounded: "{rounded.none}"
    height: 52px
    padding: 14px
  skipLink:
    typography: "{typography.navLabel}"
    textColor: "{colors.paperOnInk}"
    backgroundColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: 13px
  statusOverallField:
    backgroundColor: "{colors.yellow}"
    textColor: "{colors.ink}"
    typography: "{typography.serifDisplay}"
    rounded: "{rounded.none}"
    padding: 32px
  ledgerRow:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.bodySmall}"
    rounded: "{rounded.none}"
    height: 112px
    padding: 16px
  stateStampOperational:
    typography: "{typography.stamp}"
    textColor: "{colors.statusGreen}"
    backgroundColor: "{colors.paper}"
    rounded: "{rounded.none}"
    height: 31px
    padding: 6px
  stateStampDegraded:
    typography: "{typography.stamp}"
    textColor: "{colors.ink}"
    backgroundColor: "{colors.yellow}"
    rounded: "{rounded.none}"
    height: 31px
    padding: 6px
  stateStampOutage:
    typography: "{typography.stamp}"
    textColor: "{colors.white}"
    backgroundColor: "{colors.red}"
    rounded: "{rounded.none}"
    height: 31px
    padding: 6px
  stateStampMaintenance:
    typography: "{typography.stamp}"
    textColor: "{colors.ink}"
    backgroundColor: "{colors.paperSecondary}"
    rounded: "{rounded.none}"
    height: 31px
    padding: 6px
  stateStampNoData:
    typography: "{typography.stamp}"
    textColor: "{colors.ink}"
    backgroundColor: "{colors.paper}"
    rounded: "{rounded.none}"
    height: 31px
    padding: 6px
  ticketEvent:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.bodySmall}"
    rounded: "{rounded.none}"
    padding: 24px
  ticketEventStub:
    backgroundColor: "{colors.paperSecondary}"
    textColor: "{colors.ink}"
    typography: "{typography.eyebrow}"
    rounded: "{rounded.none}"
    padding: 22px
  historyRow:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.bodySmall}"
    rounded: "{rounded.none}"
    height: 112px
    padding: 16px
  coverageNotePublic:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.bodySmall}"
    rounded: "{rounded.none}"
    padding: 32px
  coverageNotePrivate:
    backgroundColor: "{colors.paperSecondary}"
    textColor: "{colors.ink}"
    typography: "{typography.bodySmall}"
    rounded: "{rounded.none}"
    padding: 32px
  footer:
    height: 160px
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.footerMark}"
    rounded: "{rounded.none}"
    padding: 0px
  statusDot:
    size: 13px
    rounded: "{rounded.full}"
    backgroundColor: "{colors.statusGreen}"
    padding: 0px
---

## Overview

This document is the binding design specification for `status.walshit.com`.
It resolves a reported design-system defect: the first status implementation
(`status-walshit/web/index.html`, `status-walshit/web/status.css`) invented a
second Walshit identity — a rotated yellow square `W` badge, a
`Walshit.com / Status` title-case wordmark, a slightly different paper color,
a narrower shell, a heavier header rule, and a red focus ring — none of which
exist on the canonical site. The canonical identity is defined by
`walshit-landing/hugo/layouts/partials/header.html`,
`walshit-landing/hugo/static/styles.css`,
`walshit-landing/hugo/layouts/index.html`,
`walshit-landing/hugo/layouts/partials/footer.html`, and
`walshit-landing/hugo/layouts/partials/head.html`, and is visible in the live
captures at `design-evidence/walshit-live/desktop.png` and
`design-evidence/walshit-live/mobile.png`. The first status shell is captured
at `status-walshit/render/desktop.png` and `status-walshit/render/mobile.png`.

The normative relationship has exactly two layers:

1. **Shared global identity and chrome — canonical, non-forkable.** The
   lockup, header, shell width and gutters, type stacks, base size and line
   height, focus ring, paper grain, square corners, button and rule
   conventions, motion behavior, and footer identity must match `walshit.com`
   token-for-token. The status site does not get its own version of any of
   these.
2. **House Report — the status content grammar.** The overall status field,
   five-row ruled ledger, ticket-style active event, filed incident history,
   coverage note, and status-state semantics (from `concept/index.html` and
   `concept/NOTES.md`) are the content layer rendered inside the shared
   frame. Content-layer deviations are allowed only where explicitly listed
   below.

Nothing in between. "Similar vibes" is not a compliance state.

### Current mismatch inventory (source-grounded)

| Aspect | Canonical `walshit.com` (source) | First status shell (source) | Ruling |
|---|---|---|---|
| Lockup markup / wordmark | `WAL<span class="slash">/</span>SHIT <small>…</small>` text lockup, 900-weight condensed sans, `-0.07em` tracking (`header.html` L2, `styles.css` `.brand`) | `Walshit.com / Status` title-case, `-0.055em`, no descriptor block (`web/index.html` L14–17) | **Must change** |
| Brand mark | None — the wordmark is the mark; no standalone badge | Rotated 42px yellow `W` square with red offset shadow (`status.css` `.brand-mark`) | **Must change** (remove) |
| Paper color | `#f2ead9`, secondary `#e5d8bd` (`styles.css` `:root`) | `#f3ead7`, deep `#e2d3b5`, pale `#faf6ed` (`status.css` `:root`) | **Must change** |
| Shell max width / gutters | `1280px`; `calc(100% - 48px)` desktop, `calc(100% - 32px)` at `820px` (`styles.css` `.shell`) | `1180px`; 40px desktop, 28px mobile gutters | **Must change** |
| Header height / rule | `min-height: 90px` desktop, `76px` at `820px`; `border-bottom: 1px solid var(--ink)` | `104px` / `86px`; 2px bottom rule | **Must change** |
| Focus color | `outline: 3px solid var(--yellow); outline-offset: 4px` (`styles.css` L36) | `3px solid var(--red)`, offset 3px | **Must change** |
| Grain treatment | Fixed SVG fractal-noise overlay at `opacity: .055` (`styles.css` `body::before`) | Halftone-dot multiply overlay at 0.22 plus scanline gradient | **Must change** |
| Mobile navigation | 44px hamburger toggle, full-width 48px stacked rows, `aria-expanded`, dashed row separators (`styles.css` `@media 1024px`) | `.utility-nav { display: none }` — only utility link vanishes with no route | **Must change** |
| Footer identity | Serif italic `Walshit. Yes, really.` + uppercase note, no top rule in CSS (`footer.html`, `styles.css` `footer`) | `Walshit.com / Status` italic mark, 2px top rule | **Must change** (adopt canonical voice; see Components) |
| Rule / shadow strength | 1px rules for chrome; 2px + hard offset shadows reserved for content objects (ticket, dialog, maintenance banner) | 2px rules on chrome (header, footer) and content alike | **Allowed status variant** for content objects only; chrome rules are **Canonical shared** at 1px |
| Ledger density (112px rows, ruled full-width) | `.service` rows: `min-height: 112px`, 1px rules | `.component-row`: same density | **Canonical shared** (already aligned) |
| House Report grammar (mast, ledger, ticket, history, coverage) | n/a — status content | Present | **Allowed status variant** (the point of the page) |

## Colors

All values come from `walshit-landing/hugo/static/styles.css` `:root` unless
noted. These replace the drifted values in `status-walshit/web/status.css`.

- `{colors.paper}` `"#f2ead9"` — page background. Also the `theme-color` meta
  value in `head.html`. The status prototype's `"#f3ead7"` is retired.
- `{colors.paperSecondary}` `"#e5d8bd"` — secondary paper stock: ticket
  stubs, the private-coverage panel, maintenance stamp fill. Replaces the
  prototype's `"#e2d3b5"` and `"#faf6ed"`; there is no third paper tone.
- `{colors.ink}` `"#171713"` — text, rules, primary button fill.
- `{colors.red}` `"#c93424"` — the slash, interruption/outage meaning,
  button offset shadows, underline accents. Red always means "something is
  interrupted or demands attention." It is never decorative filler and never
  a focus ring.
- `{colors.yellow}` `"#f1bd38"` — notice stock and the focus ring. Yellow
  means "notice / attention, not failure": the overall status field
  background, degraded stamps, hover fill on ledger rows.
- `{colors.statusGreen}` `"#39754d"` — operational meaning only, carried
  over from `concept/index.html` (`--green`). This is the single
  status-content addition to the palette. It may appear only in state stamps,
  the status dot, and uptime marks — never in chrome.
- `{colors.white}` `"#ffffff"` — an accessibility text utility only, not a
  new paper stock. The canonical source already uses CSS `white` (the skip
  link in `styles.css`), so this is source-grounded. Its only sanctioned
  uses are text sitting on ink or red fills where AA contrast requires it —
  most importantly `stateStampOutage` text on `{colors.red}` (contrast
  21:1 → 5.36:1 on red, comfortably AA). White is never a background, never
  a chrome surface, and never replaces `{colors.paper}`.
- Semantic aliases: `{colors.primary}` `"#171713"` (= ink),
  `{colors.secondary}` `"#c93424"` (= red), and `{colors.tertiary}`
  `"#f1bd38"` (= yellow) are pure aliases for tooling that expects a
  semantic palette. They introduce no new values; the canonical named tokens
  above remain the authoritative vocabulary in prose and implementation.
- Hairline rule color: the canonical site uses solid `{colors.ink}` for
  structural 1px rules and `rgba(23, 23, 19, 0.28)` (`--rule` in
  `styles.css`) for dashed mobile-nav separators. Reproduce both exactly.

Contrast: ink on paper, ink on yellow, and paper on ink all pass WCAG AA at
the sizes used. Red on paper is 4.39:1, which fails AA for ordinary small
text; that pairing is permitted **only** for the brand slash — a logo
element covered by the WCAG 1.4.3 logotype exemption (see Typography) — and
is prohibited for ordinary body, label, or status text at small sizes.
Outage stamp text uses `{colors.white}` on `{colors.red}` (5.36:1, AA at
any size); do not use paper on red for text, and do not use red fills
behind long-form body text.

## Typography

Both stacks come verbatim from `styles.css` `:root`:

- Sans: `"Arial Narrow", "Aptos Narrow", "Helvetica Neue", Arial, sans-serif`
- Serif: `Georgia, "Times New Roman", serif`

No webfonts, no new stacks. Base is `16px` with `line-height: 1.45`
(`styles.css` `body`), replacing the prototype's `1.4`.

### The lockup (resolves the reported mismatch)

The canonical top-left lockup is **text, not a badge**. Exact markup pattern,
from `header.html`:

```html
<a class="brand" href="https://walshit.com/" aria-label="Walshit home">
  WAL<span class="slash">/</span>SHIT
  <small>Service status<br>House report</small>
</a>
```

Binding rules:

- Capitalization is exactly `WAL/SHIT` — never `Walshit.com`, never
  title case, never a standalone `W`.
- Wordmark: `{typography.wordmark}` — the sans stack at
  `clamp(1.55rem, 3vw, 2.15rem)`, weight 900, letter-spacing `"-0.07em"`,
  line-height 1 (`styles.css` `.brand`).
- Slash: `{typography.wordmarkSlash}` — serif stack, italic, weight 400,
  `{colors.red}` (`styles.css` `.brand .slash`). The red serif italic slash
  is the family signature; it is required. It is brand punctuation inside a
  logotype, not body text: red on paper measures 4.39:1, below the 4.5:1 AA
  threshold, and is acceptable here solely under the WCAG 1.4.3/1.4.6
  exemption for text that is part of a logo or brand name. The `brandSlash`
  token therefore carries no `backgroundColor` — it is not a text-on-fill
  content component — and the red-on-paper pairing is prohibited for any
  ordinary small text outside the lockup.
- Descriptor (`<small>`): `{typography.descriptor}` — `0.57rem`, weight 700,
  letter-spacing 0.13em, line-height 1.25, uppercase, `max-width: 9rem`,
  separated from the wordmark by `padding-left: .7rem` and a
  `border-left: 1px solid {colors.ink}` one-pixel rule with `.7rem` flex gap
  (`styles.css` `.brand small`).
- **Status descriptor text (exact recommendation):** `Service status` on
  line one, `<br>`, `House report` on line two — rendered uppercase by the
  existing `text-transform`. This mirrors the two-line
  `Movies, shows<br>and audiobooks` rhythm on the landing site while naming
  the page's job and its house voice. Use this text; do not present
  alternatives.
- The descriptor is hidden at the same breakpoint as `walshit.com`:
  `display: none` inside `@media (max-width: 820px)` (`styles.css` L412).
- Brand destination on the status site: the lockup links to
  `https://walshit.com/` with `aria-label="Walshit home"`. The lockup is the
  guaranteed route home on every viewport.
- The rotated yellow/red `W` square (`status.css` `.brand-mark`) is **not an
  approved primary family lockup** and must be removed from the status
  header in the next implementation pass. (The oversized watermark `W`
  inside a yellow field — `styles.css` `.bill::before`,
  `status.css` `.overall::after` — is a background texture, not a lockup,
  and remains permitted.)

### Display and label type

- Display headings (`h1`, section `h2`, ticket titles): serif stack,
  weight 400, negative tracking between `"-0.075em"` and `"-0.035em"` by
  size, line-height 0.83–1.02, per `styles.css` `h1`, `.ticket h2`,
  `.program h2`. Red italic serif is used for emphasis words and index
  numbers (`h1 em`, `.section-number`, `.service .num`).
- UI labels (nav, eyebrows, stamps, buttons): condensed sans, 800–900
  weight, uppercase, positive tracking 0.08em–0.15em, 0.64rem–0.78rem.
- Body copy: sans, 0.86rem–1.05rem depending on slot.

## Layout

Shell, from `styles.css` `.shell`:

- `width: min(calc(100% - 48px), 1280px); margin-inline: auto;` — i.e. max
  width `1280px`, `48px` total-side gutters on desktop.
- At `@media (max-width: 820px)`: `width: min(calc(100% - 32px), 1280px)` —
  `32px` gutters. The prototype's 1180px/40px/28px shell is retired.

Breakpoints — use exactly the three the source uses, at the same duties:

- `1024px` — navigation collapses to the toggle + stacked column
  (`styles.css` L386–407).
- `820px` — shell gutters tighten to 32px, header drops to 76px, brand
  descriptor hides, multi-column grids stack (`styles.css` L409–440).
- `460px` — actions go full-width, shadows lighten, footer stacks
  (`styles.css` L442–456).

Header, from `styles.css` `header` and the `1024px`/`820px` blocks:

- Desktop: flex row, `min-height: 90px`, `border-bottom: 1px solid
  {colors.ink}` (one pixel — not the prototype's 2px), brand left, nav right.
- ≤1024px: header wraps, `padding-block: 16px`, 44×44px `nav-toggle`
  hamburger appears at the right.
- ≤820px: `min-height: 76px`, descriptor hidden.

### Status navigation model (binding)

Desktop (>1024px) — a single `nav` with `aria-label="Status navigation"`,
right-aligned, using `navLinkDesktop` styling (`nav a` rules: 44px min
height, `.55rem .85rem` padding, 0.72rem/800/uppercase/0.1em, 2px underline
on hover). Link order after the lockup:

1. `Walshit.com` → `https://walshit.com/` (explicit route home in addition
   to the lockup)
2. `Incident history` → `#history`
3. `Updates` → `https://walshit.com/updates/` (the family's existing
   update/history destination, per `header.html`)

Mobile (≤1024px) — reuse the canonical disclosure pattern exactly
(`header.html` L3–9, `styles.css` `.nav-toggle` and the 1024px media block):

- Visible, labeled 44×44px toggle button with the three-line icon,
  `aria-controls="status-navigation"`, `aria-expanded` toggling
  `false`/`true`, accessible name `Open menu` / `Close menu`.
- Open state: nav becomes a full-width stacked column under a 1px ink top
  rule; every row full-width, `min-height: 48px`, left-aligned, separated by
  `1px dashed rgba(23, 23, 19, 0.28)`.
- Closed state: `hidden` on the nav — no focusable content while closed.
- Escape closes the disclosure and returns focus to the toggle; the icon
  morph transitions are suppressed under `prefers-reduced-motion: reduce`
  exactly as in `styles.css` L458–462.
- **All three destinations remain reachable on every viewport.** The
  prototype behavior of `display: none` on the only utility link at 820px
  (`status.css` L515–517) with no replacement route is prohibited.

Content rhythm: House Report sections keep the concept's vertical cadence
(mast, then ledger at ~`{spacing.xl}`×2.5 top margin, then active events,
history, coverage), full-bleed within the shell, separated by 1px ink rules
in the canonical manner (`styles.css` `.program`, `.list`).

## Elevation & Depth

The family does not use blur shadows, gradients-as-depth, or rounded cards.
Depth is expressed three ways, all from `styles.css`:

- **Hard offset shadows** on physical objects: ticket `10px 10px 0
  {colors.ink}` (7px at ≤460px), primary button `4px 4px 0 {colors.red}`
  compressing to `2px 2px 0` on hover with a `translate(2px, 2px)`, dialog
  `12px 12px 0 {colors.red}`, maintenance banner `6px 6px 0 {colors.red}`.
  Status usage: the active-event ticket and the overall mast may carry hard
  ink or red offset shadows in these magnitudes; nothing else floats.
- **Rules**: 1px solid ink for all chrome and ledger separations; 2px solid
  ink is reserved for content-object borders and list tops (`.ticket`,
  `.list`, `dialog`); 1px dashed ink for ticket perforations and stub edges.
- **Paper grain**: one fixed, pointer-transparent SVG fractal-noise overlay
  at `opacity: .055` (`styles.css` `body::before`), plus the single
  `radial-gradient` sheen on `body`. The prototype's halftone-dot multiply
  layer and scanline gradient are retired; there is exactly one grain
  treatment in the family.

Focus is part of depth language: `:focus-visible { outline: 3px solid
{colors.yellow}; outline-offset: 4px; }` (`styles.css` L36). The yellow ring
is canonical on every page in the family. The status prototype's red ring is
a defect: red is reserved for failure meaning, so a red focus ring misreads
as an error state.

Motion: transitions only on hover compression, ledger-row hover, and the
nav-toggle icon; all effectively disabled under
`prefers-reduced-motion: reduce` per `styles.css` L458–462. `html {
scroll-behavior: smooth }` likewise reverts to `auto`.

## Shapes

- **Square corners everywhere.** `{rounded.none}` on every box: buttons,
  tickets, stamps, dialogs, ledger rows, nav rows. No generic SaaS pills, no
  radius tokens beyond the two below.
- `{rounded.full}` (`999px` — a pill-overflow radius that renders any box
  whose radius exceeds half its size as visually circular) is permitted only
  for the three canonical circles: the status dot
  (`.ticket-status::before` on the landing site; `statusDot` here), the
  circular `.service .go` arrow chip, and ticket perforation notches. It is
  never used to soften rectangles into pills.
- Status stamps are squared rectangles with 1px `currentColor` borders
  (canonicalized from `status.css` `.state-stamp`) — never rounded pills.
- Tickets are rectangles with 2px ink borders, dashed internal perforations,
  optional slight rotation (`rotate(1.5deg)` on the landing ticket; the
  status active-event ticket stays unrotated for legibility, which is an
  approved content-layer choice).

## Components

Token definitions live in the front matter; this section binds them to
markup and behavior.

- **shell** — every top-level region (`header`, `main` sections, `footer`)
  is a `.shell`. Max width `1280px`, gutters per Layout.
- **headerDesktop / headerMobile** — heights `90px` / `76px`
  (mobile applies at ≤820px; between 1024px and 820px the desktop height
  holds while nav collapses), 1px ink bottom rule, `{colors.paper}`
  background. The header is never yellow, never bordered at 2px.
- **brandLockup + brandSlash + brandDescriptor** — the exact lockup
  specified in Typography. `brandDescriptor` width `144px` (9rem) max, 1px
  ink left rule, hidden ≤820px. Destination `https://walshit.com/`,
  `aria-label="Walshit home"`.
- **navLinkDesktop / navRowMobile / navToggle** — the three-destination
  model from Layout. Desktop targets ≥44px; mobile rows ≥48px; toggle
  exactly 44×44px.
- **skipLink** — first focusable element, `Skip to status`, ink block with
  paper text, off-canvas until focused (`styles.css` `.skip`).
- **buttonPrimary / buttonSecondary** — canonical `.button` and
  `.button.secondary`: 52px min height, 1px ink border, uppercase 900 label,
  primary carries the `4px 4px 0 {colors.red}` shadow and hover compression;
  secondary is transparent, filling ink on hover. The `↗` glyph suffix comes
  from the existing `content: "\2197\FE0E"` rule. Full-width at ≤460px.
- **statusOverallField** — the House Report mast: an unmistakable full-width
  field answering "is it working?" first. `{colors.yellow}` ground, 2px ink
  border, oversized serif verdict (`overall-title` scale), eyebrow
  `House report · <date/issue>`, `role="status"` with `aria-live="polite"`,
  the faint italic serif `W` watermark, and a `statusDot` whose fill follows
  the current state color. The dot is purely decorative — it contains no
  text or glyph, carries `aria-hidden="true"`, and defines no `textColor`;
  the state is always also conveyed by the adjacent verdict text and stamps,
  so color is never the sole signal. Yellow stays the ground in all states;
  the state is carried by the verdict text, dot, and stamps — the field
  never turns green or red wholesale.
- **ledgerRow** (the five-component ledger) — exactly five public
  components: Watch, Home base, Requests, Audiobooks, Walshit.com (names and
  public-safe descriptions per `status-walshit/web/index.html`). Full-width
  ruled rows, `min-height: 112px`, serif component names, 90-day history
  strip, right-aligned state stamp. List top rule 2px ink, row separators
  1px (a permitted content-layer emphasis matching the landing `.list`
  convention). Row hover may fill `{colors.yellow}` like `.service:hover`.
- **stateStamp\*** (five separate variants, no nesting) —
  `stateStampOperational` (green text/keyline on paper),
  `stateStampDegraded` (ink on yellow), `stateStampOutage` (white on red —
  `{colors.white}` text for AA contrast; never paper on red),
  `stateStampMaintenance` (ink on secondary paper), `stateStampNoData` (ink
  keyline on paper, muted). Semantics: operational = measured and healthy;
  degraded = working but impaired (yellow = notice); outage = interrupted
  (red = interruption); maintenance = planned, announced work; no-data =
  honestly unmeasured — never faked as operational.
- **ticketEvent + ticketEventStub** — the active-event notice as a physical
  ticket: 2px ink border, dashed stub perforation, `{colors.paperSecondary}`
  stub with italic serif event number, red offset shadow when an incident is
  active, ink shadow when the board is quiet. One ticket per active event;
  an empty board states plainly that nothing is filed.
- **historyRow** — filed incidents in reverse chronology under a 2px top
  rule: italic serif date column, serif title, sans summary, resolution
  state. Before 90 measured days exist, history and uptime strips must say
  so plainly (e.g. "Measured since <date> — N days on record"); partial
  records are never dressed as a full 90-day history.
- **coverageNotePublic / coverageNotePrivate** — the two-panel coverage
  note: what the page covers publicly, and that internal systems, personal
  activity, library contents, operator details, and recovery information are
  never published. Private panel on `{colors.paperSecondary}` with a dashed
  divider.
- **footer** — canonical footer identity, adapted in the family voice:
  `footerMark` serif italic `Walshit. Yes, really.` on the left and an
  uppercase note on the right (pattern from `footer.html` /
  `styles.css` `footer`); the status page's right-hand note reads
  `Service status · House report`. Stacks at ≤460px. No 2px top rule —
  chrome rules are 1px or none, matching the landing footer.

### Allowed status-content variants (exhaustive)

- 2px ink borders and 2px list-top rules on **content objects** (mast,
  ticket, ledger top, history top).
- Denser data rows and the 90-day history strips.
- `{colors.statusGreen}` in stamps, dots, and uptime marks.
- Unrotated active-event ticket.
- Status-specific copy voice per `concept/NOTES.md`.

Everything else — header, lockup, shell, focus, grain, footer identity,
type stacks, corner treatment, motion — may not fork.

## Do's and Don'ts

**Do**

- Use the exact `WAL/SHIT` text lockup with the red serif italic slash and
  the `SERVICE STATUS / HOUSE REPORT` descriptor.
- Keep `#f2ead9` / `#e5d8bd` / `#171713` / `#c93424` / `#f1bd38` as the only
  chrome palette, plus `#39754d` strictly for operational meaning and
  `#ffffff` strictly as an accessibility text utility on ink or red fills
  (already present in source via the skip link's CSS `white`) — never as a
  surface or chrome color.
- Keep the yellow 3px focus ring with 4px offset on every focusable element.
- Keep all navigation destinations reachable at every viewport via the
  canonical toggle pattern.
- State partial monitoring history honestly before 90 measured days.
- Keep red = interruption and yellow = notice, everywhere, without
  exception.

**Don't**

- Don't render a standalone `W` square badge as the brand mark — remove it.
- Don't use a red focus ring, rounded pills, webfonts, blur shadows,
  gradients-as-depth, or a second grain treatment.
- Don't hide the only utility link on mobile without an equivalent route.
- Don't fork shell width, gutters, header height, rule weight, or base
  type from `walshit-landing/hugo/static/styles.css`.
- Don't turn the overall field green/red wholesale; the yellow notice stock
  is constant and the verdict carries the state.
- Don't claim uptime or history that was not measured.

### Agent implementation contract (acceptance criteria)

A future implementation pass is compliant when all of the following hold:

1. **Header DOM order (exact):** `a.skip` → `header.shell` containing, in
   order: `a.brand` (text nodes `WAL`, `span.slash` `/`, `SHIT`, then
   `small` with `Service status<br>House report`), `button.nav-toggle`
   (44×44px, `aria-expanded`, `aria-controls`), `nav#status-navigation`
   with links in order `Walshit.com`, `Incident history`, `Updates`.
2. **Lockup:** text reads exactly `WAL/SHIT`; slash is `#c93424` serif
   italic; `a.brand` targets `https://walshit.com/` with
   `aria-label="Walshit home"`; no `W` badge element exists anywhere in the
   header; descriptor hidden at ≤820px only.
3. **Tokens:** computed styles show paper `#f2ead9`, secondary `#e5d8bd`,
   ink `#171713`, red `#c93424`, yellow `#f1bd38`; shell max-width `1280px`
   with 48px/32px gutters; header min-height 90px/76px with a 1px ink rule;
   base 16px/1.45; `:focus-visible` outline `3px solid #f1bd38` offset 4px;
   media queries exist at exactly `1024px`, `820px`, `460px`.
4. **Navigation behavior:** desktop shows all three links at ≥44px targets;
   ≤1024px shows the labeled toggle, 48px full-width rows, correct
   `aria-expanded`, `hidden` nav when closed (no focusable closed content),
   Escape closes and restores focus to the toggle; disclosure motion is
   inert under `prefers-reduced-motion: reduce`; every destination remains
   reachable at 430px width.
5. **Visual regression:** screenshots at `1440×1000`, `1024×900`,
   `820×1000`, and `430×932` show no horizontal overflow, no clipped or
   overlapping text, no hidden destination, no interactive target under
   44×44px, and no focus loss when the disclosure closes; the status header
   at 1440 is visually interchangeable with `walshit.com`'s header except
   for descriptor text and link labels.
6. **Accessibility:** all non-logo text meets WCAG AA contrast on its
   actual background — every ordinary UI, body, and status label passes
   without exception; the only exemption is the `WAL/SHIT` lockup slash
   under the WCAG logotype exemption. Outage stamp text is `#ffffff` on
   `#c93424`. The status dot is decorative (`aria-hidden="true"`) and never
   the sole carrier of state. `role="status"`/`aria-live="polite"` on the
   overall field; reduced-motion media query present and effective.
7. **Assets:** no external fonts, scripts, or images; only local assets
   already approved for the family.
8. **House Report preserved:** after alignment, the page still presents the
   unmistakable overall field, the five-row ruled ledger, the ticket-style
   active event, the filed history, the coverage note, and the five
   state semantics — recognizable against `concept/index.html` and
   `status-walshit/render/*.png` in structure even as the chrome changes.

**Boundary:** this document specifies; it does not certify. The mismatch in
`status-walshit/web/` remains in source until a later implementation task applies
this DESIGN.md and verifies it against the criteria above. Hermes will lint
this file with `npx -y @google/design.md lint`.
