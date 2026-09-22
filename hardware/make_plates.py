#!/usr/bin/env python3
"""
밤부랩 H2D 베드(350x320mm)에 맞춰 부품을 묶은 plate STL 생성기.
전부 PETG로 출력하는 전제. 개별 STL을 읽어 XY 평행이동 후 하나의
멀티오브젝트 ASCII STL로 합친다. Bambu Studio에서 열면 각 부품이
개별 오브젝트로 인식되어 재배치도 가능하다.

사용:
    python3 make_plates.py            # stl/ 의 개별 STL을 읽어 plates/ 에 생성
"""
import glob, os, re, sys

BED_X, BED_Y = 350, 320          # H2D 베드
MARGIN = 8                       # 베드 가장자리 여백
GAP = 6                          # 부품 간 간격

HERE = os.path.dirname(os.path.abspath(__file__))
STL_DIR = os.path.join(HERE, "stl")
OUT_DIR = os.path.join(HERE, "plates")


def read_tris(path):
    """ASCII STL -> (list of (nx,ny,nz, v0,v1,v2)), 각 v는 (x,y,z)."""
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
    out = []
    for n, v0, v1, v2 in tris:
        out.append((n,
                    (v0[0]+dx, v0[1]+dy, v0[2]),
                    (v1[0]+dx, v1[1]+dy, v1[2]),
                    (v2[0]+dx, v2[1]+dy, v2[2])))
    return out


def write_plate(path, objects):
    """objects: list of (name, tris). 각 부품을 solid...endsolid 로 분리."""
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


def pack(part_tris, layout):
    """
    layout: list of (name, col, row) — 격자 슬롯 좌표(정수) 지정 방식은
    쓰지 않고, 여기서는 명시적 (dx,dy) 리스트를 받는다.
    """
    objs = []
    for i, (name, dx, dy) in enumerate(layout):
        objs.append((f"{name}_{i}", offset(part_tris[name], dx, dy)))
    return objs


def part_size(part_tris, name):
    x0, x1, y0, y1 = bounds(part_tris[name])
    return x1 - x0, y1 - y0, x0, y0


def row_layout(part_tris, name, count, y_start=MARGIN, x_start=MARGIN, per_col=None):
    """
    부품 count개를 격자로 배치한 (name,dx,dy) 리스트 반환.
    부품 원점을 (x_start,y_start) 기준으로 정렬(로컬 최소좌표 보정).
    per_col: 한 열(세로)에 몇 개. None이면 한 열에 전부.
    """
    w, h, ox, oy = part_size(part_tris, name)
    if per_col is None:
        per_col = count
    layout = []
    for i in range(count):
        col = i // per_col
        row = i % per_col
        dx = x_start + col * (w + GAP) - ox
        dy = y_start + row * (h + GAP) - oy
        layout.append((name, dx, dy))
    return layout, w, h


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    parts = {}
    for p in glob.glob(os.path.join(STL_DIR, "*.stl")):
        name = os.path.splitext(os.path.basename(p))[0]
        parts[name] = read_tris(p)
    if not parts:
        print("no STL found", file=sys.stderr); sys.exit(1)

    plates = {}

    # ---- Plate 1: rail_pad x4 (200x50) — 세로로 4줄 ----
    lay, w, h = row_layout(parts, "rail_pad", 4, per_col=4)
    plates["plate1_pads"] = pack(parts, lay)

    # ---- Plate 2: rail_adapter x4 (80x80) + wheel_cradle x4 (110x60) ----
    # 좌측: cradle 4개(세로 1열). 우측: adapter 4개(2열x2행, 80x80이라 세로로 못 쌓음).
    objs2 = []
    cl, cw, ch = row_layout(parts, "wheel_cradle", 4, x_start=MARGIN, per_col=4)
    objs2 += cl
    al, aw, ah = row_layout(parts, "rail_adapter", 4,
                            x_start=MARGIN + cw + GAP, per_col=2)
    objs2 += al
    plates["plate2_adapter_cradle"] = pack(parts, objs2)

    # ---- Plate 3: wheel_chock x8 (42x24) + ramp seg0/1/2 x2 each ----
    # 좌측: chock 8개를 1열x8행(폭 절약). 우측: ramp 3종을 각 행에 2개씩 나란히.
    # 램프는 다웰 핀 때문에 X로 길어져(118.7mm) 2개 나란히 두면 폭이 크므로
    # chock 를 1열로 좁혀 ramp 시작 X 를 앞당긴다.
    chl, chw, chh = row_layout(parts, "wheel_chock", 8, x_start=MARGIN, per_col=8)
    objs3 = chl[:]
    ramp_x = MARGIN + (chw + GAP) + GAP
    ry = MARGIN
    for seg in ("rail_ramp_seg0", "rail_ramp_seg1", "rail_ramp_seg2"):
        w, h, ox, oy = part_size(parts, seg)
        for c in range(2):                       # 같은 세그 2개를 X로 나란히
            dx = ramp_x + c * (w + GAP) - ox
            dy = ry - oy
            objs3.append((seg, dx, dy))
        ry += h + GAP                            # 다음 세그는 아래 행
    plates["plate3_chock_ramp"] = pack(parts, objs3)

    # ---- Plate 4: MCU 박스 base + lid (전자부 인클로저, 각 1개) ----
    # 하중경로 아님(PLA 가능)이라 별도 판. base 80x60x30 + lid 80x60x2.5.
    if "mcu_box_base" in parts and "mcu_box_lid" in parts:
        objs4 = []
        bw, bh, box, boy = part_size(parts, "mcu_box_base")
        objs4.append(("mcu_box_base", MARGIN - box, MARGIN - boy))
        lw, lh, lox, loy = part_size(parts, "mcu_box_lid")
        objs4.append(("mcu_box_lid", MARGIN + bw + GAP - lox, MARGIN - loy))
        plates["plate4_mcu_box"] = pack(parts, objs4)

    # ---- 검증 + 출력 ----
    for pname, objs in plates.items():
        # bbox 계산
        allx = [v[0] for _, tris in objs for _, *vs in tris for v in vs]
        ally = [v[1] for _, tris in objs for _, *vs in tris for v in vs]
        bx, by = max(allx) - min(allx), max(ally) - min(ally)
        fit = "OK" if (max(allx) <= BED_X and max(ally) <= BED_Y
                       and min(allx) >= 0 and min(ally) >= 0) else "OVERFLOW!"
        out = os.path.join(OUT_DIR, pname + ".stl")
        write_plate(out, objs)
        print(f"{pname:26s} {len(objs)} objs  footprint {bx:6.1f}x{by:6.1f}mm  "
              f"[{min(allx):.1f},{max(allx):.1f}]x[{min(ally):.1f},{max(ally):.1f}]  {fit}")


if __name__ == "__main__":
    main()
