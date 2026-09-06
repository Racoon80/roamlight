# Roamlight — the design direction

The starting point was a travel site: a story with 356 photographs. This is
something else — a library that grows to tens of thousands and that is worked
in **by hand**. That difference decides almost every choice below.

---

## 1. Navigation — four levels, three clicks

`year → country → album → photographs`, with pagination on the last level. That
mirrors the folders on disk **1:1** — so there is no second "categories" logic
that can drift away from reality.

What stays visible **on every page**:

- **A search field in the header** — not hidden behind a magnifying glass.
  Without any AI in the picture, search is not an extra; it is the only way out
  of a dead end.
- **Breadcrumb**: `Home / 2026 / Portugal / Porto 2026`
- **A filter drawer** (not a page of its own): year, country, place, camera,
  lens, kind, rating, "has GPS", "in an album", "never looked at", tag, person.
  Active filters stand underneath as chips, always removable in one click.
- **A year timeline**: a thin strip `2000 … 2025 2026`, a click jumps there.

⚠ **Pagination, not infinite scroll.** Share links and tagging sessions need
stable URLs — "carry on at page 3" has to be a link, not a scroll position that
is gone after a reload.

⚠ **A cold start has to look honest.** With 0 photographs: no placeholder grid,
just one calm sentence — and a link to the upload page.

```
┌──────────────────────────────────────────────────────────────────┐
│ Family.              [ Search: photographs · people · places  Q ]│
│                                                     [Filter ▾]   │
├──────────────────────────────────────────────────────────────────┤
│  2000  2005  2010  2015  2020  2024  2025  2026                  │
├──────────────────────────────────────────────────────────────────┤
│  2026 ────────────────────────────────────────  38 photographs › │
│      Portugal — Porto 2026                                       │
│  2024 ───────────────────────────────────────  312 photographs › │
│      Belgium — Ostend 2024 · Luxembourg — 3 albums               │
├──────────────────────────────────────────────────────────────────┤
│ Newly added  [ ][ ][ ][ ][ ][ ][ ][ ]                            │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. The grid — **not** "always two side by side"

The travel site's rule is **not** carried over. Two reasons:

1. **No text under the photograph.** There, a caption and a landmark note
   justified the space. Here there is a date and two tags at most.
2. **The volume.** One album can hold 900 photographs (a camera and a phone
   over one week). At two per row that is hundreds of rows.

So: an **even grid with `object-fit: cover`**, 3 columns on a phone, 6–8 on a
desktop — a contact sheet, not a gallery wall. That is also what makes the
tagging mode (§4) possible at all.

What **stays**: respecting the orientation where it counts — in the
**lightbox** (`object-fit: contain`) and on the **album cover** (one large,
uncropped photograph at the top).

---

## 3. Upload — the four fields

In sequence, because the fields depend on each other (the name suggestions come
out of the year and the country).

```
┌──────────────────────────────────────────────────────────────────┐
│ 1  Year      [2026 ▾]  or  [+ new year]                          │
│    ✎ suggested from the EXIF date                                │
│ 2  Country   [Portugal ▾]  or  [+ new country]                   │
│    ✎ suggested from GPS (an offline border table)                │
│ 3  Name      [Porto 2026 ▾]  or  [+ new name]                    │
│    ✎ suggested: "<place> <year>"                                 │
│ 4  Place     [ Porto ]        (metadata, never part of the path) │
├──────────────────────────────────────────────────────────────────┤
│ Original  →  originals / 2026 / Portugal / Porto 2026            │
│ Website   →  library   / 2026 / Portugal / Porto 2026            │
│ 14 photographs · 2026-07-14 to 2026-07-22 · 612 MB               │
│              [ Change ]                 [ Take it ]              │
├──────────────────────────────────────────────────────────────────┤
│ ⚠ 1 of 14 is already known (sha256, in "Ostend 2024")            │
│     [ Skip (default) ]              [ Take it anyway ]           │
└──────────────────────────────────────────────────────────────────┘
```

**The core of the design:** a small **✎** stands next to every value the site
**guessed**, and disappears the moment somebody touches it. So at a glance you
see what is a suggestion and what is a decision — and you correct it in the same
field, without a second dialog.

**"Take it" is the point of no return.** Before that, everything is only text
and dropdowns.

**Progress** is a queue with the real stations — not a spinner:

```
IMG_1497.CR3   [●━━━━━━━━━○─────]  stored, being verified…
IMG_1498.CR3   [●━━━━━━━━━●━━━━●]  on the site  ✓
IMG_1500.CR3   [●───────────────]  uploading…  1.2 / 12 MB

14 of 14 · 3 on the site · 1 converting · incoming: 3 left
```

An error leaves its row **standing in red** with plain words — it does not
disappear. The original is never gone, so no panic UI is needed there.

---

## 4. Tags and people — 200 photographs in one sitting

This is where the site stands or falls, because without AI there is nothing
else.

**A · Batch mode** — a dense grid with checkboxes, click / shift-click (a range)
/ ctrl-click, and a **sticky bar at the bottom** that is never hidden.

| Key | What |
|---|---|
| `↑ ↓ ← →` | the focus moves through the grid |
| `Space` | select / deselect |
| `Shift+click` / `Shift+↓` | a whole range |
| `1`–`9` | puts the Nth **recent** person on **every** selected photograph |
| `Shift+1`–`9` | takes them off again |
| `Enter` | a small side panel for that one photograph (**not** a full lightbox) |
| `R` | repeats the last assignment on the selection |
| `Cmd+A` | everything on this page |

The digits `1`–`9` map onto a **"recently used" list** of people. In one sitting
the same family comes round again and again — that turns 200 clicks into 20 key
presses.

**B · Single mode** — one photograph large, person pills above it, `→` moves on.
For the case where every photograph is different.

The progress is always there (*"142 / 200 tagged"*) — not only at the end. A
two-hour session with no feedback is exactly the point at which people give up.

---

## 5. Design tokens

Two things from the travel site would be wrong here: its Didone/amber
"darkroom" character is too theatrical for a tool somebody tags in for hours,
and this one needs a **real light and dark mode** (that one is dark only) — the
family looks during the day, the tagging happens in the evening.

The direction: **a calm archive look.** Warm paper for light, muted charcoal for
dark, one muted accent (petrol green), small radii, functional type. The
photographs are the colourful thing — the frame around them has to be quiet.

```css
:root {
  /* -------- Light (Standard) -------- */
  --ground:        #f6f2ea;
  --ground-lift:   #ffffff;
  --ground-sunken: #ece5d8;
  --ink:           #221e19;
  --ink-soft:      #5c5348;
  --ink-mute:      #8d8375;
  --rule:          rgba(34, 30, 25, 0.12);
  --rule-strong:   rgba(34, 30, 25, 0.28);
  --accent:        #3f6e64;   /* muted petrol — an accent, never a photo colour */
  --accent-ink:    #ffffff;
  --danger:        #a4402c;

  --display: "Fraunces", "Iowan Old Style", Georgia, serif;
  --body:    "Source Serif 4", Georgia, serif;
  --ui:      "Libre Franklin", "Segoe UI", sans-serif;
  --mono:    "JetBrains Mono", "SFMono-Regular", Menlo, monospace;

  --space-1: 0.25rem; --space-2: 0.5rem;  --space-3: 0.75rem;
  --space-4: 1rem;    --space-5: 1.5rem;  --space-6: 2rem;
  --space-7: 3rem;    --space-8: 4.5rem;

  --radius-s: 3px;    /* Chips, Checkboxen, Miniaturen */
  --radius-m: 6px;    /* Kaarten, Panelen, Inputs */
  --radius-l: 14px;   /* Modaler, Toast */

  --gutter:   clamp(1rem, 4vw, 3.5rem);
  --measure:  34rem;
  --ease:     cubic-bezier(0.16, 1, 0.3, 1);
  --grid-min: 9rem;   /* Mindestbreet vun enger Miniatur am dichte Raster */
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --ground: #201d19;  --ground-lift: #2a2622;  --ground-sunken: #17140f;
    --ink: #ede7dc;     --ink-soft: #b8ada0;     --ink-mute: #837a6d;
    --rule: rgba(237, 231, 220, 0.12);
    --rule-strong: rgba(237, 231, 220, 0.26);
    --accent: #6fa89b;  --accent-ink: #17140f;   --danger: #d97a63;
  }
}
:root[data-theme="dark"] {
  --ground: #201d19;  --ground-lift: #2a2622;  --ground-sunken: #17140f;
  --ink: #ede7dc;     --ink-soft: #b8ada0;     --ink-mute: #837a6d;
  --rule: rgba(237, 231, 220, 0.12);
  --rule-strong: rgba(237, 231, 220, 0.26);
  --accent: #6fa89b;  --accent-ink: #17140f;   --danger: #d97a63;
}
```
`--display` only for headings and album titles · `--ui` for forms, buttons,
chips and the breadcrumb (that is 90 % of the time) · `--body` only for longer
text (notes).

---

## 6. Three things that would be **wrong** here

1. **AI suggestions hidden as "help"** — no auto-tags, no "similar
   photographs", and **no** placeholder button saying "later". That would
   contradict the whole "no AI" principle and raise false expectations.
2. **A public gallery look** — no cookie banners, no social buttons, no SEO
   tags, no "explore" feed. The site sits behind authentication; every public
   element is a contradiction and one more surface to attack.
3. **Cinematic motion** — no parallax, no scroll reveals, no hero zoom. At tens
   of thousands of photographs, and in a session where somebody tags 200 of
   them, every animation costs twice: performance and patience. Motion at most
   as a short focus or hover response (< 150 ms).

---

## 7. What was actually built

The part above was the direction. On the first look the implementation was
flat — and that was fair: the direction was on paper, and on the site stood a
bare form. That was made good.

### The look: "contact sheet"

The travel site's look is a **darkroom print** — Didone headings, amber as a
safelight, grain over everything. This site is the **sleeve around the
negative**: the same warm coal and the same grain, but

| | Travel | Roamlight |
|---|---|---|
| Headings | Bodoni Moda (Didone) | **Bodoni Moda** — the family resemblance |
| Body text | Spectral | **Spectral** |
| Metadata | IBM Plex Mono | **IBM Plex Mono** |
| Accent | safelight amber `#e2673b` | **a muted blue** `#6aa9e0` |
| Grid | two side by side, plates | **contact sheet**, dense, plate number on hover |

⚠ The type in §5 above was the proposal (Fraunces, Libre Franklin). What is
actually shipped is the table here: the two sites share their typography, and
the difference is carried by the grid and the accent instead. The three
families and their licences are listed in
[`static/fonts/LICENSE.md`](static/fonts/LICENSE.md).

⚠ **The fonts are self-hosted** (`static/fonts/*.woff2`, 8 files, 300 KB). The
travel site loads them from Google — that does not work here: this site's own
CSP says `font-src 'self'`, and the share pages must pull **nothing at all**
from a third party. Three tests hold that in place.

### The bug that made the white boxes

```css
input[type=text] { background: var(--ground-lift); … }   /* does NOT match */
```

The fields have **no** `type` attribute (`<input id="year">`), and an attribute
selector only matches when the attribute is there. So the fields got the
browser's default look — a white box with pale text, **unreadable**. Now:
`input:not([type=checkbox]):not([type=radio])`, plus `color-scheme` on `:root`,
so the browser colours its own controls correctly too.

### "Pick one or make a new one" has to be visible

A `datalist` is invisible: the field looks like an empty text box. So now there
is a real **picker** per field — a click on `▾` or focus shows what already
exists (with the number of folders in it), typing filters, and underneath stands
*"Create «Porto 2026»"* when you are building something new. Keyboard: `↑ ↓`,
`Enter`, `Esc`.
