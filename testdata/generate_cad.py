"""Generate license-clean CAD test files (CC0): a coloured bracket assembly as STEP and IGES.

    python testdata/generate_cad.py

Pure Python — no CAD kernel needed:
- STEP (AP214): an assembly of three coloured solids (base plate, two bosses, a knob) as faceted
  B-reps (FACETED_BREP / POLY_LOOP) in millimetres, styled with COLOUR_RGB so readers get per-part
  colours. Written once as a single part with three bodies and once as a real assembly
  (NEXT_ASSEMBLY_USAGE_OCCURRENCE with placements).
- IGES 5.3: the same plate + a block as bilinear B-spline surfaces (entity 128) with colour
  definition entities (314), units = millimetres.
"""

from __future__ import annotations

import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "scenes" / "cad"

Vec = tuple[float, float, float]


# -- geometry (in millimetres) ----------------------------------------------------------------------


def box(x0: float, y0: float, z0: float, x1: float, y1: float, z1: float) -> list[list[Vec]]:
    """Six outward-facing quads (counter-clockwise seen from outside)."""
    p = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return [[p[i] for i in f] for f in faces]


def cylinder(cx: float, cy: float, z0: float, z1: float, r: float, n: int = 32) -> list[list[Vec]]:
    ring = [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]
    faces: list[list[Vec]] = [[(x, y, z0) for x, y in reversed(ring)], [(x, y, z1) for x, y in ring]]
    for i in range(n):
        (ax, ay), (bx, by) = ring[i], ring[(i + 1) % n]
        faces.append([(ax, ay, z0), (bx, by, z0), (bx, by, z1), (ax, ay, z1)])
    return faces


def sphere(cx: float, cy: float, cz: float, r: float, rings: int = 12, segs: int = 24) -> list[list[Vec]]:
    def pt(i: int, j: int) -> Vec:
        th, ph = math.pi * i / rings, 2 * math.pi * j / segs
        return (cx + r * math.sin(th) * math.cos(ph), cy + r * math.sin(th) * math.sin(ph), cz + r * math.cos(th))

    faces: list[list[Vec]] = []
    for i in range(rings):
        for j in range(segs):
            a, b, c, d = pt(i, j), pt(i + 1, j), pt(i + 1, j + 1), pt(i, j + 1)
            if i == 0:
                faces.append([a, b, c])
            elif i == rings - 1:
                faces.append([a, b, d])
            else:
                faces.append([a, b, c, d])
    return faces


# name, colour (0..1), geometry — Z is up, 120 × 80 mm plate. A tuple is a true cylinder
# (cx, cy, z0, z1, r); a list is planar polygons.
PARTS = [
    ("base_plate", (0.62, 0.64, 0.67), box(0, 0, 0, 120, 80, 8)),
    ("boss_left", (0.95, 0.45, 0.10), (30.0, 40.0, 8.0, 38.0, 12.0)),
    ("boss_right", (0.95, 0.45, 0.10), (90.0, 40.0, 8.0, 38.0, 12.0)),
    ("knob", (0.12, 0.35, 0.85), sphere(60, 40, 30, 14)),
]


# -- STEP -------------------------------------------------------------------------------------------


class _Step:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def add(self, s: str) -> int:
        self.lines.append(s)
        return len(self.lines)

    def point(self, p: Vec) -> int:
        return self.add(f"CARTESIAN_POINT('',({p[0]:.6f},{p[1]:.6f},{p[2]:.6f}))")


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _unit(v: Vec) -> Vec:
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    return (v[0] / n, v[1] / n, v[2] / n)


def _newell(poly: list[Vec]) -> Vec:
    n = [0.0, 0.0, 0.0]
    for i, a in enumerate(poly):
        b = poly[(i + 1) % len(poly)]
        n[0] += (a[1] - b[1]) * (a[2] + b[2])
        n[1] += (a[2] - b[2]) * (a[0] + b[0])
        n[2] += (a[0] - b[0]) * (a[1] + b[1])
    return _unit((n[0], n[1], n[2]))


def _dir(st: _Step, v: Vec) -> int:
    return st.add(f"DIRECTION('',({v[0]:.9f},{v[1]:.9f},{v[2]:.9f}))")


def _placement(st: _Step, origin: Vec, z: Vec, x: Vec) -> int:
    return st.add(f"AXIS2_PLACEMENT_3D('',#{st.point(origin)},#{_dir(st, z)},#{_dir(st, x)})")


def _brep(st: _Step, name: str, faces: list[list[Vec]]) -> int:
    """MANIFOLD_SOLID_BREP of planar ADVANCED_FACEs with shared straight edges."""
    verts: dict[Vec, int] = {}
    edges: dict[tuple[Vec, Vec], int] = {}

    def vertex(p: Vec) -> int:
        k = tuple(round(c, 6) for c in p)
        if k not in verts:
            verts[k] = st.add(f"VERTEX_POINT('',#{st.point(k)})")  # type: ignore[arg-type]
        return verts[k]

    def oriented(a: Vec, b: Vec) -> int:
        ka, kb = tuple(round(c, 6) for c in a), tuple(round(c, 6) for c in b)
        if (kb, ka) in edges:
            return st.add(f"ORIENTED_EDGE('',*,*,#{edges[(kb, ka)]},.F.)")
        if (ka, kb) not in edges:
            d = _sub(b, a)
            length = math.sqrt(sum(c * c for c in d))
            vec = st.add(f"VECTOR('',#{_dir(st, _unit(d))},{length:.6f})")
            line = st.add(f"LINE('',#{st.point(a)},#{vec})")
            edges[(ka, kb)] = st.add(f"EDGE_CURVE('',#{vertex(a)},#{vertex(b)},#{line},.T.)")  # type: ignore[index]
        return st.add(f"ORIENTED_EDGE('',*,*,#{edges[(ka, kb)]},.T.)")  # type: ignore[index]

    face_ids = []
    for f in faces:
        oes = [oriented(f[i], f[(i + 1) % len(f)]) for i in range(len(f))]
        loop = st.add("EDGE_LOOP('',(" + ",".join(f"#{i}" for i in oes) + "))")
        bound = st.add(f"FACE_OUTER_BOUND('',#{loop},.T.)")
        n = _newell(f)
        x = _unit(_sub(f[1], f[0]))
        plane = st.add(f"PLANE('',#{_placement(st, f[0], n, x)})")
        face_ids.append(st.add(f"ADVANCED_FACE('',(#{bound}),#{plane},.T.)"))
    shell = st.add("CLOSED_SHELL('',(" + ",".join(f"#{i}" for i in face_ids) + "))")
    return st.add(f"MANIFOLD_SOLID_BREP('{name}',#{shell})")


def _cylinder_brep(st: _Step, name: str, cx: float, cy: float, z0: float, z1: float, r: float) -> int:
    """A true cylinder: two planar discs + a CYLINDRICAL_SURFACE side with a seam edge."""
    vb = st.add(f"VERTEX_POINT('',#{st.point((cx + r, cy, z0))})")
    vt = st.add(f"VERTEX_POINT('',#{st.point((cx + r, cy, z1))})")
    zdir, xdir = (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)
    cb = st.add(f"CIRCLE('',#{_placement(st, (cx, cy, z0), zdir, xdir)},{r:.6f})")
    ct = st.add(f"CIRCLE('',#{_placement(st, (cx, cy, z1), zdir, xdir)},{r:.6f})")
    eb = st.add(f"EDGE_CURVE('',#{vb},#{vb},#{cb},.T.)")
    et = st.add(f"EDGE_CURVE('',#{vt},#{vt},#{ct},.T.)")
    seam_vec = st.add(f"VECTOR('',#{_dir(st, zdir)},{z1 - z0:.6f})")
    seam_line = st.add(f"LINE('',#{st.point((cx + r, cy, z0))},#{seam_vec})")
    es = st.add(f"EDGE_CURVE('',#{vb},#{vt},#{seam_line},.T.)")

    def face(loop_edges: list[tuple[int, bool]], surface: int, same: bool) -> int:
        oes = [st.add(f"ORIENTED_EDGE('',*,*,#{e},{'.T.' if o else '.F.'})") for e, o in loop_edges]
        loop = st.add("EDGE_LOOP('',(" + ",".join(f"#{i}" for i in oes) + "))")
        bound = st.add(f"FACE_OUTER_BOUND('',#{loop},.T.)")
        return st.add(f"ADVANCED_FACE('',(#{bound}),#{surface},{'.T.' if same else '.F.'})")

    bottom = face([(eb, False)], st.add(f"PLANE('',#{_placement(st, (cx, cy, z0), zdir, xdir)})"), False)
    top = face([(et, True)], st.add(f"PLANE('',#{_placement(st, (cx, cy, z1), zdir, xdir)})"), True)
    cyl = st.add(f"CYLINDRICAL_SURFACE('',#{_placement(st, (cx, cy, z0), zdir, xdir)},{r:.6f})")
    side = face([(eb, True), (es, True), (et, False), (es, False)], cyl, True)
    shell = st.add(f"CLOSED_SHELL('',(#{bottom},#{top},#{side}))")
    return st.add(f"MANIFOLD_SOLID_BREP('{name}',#{shell})")


def _style(st: _Step, item: int, rgb: tuple[float, float, float]) -> int:
    c = st.add(f"COLOUR_RGB('',{rgb[0]:.4f},{rgb[1]:.4f},{rgb[2]:.4f})")
    fasc = st.add(f"FILL_AREA_STYLE_COLOUR('',#{c})")
    fas = st.add(f"FILL_AREA_STYLE('',(#{fasc}))")
    ssfa = st.add(f"SURFACE_STYLE_FILL_AREA(#{fas})")
    sss = st.add(f"SURFACE_SIDE_STYLE('',(#{ssfa}))")
    ssu = st.add(f"SURFACE_STYLE_USAGE(.BOTH.,#{sss})")
    psa = st.add(f"PRESENTATION_STYLE_ASSIGNMENT((#{ssu}))")
    return st.add(f"STYLED_ITEM('color',(#{psa}),#{item})")


def _context(st: _Step) -> tuple[int, int, int]:
    """→ (application context, product context, representation context) with millimetre units."""
    app = st.add("APPLICATION_CONTEXT('automotive design')")
    st.add(f"APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,#{app})")
    pctx = st.add(f"PRODUCT_CONTEXT('',#{app},'mechanical')")
    mm = st.add("(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.))")
    rad = st.add("(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.))")
    sr = st.add("(NAMED_UNIT(*)SI_UNIT($,.STERADIAN.)SOLID_ANGLE_UNIT())")
    unc = st.add(f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-04),#{mm},'distance_accuracy_value','')")
    rctx = st.add(f"(GEOMETRIC_REPRESENTATION_CONTEXT(3)GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#{unc}))"
                  f"GLOBAL_UNIT_ASSIGNED_CONTEXT((#{mm},#{rad},#{sr}))REPRESENTATION_CONTEXT('',''))")
    return app, pctx, rctx


def _product(st: _Step, app: int, pctx: int, name: str) -> tuple[int, int]:
    """→ (product_definition, product_definition_shape)."""
    prod = st.add(f"PRODUCT('{name}','{name}','',(#{pctx}))")
    st.add(f"PRODUCT_RELATED_PRODUCT_CATEGORY('part',$,(#{prod}))")
    pdf = st.add(f"PRODUCT_DEFINITION_FORMATION('','',#{prod})")
    pdc = st.add(f"PRODUCT_DEFINITION_CONTEXT('part definition',#{app},'design')")
    pd = st.add(f"PRODUCT_DEFINITION('design','',#{pdf},#{pdc})")
    pds = st.add(f"PRODUCT_DEFINITION_SHAPE('','',#{pd})")
    return pd, pds


def _axis(st: _Step, origin: Vec = (0, 0, 0)) -> int:
    o = st.point(origin)
    z = st.add("DIRECTION('',(0.,0.,1.))")
    x = st.add("DIRECTION('',(1.,0.,0.))")
    return st.add(f"AXIS2_PLACEMENT_3D('',#{o},#{z},#{x})")


def _write_step(path: Path, st: _Step, name: str) -> None:
    header = (
        "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('Renda CAD test data (CC0)'),'2;1');\n"
        f"FILE_NAME('{path.name}','2026-10-19T00:00:00',('renda'),('renda'),'renda generate_cad.py','renda','');\n"
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\nENDSEC;\nDATA;\n"
    )
    body = "".join(f"#{i}={s};\n" for i, s in enumerate(st.lines, 1))
    path.write_text(header + body + "ENDSEC;\nEND-ISO-10303-21;\n")
    print(f"wrote {path.relative_to(HERE)} ({path.stat().st_size // 1024} KB, {len(st.lines)} entities)")


def write_step_part(path: Path) -> None:
    """One product, several coloured bodies."""
    st = _Step()
    app, pctx, rctx = _context(st)
    pd, pds = _product(st, app, pctx, "bracket")
    items = [_axis(st)]
    styled = []
    for name, rgb, geom in PARTS:
        b = _cylinder_brep(st, name, *geom) if isinstance(geom, tuple) else _brep(st, name, geom)
        items.append(b)
        styled.append(_style(st, b, rgb))
    rep = st.add("ADVANCED_BREP_SHAPE_REPRESENTATION('bracket',(" + ",".join(f"#{i}" for i in items) + f"),#{rctx})")
    st.add(f"SHAPE_DEFINITION_REPRESENTATION(#{pds},#{rep})")
    st.add("MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION('',(" + ",".join(f"#{i}" for i in styled) + f"),#{rctx})")
    _write_step(path, st, "bracket")


def write_step_assembly(path: Path) -> None:
    """A real assembly: the plate as one part, a reusable 'boss' part placed twice, and the knob."""
    st = _Step()
    app, pctx, rctx = _context(st)
    asm_pd, asm_pds = _product(st, app, pctx, "bracket_assembly")
    asm_origin = _axis(st)
    asm_rep = st.add(f"SHAPE_REPRESENTATION('bracket_assembly',(#{asm_origin}),#{rctx})")
    st.add(f"SHAPE_DEFINITION_REPRESENTATION(#{asm_pds},#{asm_rep})")
    styled = []

    def part(name: str, rgb, geom) -> tuple[int, int]:
        pd, pds = _product(st, app, pctx, name)
        origin = _axis(st)
        b = _cylinder_brep(st, name, *geom) if isinstance(geom, tuple) else _brep(st, name, geom)
        rep = st.add(f"ADVANCED_BREP_SHAPE_REPRESENTATION('{name}',(#{origin},#{b}),#{rctx})")
        st.add(f"SHAPE_DEFINITION_REPRESENTATION(#{pds},#{rep})")
        styled.append(_style(st, b, rgb))
        return pd, rep

    occ = 0

    def place(child_pd: int, child_rep: int, at: Vec, label: str) -> None:
        nonlocal occ
        occ += 1
        nauo = st.add(f"NEXT_ASSEMBLY_USAGE_OCCURRENCE('{occ}','{label}','',#{asm_pd},#{child_pd},$)")
        target = _axis(st, at)
        source = _axis(st)
        idt = st.add(f"ITEM_DEFINED_TRANSFORMATION('','',#{source},#{target})")
        rrwt = st.add(f"(REPRESENTATION_RELATIONSHIP('','',#{child_rep},#{asm_rep})"
                      f"REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#{idt})SHAPE_REPRESENTATION_RELATIONSHIP())")
        pds = st.add(f"PRODUCT_DEFINITION_SHAPE('','',#{nauo})")
        st.add(f"CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#{rrwt},#{pds})")

    plate = part("base_plate", PARTS[0][1], PARTS[0][2])
    boss = part("boss", PARTS[1][1], (0.0, 0.0, 0.0, 30.0, 12.0))
    knob = part("knob", PARTS[3][1], sphere(0, 0, 0, 14))
    place(*plate, (0, 0, 0), "plate")
    place(*boss, (30, 40, 8), "boss_left")
    place(*boss, (90, 40, 8), "boss_right")
    place(*knob, (60, 40, 30), "knob")
    st.add("MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION('',(" + ",".join(f"#{i}" for i in styled) + f"),#{rctx})")
    _write_step(path, st, "bracket_assembly")


# -- IGES -------------------------------------------------------------------------------------------


def _iges_line(text: str, section: str, seq: int) -> str:
    return f"{text:<72}{section}{seq:7d}\n"


def _hollerith(s: str) -> str:
    return f"{len(s)}H{s}"


def write_iges(path: Path, unit_flag: int = 2, unit_name: str = "MM") -> None:
    """Box surfaces as bilinear B-spline patches (entity 128) with RGB colours (entity 314).
    Coordinates are written as millimetre numbers; `unit_flag` only changes what the file declares."""
    parts = [("plate", (62, 64, 67), box(0, 0, 0, 120, 80, 8)), ("block", (95, 45, 10), box(40, 20, 8, 80, 60, 40))]
    entities: list[tuple[int, list[str], int, int]] = []  # (type, params, color, form)
    color_de: dict[str, int] = {}
    # colour definitions first so surfaces can point at them (DE pointers are odd line numbers)
    for name, rgb, _ in parts:
        color_de[name] = 2 * len(entities) + 1
        entities.append((314, ["314", f"{rgb[0]:.1f}", f"{rgb[1]:.1f}", f"{rgb[2]:.1f}", _hollerith(name)], 0, 0))
    for name, _, faces in parts:
        for f in faces:
            p0, p1, p2, p3 = f  # quad corners, CCW
            # control points ordered u fastest: (u0,v0), (u1,v0), (u0,v1), (u1,v1)
            cps = [p0, p1, p3, p2]
            params = ["128", "1", "1", "1", "1", "0", "0", "1", "0", "0",
                      "0.", "0.", "1.", "1.", "0.", "0.", "1.", "1.", "1.", "1.", "1.", "1."]
            for c in cps:
                params += [f"{c[0]:.4f}", f"{c[1]:.4f}", f"{c[2]:.4f}"]
            params += ["0.", "1.", "0.", "1."]
            entities.append((128, params, -color_de[name], 0))

    start = [_iges_line("Renda CAD test data (CC0): a plate and a block as B-spline patches.", "S", 1)]
    g = [_hollerith(","), _hollerith(";"), _hollerith("renda"), _hollerith(path.name), _hollerith("renda generate_cad.py"),
         _hollerith("1.0"), "32", "38", "6", "308", "15", _hollerith("renda"), "1.0", str(unit_flag), _hollerith(unit_name), "1", "0.1",
         _hollerith("20261019.000000"), "0.0001", "200.0", _hollerith("renda"), _hollerith("renda"), "11", "0",
         _hollerith("20261019.000000")]
    gtext = ",".join(g) + ";"
    glines = [gtext[i : i + 72] for i in range(0, len(gtext), 72)]
    global_section = [_iges_line(t, "G", i + 1) for i, t in enumerate(glines)]

    directory: list[str] = []
    param_lines: list[str] = []
    for idx, (etype, params, color, form) in enumerate(entities):
        de = 2 * idx + 1
        text = ",".join(params) + ";"
        chunks: list[str] = []
        cur = ""
        for tok in [t + "," for t in params[:-1]] + [params[-1] + ";"]:
            if len(cur) + len(tok) > 64:
                chunks.append(cur)
                cur = ""
            cur += tok
        chunks.append(cur)
        first_p = len(param_lines) + 1
        for c in chunks:
            param_lines.append(f"{c:<64}{de:8d}P{len(param_lines) + 1:7d}\n")
        del text
        status = "00010000" if etype == 314 else "00000000"
        d1 = f"{etype:8d}{first_p:8d}{0:8d}{1:8d}{0:8d}{0:8d}{0:8d}{0:8d}{status:>8}"
        d2 = f"{etype:8d}{0:8d}{color:8d}{len(chunks):8d}{form:8d}{'':8}{'':8}{'':8}{0:8d}"
        directory.append(_iges_line(d1, "D", de))
        directory.append(_iges_line(d2, "D", de + 1))
    term = f"S{len(start):7d}G{len(global_section):7d}D{len(directory):7d}P{len(param_lines):7d}"
    path.write_text("".join(start + global_section + directory + param_lines) + f"{term:<72}T{1:7d}\n")
    print(f"wrote {path.relative_to(HERE)} ({path.stat().st_size // 1024} KB, {len(entities)} entities)")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    write_step_part(OUT / "bracket.step")
    write_step_assembly(OUT / "bracket_assembly.stp")
    write_iges(OUT / "plate_block.igs")


if __name__ == "__main__":
    main()
