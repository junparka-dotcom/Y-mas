"""
밤부랩 H2D 베드(350x320mm)에 맞춰 부품을 묶은 plate 생성기.

재료별로 분리 출력한다:
  - 하중 경로(pad, adapter, cradle) -> PETG   -> plates/petg/
  - 비하중(chock, ramp, 인클로저)   -> PLA     -> plates/pla/
각 plate 는 STL(멀티오브젝트 ASCII) + 3MF(Bambu Studio 네이티브) 둘 다 생성.
Bambu Studio 에서 열면 각 부품이 개별 오브젝트로 인식되어 재배치 가능.

사용:
    python3 make_plates.py            # stl/ 의 개별 STL을 읽어 plates/{petg,pla}/ 에 생성
"""
import glob, os, re, sys

BED_X, BED_Y = 350, 320          # H2D 베드
MARGIN = 8                       # 베드 가장자리 여백
GAP = 6                          # 부품 간 간격

HERE = os.path.dirname(os.path.abspath(__file__))
STL_DIR = os.path.join(HERE, "stl")
OUT_DIR = os.path.join(HERE, "plates")

# 부품 -> 재료 (하중 경로만 PETG, 나머지 PLA)
MATERIAL = {
    "rail_pad":       "petg",
    "rail_adapter":   "petg",
    "wheel_cradle":   "petg",
    "wheel_chock":    "pla",
    "rail_ramp_seg0": "pla",
    "rail_ramp_seg1": "pla",
    "rail_ramp_seg2": "pla",
    "mcu_box_base":   "pla",
    "mcu_box_lid":    "pla",
    "hx711_box_base": "pla",
    "hx711_box_lid":  "pla",
}


def read_tris(path):
    """ASCII STL -> list of (normal, v0, v1, v2)."""
    tris = []
    with open(path, errors="ignore") as f:
        text = f.read()
    facets = re.findall(
        r"facet normal\s+([-\d.eE]+)\s+([-\d.eE]+)\s+([-\d.eE]+).*?"
        r"vertex\s+([-\d.eE]+)\s+([-\d.eE]+)\s+([-\d.eE]+).*?"
        r"vertex\s+([-\d.eE]+)\s+([-\d.eE]+)\s+([-\d.eE]+).*?"
        r"vertex\s+([-\d.eE]+)\s+([-\d.eE]+)\s+([-\d.eE]+)",
        text, re.S)
    for g in facets:
        n = tuple(float(v) for v in g[0:3])
        v0 = tuple(float(v) for v in g[3:6])
        v1 = tuple(float(v) for v in g[6:9])
        v2 = tuple(float(v) for v in g[9:12])
        tris.append((n, v0, v1, v2))
    return tris


def bounds(tris):
    xs = [v[0] for _, *vs in tris for v in vs]
    ys = [v[1] for _, *vs in tris for v in vs]
    return min(xs), max(xs), min(ys), max(ys)


def offset(tris, dx, dy):
    return [(n, (a[0]+dx,a[1]+dy,a[2]), (b[0]+dx,b[1]+dy,b[2]), (c[0]+dx,c[1]+dy,c[2]))
            for n, a, b, c in tris]


def write_stl(path, objects):
    """멀티오브젝트 ASCII STL. objects: list of (name, tris)."""
    with open(path, "w") as f:
        for name, tris in objects:
            f.write(f"solid {name}\n")
            for n, v0, v1, v2 in tris:
                f.write(f"  facet normal {n[0]} {n[1]} {n[2]}\n")
                f.write("    outer loop\n")
                for v in (v0, v1, v2):
                    f.write(f"      vertex {v[0]} {v[1]} {v[2]}\n")
                f.write("    endloop\n")
                f.write("  endfacet\n")
            f.write(f"endsolid {name}\n")


def write_3mf(path, objects):
    """Bambu Studio 네이티브 3MF. 각 부품을 개별 지오메트리로.
    trimesh 미설치 시 조용히 건너뜀(STL 은 항상 생성됨)."""
    try:
        import trimesh, numpy as np
    except Exception:
        return False
    scene = trimesh.Scene()
    for name, tris in objects:
        verts, faces, vmap = [], [], {}
        for n, *vs in tris:
            idx = []
            for v in vs:
                if v not in vmap:
                    vmap[v] = len(verts); verts.append(v)
                idx.append(vmap[v])
            faces.append(idx)
        m = trimesh.Trimesh(vertices=np.array(verts), faces=np.array(faces),
                            process=False)
        scene.add_geometry(m, node_name=name, geom_name=name)
    scene.export(path)
    return True


def pack(part_tris, layout):
    objs = []
    for i, (name, dx, dy) in enumerate(layout):
        objs.append((f"{name}_{i}", offset(part_tris[name], dx, dy)))
    return objs


def part_size(part_tris, name):
    x0, x1, y0, y1 = bounds(part_tris[name])
    return x1 - x0, y1 - y0, x0, y0


def row_layout(part_tris, name, count, y_start=MARGIN, x_start=MARGIN, per_col=None):
    w, h, ox, oy = part_size(part_tris, name)
    if per_col is None:
        per_col = count
    layout = []
    for i in range(count):
        col = i // per_col; row = i % per_col
        dx = x_start + col * (w + GAP) - ox
        dy = y_start + row * (h + GAP) - oy
        layout.append((name, dx, dy))
    return layout, w, h


def build_plates(parts):
    """plate 이름 -> (재료, objects) 반환."""
    plates = {}

    # ===== PETG (하중 경로) =====
    # Plate: rail_pad x4 (200x50) — 세로 4줄
    lay, _, _ = row_layout(parts, "rail_pad", 4, per_col=4)
    plates["plate_pads"] = ("petg", pack(parts, lay))

    # Plate: rail_adapter x4 (80x80) + wheel_cradle x4 (110x60)
    objs = []
    cl, cw, _ = row_layout(parts, "wheel_cradle", 4, x_start=MARGIN, per_col=4)
    objs += cl
    al, _, _ = row_layout(parts, "rail_adapter", 4,
                          x_start=MARGIN + cw + GAP, per_col=2)
    objs += al
    plates["plate_adapter_cradle"] = ("petg", pack(parts, objs))

    # ===== PLA (비하중) =====
    # Plate: wheel_chock x8 (1열) + ramp seg0/1/2 x2 each
    chl, chw, _ = row_layout(parts, "wheel_chock", 8, x_start=MARGIN, per_col=8)
    objs = chl[:]
    ramp_x = MARGIN + (chw + GAP) + GAP
    ry = MARGIN
    for seg in ("rail_ramp_seg0", "rail_ramp_seg1", "rail_ramp_seg2"):
        w, h, ox, oy = part_size(parts, seg)
        for c in range(2):
            objs.append((seg, ramp_x + c*(w+GAP) - ox, ry - oy))
        ry += h + GAP
    plates["plate_chock_ramp"] = ("pla", pack(parts, objs))

    # Plate: 전자부 인클로저 — MCU(base+lid) + HX711(base x4 + lid x4)
    if "mcu_box_base" in parts and "mcu_box_lid" in parts:
        objs = []
        bw, bh, box, boy = part_size(parts, "mcu_box_base")
        objs.append(("mcu_box_base", MARGIN - box, MARGIN - boy))
        lw, lh, lox, loy = part_size(parts, "mcu_box_lid")
        objs.append(("mcu_box_lid", MARGIN + bw + GAP - lox, MARGIN - loy))
        if "hx711_box_base" in parts and "hx711_box_lid" in parts:
            hy = MARGIN + max(bh, lh) + GAP
            hbw, hbh, hbox, hboy = part_size(parts, "hx711_box_base")
            hlw, hlh, hlox, hloy = part_size(parts, "hx711_box_lid")
            x = MARGIN
            for _ in range(4):
                objs.append(("hx711_box_base", x - hbox, hy - hboy)); x += hbw + GAP
            hy2 = hy + max(hbh, hlh) + GAP
            x = MARGIN
            for _ in range(4):
                objs.append(("hx711_box_lid", x - hlox, hy2 - hloy)); x += hlw + GAP
        plates["plate_enclosures"] = ("pla", pack(parts, objs))

    return plates


def main():
    parts = {}
    for p in glob.glob(os.path.join(STL_DIR, "*.stl")):
        name = os.path.splitext(os.path.basename(p))[0]
        parts[name] = read_tris(p)
    if not parts:
        print("no STL found", file=sys.stderr); sys.exit(1)

    for mat in ("petg", "pla"):
        os.makedirs(os.path.join(OUT_DIR, mat), exist_ok=True)

    plates = build_plates(parts)
    have_3mf = True
    for pname, (mat, objs) in plates.items():
        # 재료 일관성 검증: plate 안의 모든 부품이 같은 재료인지
        base_names = set(n.rsplit("_", 1)[0] for n, _ in objs)
        mats = set(MATERIAL.get(b, "?") for b in base_names)
        assert mats == {mat}, f"{pname}: 재료 혼합 {mats} (기대 {mat})"

        # bbox / fit
        allx = [v[0] for _, tris in objs for _, *vs in tris for v in vs]
        ally = [v[1] for _, tris in objs for _, *vs in tris for v in vs]
        bx, by = max(allx)-min(allx), max(ally)-min(ally)
        fit = ("OK" if max(allx) <= BED_X and max(ally) <= BED_Y
               and min(allx) >= 0 and min(ally) >= 0 else "OVERFLOW!")

        stl_path = os.path.join(OUT_DIR, mat, pname + ".stl")
        tmf_path = os.path.join(OUT_DIR, mat, pname + ".3mf")
        write_stl(stl_path, objs)
        ok3 = write_3mf(tmf_path, objs)
        have_3mf = have_3mf and ok3
        print(f"[{mat.upper():4s}] {pname:22s} {len(objs):2d} objs  "
              f"{bx:6.1f}x{by:6.1f}mm  {fit}  "
              f"{'STL+3MF' if ok3 else 'STL only'}")

    if not have_3mf:
        print("\n주의: trimesh 미설치로 일부 3MF 생략됨. `pip install trimesh networkx`")


if __name__ == "__main__":
    main()
