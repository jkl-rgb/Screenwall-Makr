# Screenwall Makr — Current State

**Last updated:** 2026-05-12  
**Repo HEAD (reference):** `749f39c` on `main` — shop DXF artwork: **L** bend-1 CL from nominal face (+ constants below), **no** 2×T perimeter corner notch on `cut`, **no** face-corner squares, **`bend`** layer for **L and J**.

This file is the short handoff for future work. If it conflicts with older prose in `KnownTruths` or `.cursor/rules/screenwall.mdc`, **prefer this file and the code** until those docs are reconciled.

---

## What is verified / locked in code

- **J4S** — Blank size, HC placement, `f2` / bend2 CL from blank edge, J+J miters, and Fusion cross-checks in `KnownTruths` §1–§3 for **blank geometry** still match.
- **L4S / MIX (L sides)** — Leg flat for blank sizing uses **§8**: `f1 = nominal − OSS + BA` (equivalently `nominal − BD/2 + BA/2`).
- **DXF `finished_face`** — CSV `width` × `height` = nominal opening at developed **f1+f2** runouts (`_nominal_finished_face_rect_xy` ortho; `_rt_nominal_face_corners` for RT). Holes, slots, and margins use that datum.
- **DXF `bend` layer** — Bend **1** for **every active** L or J side; bend **2** only for **J** with `f2 > 0`.  
  - **J** bend-1 CL: **HC ± BA/2** (same as Fusion bend zone from hard corner).  
  - **L** bend-1 CL: from nominal **face** edge, **0.195″** inward into the void + **BA/2**, plus **0.027″** further toward the outer perimeter (`SHOP_FINISHED_FACE_INSET`, `L_BEND_CL_OUTWARD`). See `_bend1_cl_positions`, `_draw_bend_lines`, `_draw_bend_lines_rt`.
- **DXF `cut` layer** — Single closed blank outline. `generate_panel_dxf` passes **`notch_size = 0`** into `_blank_outline` / `_blank_outline_rt`, so **no** former **2×T** relief at bend-1 intersections and **no** J+J five-point square detour on the cut polyline. **No** four **0.195″** closed squares at face corners on `cut`.
- **Panel ID** — Stick-font on `text`; when the ID flange **also has install slots**, anchor uses **`max(4″ from flange start, past first slot)`** (`PANEL_ID_CLEAR_FROM_FLANGE_END`, `PANEL_ID_CLEAR_PAST_SLOT`), else **mid-flange**.

---

## Bend & blank math (blank sizing vs DXF bend lines)

| Item | J | L |
|------|---|---|
| Leg flat `f1` (blank sizing) | `nominal − 3×BD/2` | **`nominal − OSS + BA`** |
| Lip flat `f2` | `nominal − BD/2` | — |
| **Bend 1 CL (`bend` layer)** | **HC ± BA/2** | **From nominal face:** 0.195″ + BA/2 + 0.027″ toward blank (see code) |
| Bend 2 CL (`bend` layer) | `f2` from blank edge | — |

- `OSS = R + T`, `BA = (π/2)(R + K×T)`, `BD = 2×OSS − BA`, `BA/2 = (π/4)(R + K×T)`.
- **HC** = inner hard corners of the void at `el+bd`, `eb+bd`, etc. Nominal **face** rect is offset **bd** from HC toward the aperture on active sides (ortho); RT uses the shifted nominal quad from `_rt_nominal_face_corners`.

---

## Corner joinery (perimeter `cut` only)

- **`notch_size`** in `generate_panel_dxf` is **`0.0`**; outline logic only builds `notch_path` when **`notch_size > 1e-9`**.
- **J+J** — Miter + void edges unchanged at the **geometry** level; the **cut path** no longer walks the old five-point square relief when `notch_size` is zero.
- **Non–J+J** — Void segments meet **HC** without the former quadrant + bridge notch jog on `cut`.
- **`gap_override`** — Only affects **J+J** when `gap > 0` (void endpoints pulled by `gap/2` along void edges).
- **`_draw_corner_reliefs`** — Still a **no-op**. Optional OSS face notch / back J circle in `KnownTruths` §6 remain **reference / future**, not separate DXF entities today.

---

## Streamlit (web)

- **Streamlit Community Cloud** runs whatever is on **GitHub** (`main`, `streamlit_app.py`). Local edits do not appear on the web until **commit + push**.
- The CSV template caption includes **`GENERATOR_ARTWORK_TAG`** (e.g. `shop-bend-face195-L027-no2Tnotch`). If that string is missing or stale, redeploy or use **Reboot app** on [share.streamlit.io](https://share.streamlit.io/).

---

## Install slots & panel ID

- **Layer:** `fastening`; slot width = `fastener_dia`; length = `slot_length` or **`fastener_dia + 0.50"`**.
- **L slots:** `_l_positions` — **2″** end margin, ~**12″** o.c., max **13″** spacing.
- **J slots:** Align to perforation rows/columns; normal position from bend geometry + `BD`.

---

## Intentional gaps

- **Nesting** — `nest_panels` / `write_nesting_dxf` still stubs.

---

## Suggested next-chat prompt

`Use CURRENT_STATE.md and KnownTruths as sources of truth; generator is screenwall_generator.py.`
