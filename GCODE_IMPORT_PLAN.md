# G-code Import — Research & Implementation Plan

**Status:** Tier 1 **implemented** in `gcode_import.py` (laser / mill / turret-punch
dialects, annotated round-trip + heuristic classification, DXF rebuild, UI import
section with PDF preview). Tier 2 (`PanelSpec` recovery) seeded by `summarize()`;
full parametric recovery still open.
**Companion feature:** `gcode_export.py` (shipped) — exports `.nc` per panel from the
same in-memory drawing as the DXF writer.

Goal: allow Screenwall Makr to **build artwork from G-code** — read an `.nc`
program, reconstruct layered geometry (and, where possible, panel semantics),
and re-enter the normal pipeline (DXF out, future nesting, re-export).

---

## 1. Key design decision: geometry actions live in the G-code

Raw G-code is just ordered motion; it does not say *what* a path is. The
exporter therefore annotates every path with a structured comment:

```
(op=cut layer=holes shape=circle)
G0 X10.5 Y12.0
M3 S1000
G3 X9.75 Y12.0 I-0.375 J0 F60
G3 X10.5 Y12.0 I0.375 J0
M5
```

This is the "define actions of geometry from the G-code" convention:

| Field  | Values today | Meaning on import |
|--------|--------------|-------------------|
| `op`   | `cut`, `mark` | machining intent |
| `layer`| `cut`, `holes`, `fastening`, `text`, `bend` | target DXF layer |
| `shape`| `circle`, `slot`, `loop`, `polyline`, `line` | reconstruction hint |

Treat this as a versioned mini-format (**SCREENWALL-NC v1**, declared in the
program header comment). Files we exported round-trip losslessly; foreign
files fall back to heuristics (§3). The vocabulary is extensible — new actions
(e.g. `op=etch`, `layer=finished_face`, `shape=relief`) are new comment values,
no parser changes.

## 2. Import tiers

**Tier 1 — geometry import (build artwork from any 2D G-code).**
Parse motion, rebuild paths, assign layers, emit an ezdxf document identical in
structure to `build_panel_document` output. Feasible with no new dependencies.

**Tier 2 — parametric recovery (G-code → `PanelSpec` proposal).**
From Tier 1 geometry, measure blank extents, hole diameter/pitch/pattern, slot
stations, and propose a CSV row for review. Reliable for annotated files;
best-effort for foreign files. Bend/flange semantics (L vs J, developed flats)
are **not** recoverable from cut geometry alone — the operator confirms or the
annotations carry them (v2 could embed the originating CSV row as a comment,
making recovery exact).

## 3. Parser requirements (`gcode_import.py`)

1. **Tokenizer / modal state.** Words `G/M/X/Y/Z/I/J/R/F/S`; modal motion
   (G0/G1/G2/G3), units (G20/G21 → normalize to inches), distance mode
   (G90/G91 → normalize to absolute), plane (G17 only; reject G18/G19).
   Comments: `( … )` and `;` end-of-line.
2. **Engagement model** (what counts as "pen down"):
   - laser dialect: paths live between `M3 … M5`;
   - mill dialect: XY motion while `Z < 0` (plunge/retract detection);
   - configurable Z threshold for foreign files.
3. **Arc reconstruction.** I/J center offsets (incremental, as we emit) plus
   R-word arcs for foreign files; convert to bulge for DXF slots, or CIRCLE
   when a closed path is exactly two half arcs about one center.
4. **Heuristic layer classification** when annotations are absent:
   - closed loop of arcs about one center → `holes` circle;
   - closed 2-line + 2-semicircle loop → `fastening` slot;
   - largest-area closed loop → `cut` perimeter (all other loops inside it);
   - short open strokes clustered near an edge → `text`;
   - long straight open lines spanning the blank → `bend` marks.
5. **Tolerance handling.** Snap nearly-coincident endpoints, merge collinear
   runs, and re-fit arcs from chordal polylines (many controllers/CAM posts
   flatten arcs into tiny G1 segments) with a configurable chord tolerance.
6. **Out of scope / reject with clear errors:** cutter compensation (G41/G42),
   canned cycles, work offsets beyond a simple XY shift (G54…), 3D moves.

## 4. Wiring it in

- `parse_gcode(text, config) -> list[Path]` — same path model as
  `gcode_export.extract_paths` (shared dataclass module once import lands).
- `paths_to_document(paths) -> ezdxf doc` — inverse of the exporter; feeds the
  existing save/ZIP flow, so an imported NC immediately yields shop DXF.
- `propose_spec(paths) -> dict` — Tier 2 measurements for a prefilled CSV row.
- Streamlit: an **Import G-code** uploader next to the CSV uploader; preview
  the classified layers, then offer DXF download / spec CSV.
- Tests: round-trip (`panel_gcode` → `parse_gcode` → entity census equals
  `build_panel_document`), heuristics on stripped-comment programs, unit and
  modal-state edge cases.

## 5. Risk notes

- Dialect variance in foreign files is the main cost driver; annotated
  Screenwall files avoid it entirely, so ship round-trip first, heuristics second.
- Perimeter-vs-void classification must use containment + area, not path order.
- Text strokes are decorative; misclassification there must never contaminate
  `cut`/`holes` (bias unknown short paths toward `text`/ignore, never `cut`).
