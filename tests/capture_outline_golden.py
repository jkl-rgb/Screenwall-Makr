"""Capture golden DXF geometry fixtures for corner-fold-clearance regression tests.

Run from the repo root:  python3 tests/capture_outline_golden.py

Writes tests/fixtures/outline_golden.json containing, per spec, every entity's
normalized geometry with the corner fold clearance DISABLED
(corner_gap_override=0). Captured 2026-08-22 from the pre-fold generator;
all specs are byte-identical to that generator except G-RT4J, whose outline
was re-captured after the RT-bottom br/tr arrival fix (the pre-fix outline
drew a stray diagonal through those corner voids).
"""
import dataclasses
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screenwall_generator import PanelSpec, build_panel_document


def golden_specs():
    return [
        PanelSpec("G-L4S", 24.0, 18.0, 0.1875, "L4S", "L", 2.0, None,
                  0.25, 1.0, "straight", fastening_pair="tb"),
        PanelSpec("G-J4S", 24.0, 18.0, 0.1875, "J4S", "J", 2.0, 1.0,
                  0.25, 1.0, "straight", fastening_pair="tb"),
        PanelSpec("G-J4S-125", 24.0, 18.0, 0.125, "J4S", "J", 2.0, 1.0,
                  0.25, 1.0, "staggered", fastening_pair="lr"),
        PanelSpec("G-J4S-GAP", 24.0, 18.0, 0.1875, "J4S", "J", 2.0, 1.0,
                  0.25, 1.0, "straight", fastening_pair="tb", gap_override=0.0625),
        PanelSpec("G-MIX", 24.0, 18.0, 0.1875, "MIX", "L", 0.0, None,
                  0.25, 1.0, "straight", fastening_pair="tb",
                  top_type="J", top_f1=2.0, top_f2=1.0,
                  bottom_type="J", bottom_f1=2.0, bottom_f2=1.0,
                  left_type="L", left_f1=1.5, left_f2=0.0,
                  right_type="L", right_f1=0.0, right_f2=0.0),
        PanelSpec("G-RT4S", 24.0, 18.0, 0.1875, "RT4S", "L", 2.0, None,
                  0.25, 1.0, "straight", fastening_pair="tb",
                  rt_opposing_edge="top", rt_leg_left=18.0, rt_leg_right=12.0),
        PanelSpec("G-RT4J", 24.0, 20.0, 0.1875, "RT4J", "J", 2.0, 1.0,
                  0.25, 1.0, "straight", fastening_pair="b",
                  rt_opposing_edge="bottom", rt_leg_left=20.0, rt_leg_right=14.0),
    ]


def _r(v):
    return round(float(v), 9)


def entity_record(e):
    t = e.dxftype()
    rec = {"type": t, "layer": e.dxf.layer}
    if t == "LINE":
        rec["geo"] = [_r(e.dxf.start.x), _r(e.dxf.start.y), _r(e.dxf.end.x), _r(e.dxf.end.y)]
    elif t == "LWPOLYLINE":
        rec["closed"] = bool(e.closed)
        rec["geo"] = [[_r(x), _r(y), _r(b)] for x, y, b in e.get_points("xyb")]
    elif t == "CIRCLE":
        rec["geo"] = [_r(e.dxf.center.x), _r(e.dxf.center.y), _r(e.dxf.radius)]
    elif t == "ARC":
        rec["geo"] = [_r(e.dxf.center.x), _r(e.dxf.center.y), _r(e.dxf.radius),
                      _r(e.dxf.start_angle), _r(e.dxf.end_angle)]
    elif t == "TEXT":
        rec["geo"] = [e.dxf.text]
    else:
        rec["geo"] = []
    return rec


def capture():
    data = {}
    for spec in golden_specs():
        spec = dataclasses.replace(spec, corner_gap_override=0.0)
        doc = build_panel_document(spec)
        records = sorted(
            (entity_record(e) for e in doc.modelspace()),
            key=lambda r: json.dumps(r, sort_keys=True),
        )
        data[spec.panel_id] = records
    return data


if __name__ == "__main__":
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fixtures", "outline_golden.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump(capture(), fh, sort_keys=True)
    n = sum(len(v) for v in capture().values())
    print(f"wrote {out_path}: {n} entities across {len(capture())} specs")
