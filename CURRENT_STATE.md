# Screenwall Makr — Current State

**Last updated:** 2026-05-13  
**Repo HEAD (reference):** `96ad3f6` on `main` — shop DXF: **cut** void HC uses **`SHOP_FLANGE_CORNER_INSET`** (0.195″ ref) from nominal face inner corner; **`bend`** layer still uses **BD** from nominal face for ortho HC quad; F1 bend-1 inset from face (`SHOP_F1_BEND_INSET_REF` + material blend); developed flats L/J calibrated; RT layout uses same **CI**/**BD** split; no 2×T perimeter corner notch on `cut`; **`GENERATOR_ARTWORK_TAG`** = `shop-f1inset168-L1665-J1497-1582`.

This file is the short handoff for future work. If it conflicts with older prose in `KnownTruths` or `.cursor/rules/screenwall.mdc`, **prefer this file and the code** until those docs are reconciled.

---

## Status (honest)

- **Primary shop geometry is not “locked solid.”** Blank sizing, developed flats, bend artwork, and perimeter cut are converging against coupons and drawings, but not every combination has been field-verified.
- **Next engineering focus (planned): fastening / perforation holes** — alignment to face, margins, patterns, and any slot vs hole interactions. Prior chat will carry that work.

---

## What is verified / implemented in code

- **J4S** — Blank size, `f2` / bend-2 CL from blank edge, J+J miters, and Fusion cross-checks in `KnownTruths` §1–§3 for **blank sizing / bend theory** remain the main Fusion anchor (see §3 note below for **`cut`** vs theory).
- **L4S / MIX (L sides)** — Leg flat for blank sizing uses **§8**: `f1 = nominal − OSS + BA` (equivalently `nominal − BD/2 + BA/2`). Shop **auto** mode scales developed L/J from calibration ref vs bend theory (`shop_flat_mode`).
- **DXF `finished_face`** — CSV `width` × `height` = nominal opening at developed **f1+f2** runouts (`_nominal_finished_face_rect_xy` ortho; `_rt_nominal_face_corners` for RT with **`hc_to_face_pad = SHOP_FLANGE_CORNER_INSET`**). Holes, slots, and margins use that datum.
- **DXF `bend` layer** — Bend **1** for **every active** L or J side; bend **2** only for **J** with `f2 > 0`.  
  - **J** bend-1 CL: **HC ± BA/2** where HC quad is **nominal face + BD** along active legs (ortho: `ffx0+bd`, …).  
  - **L** bend-1 CL: **`_f1_bend_inset_from_face`** from nominal face (shop ref **0.168″** at calibration alloy/thickness, blended for other materials) + **BA/2** toward blank, with **`L_BEND_CL_OUTWARD`** used in the theory blend path. See `_bend1_cl_positions` (`face_corner_pad=bd` from `_draw_bend_lines`), `_draw_bend_lines`, `_draw_bend_lines_rt`.
- **DXF `cut` layer** — Single closed blank outline. `generate_panel_dxf` passes **`notch_size = 0`** into `_blank_outline` / `_blank_outline_rt`, so **no** former **2×T** relief at bend-1 intersections and **no** J+J five-point square detour on the cut polyline. **No** four closed squares at face corners on `cut`.  
  - **Void inner HC (ortho):** `fx0 = el + CI`, `fy0 = eb + CI`, mirrored on right/top (`CI = SHOP_FLANGE_CORNER_INSET`). This fixes face-corner → inside-flange-leg on the perimeter (~**0.195″** ref), **not** BD (~0.31″ on 5052 / 0.1875″).  
  - **`_bend1_cl_positions`** inside `_blank_outline` uses **`face_corner_pad=CI`** so bend-1 CL references match the cut quad.  
- **Panel ID** — Stick-font on `text`; when the ID flange **also has install slots**, anchor uses **`max(4″ from flange start, past first slot)`** (`PANEL_ID_CLEAR_FROM_FLANGE_END`, `PANEL_ID_CLEAR_PAST_SLOT`), else **mid-flange**.

---

## Bend & blank math (blank sizing vs DXF layers)

| Item | J | L |
|------|---|---|
| Leg flat `f1` (blank sizing) | `nominal − 3×BD/2` | **`nominal − OSS + BA`** |
| Lip flat `f2` | `nominal − BD/2` | — |
| **Bend 1 CL (`bend` layer)** | **HC ± BA/2** (HC from **nominal face + BD**) | **From nominal face:** `_f1_bend_inset_from_face` + **BA/2** toward blank (see code) |
| Bend 2 CL (`bend` layer) | `f2` from blank edge | — |

- `OSS = R + T`, `BA = (π/2)(R + K×T)`, `BD = 2×OSS − BA`, `BA/2 = (π/4)(R + K×T)`.
- **Nominal face** inner corner: ortho **`(el, eb)`**; **`finished_face`** layer matches CSV aperture.
- **`cut` void inner corners:** inset **`CI`** from that corner along active axes (not **BD**). **`bend`** ortho HC quad: **`BD`** inset from nominal face for J bend math.

---

## Corner joinery (perimeter `cut` only)

- **`notch_size`** in `generate_panel_dxf` is **`0.0`**; outline logic only builds `notch_path` when **`notch_size > 1e-9`**.
- **J+J** — Miter + void edges; void runouts use **`f1 + CI`** (and **`f1 + f2 + CI`** where no miter).  
- **Non–J+J** — Void segments meet **HC** without the former quadrant + bridge notch jog on `cut`.
- **`gap_override`** — Only affects **J+J** when `gap > 0` (void endpoints pulled by `gap/2` along void edges).
- **`_draw_corner_reliefs`** — Still a **no-op**. Optional OSS face notch / back J circle in `KnownTruths` §6 remain **reference / future**, not separate DXF entities today.

---

## Streamlit (web)

- **Streamlit Community Cloud** runs whatever is on **GitHub** (`main`, `streamlit_app.py`). Local edits do not appear on the web until **commit + push**.
- The CSV template caption includes **`DXF engine:`** and **`GENERATOR_ARTWORK_TAG`** (currently **`shop-f1inset168-L1665-J1497-1582`**). If that string is missing or stale, redeploy or use **Reboot app** on [share.streamlit.io](https://share.streamlit.io/).

---

## Install slots & panel ID

- **Layer:** `fastening`; slot width = `fastener_dia`; length = `slot_length` or **`fastener_dia + 0.50"`**.
- **L slots:** `_l_positions` — **2″** end margin, ~**12″** o.c., max **13″** spacing.
- **J slots:** Align to perforation rows/columns; normal position from bend geometry + `BD`.

---

## Intentional gaps

- **Nesting** — `nest_panels` / `write_nesting_dxf` still stubs.
- **Primary geometry** — Shop artwork and theory are aligned in code but not fully closed across all panel types, alloys, and shop measurements.
- **Fastening / face holes** — Next area to harden (margins, grid vs face, RT, etc.).

---

## Suggested next-chat prompt

`Use CURRENT_STATE.md and KnownTruths; generator is screenwall_generator.py. Next: fastening holes (and related DXF layers).`
