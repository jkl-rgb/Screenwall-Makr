# Screenwall Makr — Current State

**Release:** Beta v0.1.0 — First Draft  
**Last updated:** 2026-05-15  
**Milestone:** First draft ready for team testing (not production sign-off).

This file is the short handoff for future work. If it conflicts with older prose in `KnownTruths` or `.cursor/rules/screenwall.mdc`, **prefer this file and the code** until those docs are reconciled.

---

## Status

- **Beta v0.1.0 — First Draft** is the current team-testing release. Streamlit shows **Beta** in the title and `Beta v0.1.0 · Beta — First Draft · 2026-05-15` under the header (`APP_VERSION` / `APP_RELEASE_LABEL` in `screenwall_generator.py`).
- **Primary shop geometry is in good shape:** blank sizing, developed flats (`shop_flat_mode` auto), **`bend`** artwork, **`cut`** perimeter (CI vs BD), J install-slot shop rules, J+J miter-safe slot stations, RT panel ID on the **parallel** flange.
- **Batch CSV:** no coded row limit; ~150 panels ≈ 30 s locally. Use **unique `panel_id`** per row or the ZIP will contain one file per ID (last row wins). Very large batches may hit Streamlit Cloud memory/time when building the in-memory ZIP.
- **G-code export:** optional `.nc` per panel (`gcode_export.py`) from the same in-memory drawing as the DXF (`build_panel_document`). Laser (M3/M5) or mill (Z-plunge) dialect; panel-ID etch → holes → slots → perimeter last; `finished_face`/`bend` reference-only (bend etch opt-in). Paths carry `(op=… layer=… shape=…)` comments for round-trip.
- **Next engineering (post-beta):** G-code **import** (see `GCODE_IMPORT_PLAN.md`); nesting / sheet layout; duplicate-ID warnings; optional batch progress UI.

---

## What is verified / implemented in code

- **Flange codes:** `L4S`, `J4S`, `L2TB`, `J2TB`, `L2LR`, `J2LR`, `MIX`, `RT4S`, `RT4J`.
- **J4S / J lip slots:** nominal F2 − hole-to-face OC to blank outer lip; outer perforation line only; ≤ 12″ c–c; J+J trim to straight lip between miters.
- **RT4S / RT4J:** trapezoid face, RT blank outline, RT slots; **panel ID on parallel side** (not angled hypotenuse).
- **L4S / MIX (L sides):** Leg flat for blank sizing uses **§8**: `f1 = nominal − OSS + BA`. Shop **auto** scales developed L/J from calibration ref.
- **DXF `finished_face`:** CSV `width` × `height` at developed f1+f2 runouts; RT uses `_rt_nominal_face_corners` with `SHOP_FLANGE_CORNER_INSET`.
- **DXF `bend`:** Bend 1 all active L/J; bend 2 for J with f2 > 0. J bend-1 HC ± BA/2; L bend-1 from `_f1_bend_inset_from_face` + BA/2.
- **DXF `cut`:** `notch_size = 0`; void inner HC uses **CI** (not BD on perimeter).
- **Panel ID:** Stick-font on `text`; inside-face-up convention; RT uses `_panel_id_anchor_rt`.

---

## Bend & blank math (summary)

| Item | J | L |
|------|---|---|
| Leg flat `f1` (blank sizing) | `nominal − 3×BD/2` | `nominal − OSS + BA` |
| Lip flat `f2` | `nominal − BD/2` | — |

See `KnownTruths` §1–§3 for Fusion cross-checks.

---

## Streamlit (web)

- Deploy from **`main`**; entry `streamlit_app.py`; page title **Screenwall Makr Beta**.
- Confirm deploy: caption shows **`Release: Beta v0.1.0 · …`** and **`DXF engine:`** `GENERATOR_ARTWORK_TAG` (currently `shop-RT-panelId-parallel-flange`).
- Reboot app on [share.streamlit.io](https://share.streamlit.io/) after push if the version string is stale.

---

## Intentional gaps (beta)

- **Nesting** — stubs only.
- **No duplicate `panel_id` guard** in UI.
- **Zip built entirely in RAM** — limits very large jobs on Cloud.

---

## Suggested next-chat prompt

`Use CURRENT_STATE.md and KnownTruths; Beta v0.1.0 first draft. Generator: screenwall_generator.py.`
