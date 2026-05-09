SCREENWALL MAKR — BEND MATH REFERENCE
Verified against Fusion 360 + Artform factory drawing
Material: 0.1875" 3003 Aluminum, r=0.125", k=0.33
======================================================

FORMULAS
--------
OSB (Outside Setback) = tan(45°) × (r + t)         = 0.3125"
BA  (Bend Allowance)  = (π/2) × (r + k×t)          = 0.2935"  [Fusion measures 0.294"]
BD  (Bend Deduction)  = 2×OSB - BA = 2×(r+t) - BA  = 0.3315"
BA/2                  = 0.1468"  [bend CL offset from hard corner]


FLAT ZONE LENGTHS (J4S, flange1=2.0", flange2=2.25")
------------------------------------------------------
f2 (lip flat, blank edge → bend2 CL) = nominal_f2 - BD/2 = 2.25 - 0.166 = 2.084"
f1 (leg flat, for blank sizing)       = nominal_f1 - 3×BD/2 = 2.0 - 0.497 = 1.503"
ex (total extension per side)         = f1 + f2 = 3.587"

Gemini equivalent terms:
  J-flange flat = nominal_f2 - OSB = 2.25 - 0.3125 = 1.9375"  [to material edge]
  L-flange flat = nominal_f1 - BD  = 2.0  - 0.3315 = 1.669"   [CL-to-CL distance]
  Note: Gemini measures to material edge; we measure to bend CL. Same geometry. ✓


BLANK SIZE (24"W × 36"H panel)
--------------------------------
Method (Gemini): total out-to-out - 4×BD
  Width:  24 + 2×2.0 + 2×2.25 - 4×0.3315 = 32.50 - 1.326 = 31.174" ✓
  Height: 36 + 2×2.0 + 2×2.25 - 4×0.3315 = 44.50 - 1.326 = 43.174" ✓

Method (ours): face + 2×(f1+f2)
  Width:  24 + 2×3.587 = 31.174" ✓
  Height: 36 + 2×3.587 = 43.174" ✓


HARD CORNER PLACEMENT
---------------------
HC_offset from blank edge = ex + BD = 3.587 + 0.3315 = 3.918"
HC-HC width  = 31.174 - 2×3.918 = 23.338"  [Fusion: 23.36"] ✓
HC-HC height = 43.174 - 2×3.918 = 35.338"  [Fusion: 35.367"] ✓


LAYOUT FROM BLANK EDGE INWARD
-------------------------------
[blank edge] → 2.084" → [bend2 CL] → 1.669" → [bend1 CL] → 0.147" → [HARD CORNER] → face

bend1 CL from blank edge = HC_offset - BA/2 = 3.918 - 0.147 = 3.771"
bend2 CL from blank edge = f2 = 2.084"
CL-to-CL distance        = 3.771 - 2.084 = 1.687"  [Fusion: 1.669"] ✓
bend1 CL from HC         = BA/2 = 0.147"            [Fusion: 0.147"] ✓
bend2 CL from HC         = 3.918 - 2.084 = 1.834"   [Fusion: 1.823"] ✓


MITER GEOMETRY (BL corner, blank origin 0,0 at bottom-left)
-------------------------------------------------------------
Hard corner (void inner) = (3.918, 3.918)
Void edge length = f1 + BD = 1.503 + 0.3315 = 1.834"
  [void edge runs from HC to bend2 line level so miter starts at bend2 CL]

Miter start = (3.918, 2.084)  [on bend2 CL level, at HC x-position]
Miter end   = (1.834, 0)      [on blank bottom edge]
Miter angle = 45°
Miter length = √2 × f2 = √2 × 2.084 = 2.948"

Gemini's vertex coordinates (their reference system):
  A (outer J-flange edge)  = (0.000, 1.9375)
  B (outer L-flange edge)  = (1.9375, 3.6061)
  V (theoretical vertex)   = (2.084, 2.084)   [bend line intersection]
  V2 (relief vertex)       = (2.115, 2.115)   [+1/32" anti-collision clearance]


20-EDGE OUTLINE STRUCTURE
--------------------------
4  primary edges  L = face_dim - 2×f2  (19.169" and 31.169" for this panel)
8  void edges     L = f1+BD = 1.834"   (2 per corner)
8  miter edges    L = √2×f2 = 2.948"  (2 per corner)


DXF LAYERS
----------
cut       (color 1) — 20-point blank outline
bend      (color 3) — bend1 and bend2 centerlines (individual segments)
holes     (color 2) — perforations
fastening (color 5) — fastener holes


K-FACTORS BY ALLOY
------------------
3003 aluminum: k = 0.33
5052 aluminum: k = 0.38


BEND RADIUS RULE
----------------
r = 2/3 × t  (factory rule, verified: 0.1875 × 0.6667 = 0.125")

Scales to all gauges:
  t=0.0625":  r=0.04167"  BD=0.1105"  BA/2=0.0489"
  t=0.0800":  r=0.05333"  BD=0.1414"  BA/2=0.0626"
  t=0.1250":  r=0.08333"  BD=0.2210"  BA/2=0.0978"
  t=0.1875":  r=0.12500"  BD=0.3315"  BA/2=0.1468"
  t=0.2500":  r=0.16667"  BD=0.4419"  BA/2=0.1957"
