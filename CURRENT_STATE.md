# Screenwall Makr — Current State

**Last updated:** 2026-05-13  
**Repo HEAD (reference):** `28b4619` on `main` — stable baseline for **L/J joinery**, **L flange development**, and **panel ID vs install slots**.

This file is the short handoff for future work. If it conflicts with older prose in `KnownTruths` or `.cursor/rules/screenwall.mdc`, **prefer this file and the code** until those docs are re-read after the next big change.

---

## What is verified in this baseline

- **J4S** — Blank size, HC placement, bend1/bend2 CLs, J+J miters, and Fusion cross-checks in `KnownTruths` §3 still match.
- **L4S / MIX (L sides)** — Flat leg uses **§8** development; bend-1 CL uses the **same HC − BA/2 rule as J**; non–J+J corner joinery uses the **bend-relief square** on the **intersection of bend-1 centerlines**, quadrant toward HC **plus bridge to hard corner** (no BD-wide stair gap).
- **Panel ID** — Stick-font linework on layer `text`; when the chosen ID flange **also has install slots**, anchor is **`max(4″ from flange start, past first slot)`** (see constants in `screenwall_generator.py`), else **mid-flange**.

---

## Bend & blank math (code)

| Item | J | L |
|------|---|---|
| Leg flat `f1` (blank sizing) | `nominal − 3×BD/2` | **`nominal − OSS + BA`** (= `nominal − BD/2 + BA/2`) |
| Lip flat `f2` | `nominal − BD/2` | — |
| Bend 1 CL | **`HC − BA/2`** toward blank (same for L and J) | same |
| Bend 2 CL | `f2` from blank edge (J only) | — |

- `OSS = R + T`, `BA = (π/2)(R + K×T)`, `BD = 2×OSS − BA`, `BA/2 = (π/4)(R + K×T)`.
- **HC–HC** on flat remains **`face_width − 2×BD`** (and same for height) — that matches the documented Fusion convention for **W, H = finished face O.D.**

---

## Corner joinery (perimeter)

- **`notch_size`** in outline = **`2×T`** (e.g. **0.375″** for **0.1875″** stock), centered on **bend-1 CL intersection** at each active corner.
- **J+J** — Miter + existing **five-point** relief path around that square (shop/Fusion path).
- **Non–J+J** (L+L, J+L, L+J) — **Two edges** of the square in the quadrant toward HC, then a **short segment** along the inner face line to HC so the outline meets the hard corner flush.
- **`gap_override`** — Still only affects **J+J** miter apex (void endpoints pulled by `gap/2` along void edges when `gap > 0`).

`_draw_corner_reliefs` is currently a **no-op**; reliefs are carried only on the **main cut outline**, not as separate overlapping rectangles.

---

## Install slots & panel ID

- **Layer:** `fastening`; slot width = `fastener_dia`; length = `slot_length` or **`fastener_dia + 0.50"`**.
- **L slots:** `_l_positions` — **2″** end margin, ~**12″** o.c., max **13″** spacing.
- **J slots:** Align to perforation rows/columns; normal position from bend geometry + `BD`.

**Panel ID:** `PANEL_ID_CLEAR_FROM_FLANGE_END = 4.0`″, `PANEL_ID_CLEAR_PAST_SLOT = 0.125`″ — avoids sitting on the first slot when that side is in `fastening_pair`.

---

## CSV / Streamlit

- Template may still include **example rows** — delete before production batch upload.
- Delimiters, UTF-8/UTF-16, slot column aliases unchanged.
- After deploy changes: **Reboot app** in Streamlit; confirm UI build marker if you use one.

---

## Intentional gaps

- **Nesting** — `nest_panels` / `write_nesting_dxf` still stubs.

---

## Suggested next-chat prompt

`Use CURRENT_STATE.md and KnownTruths as sources of truth; generator is screenwall_generator.py.`
