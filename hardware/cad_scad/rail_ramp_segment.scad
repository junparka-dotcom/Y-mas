// ===== 진입 램프 (분할 출력용) =====
// 전체 램프 길이 약 325mm를 3분할해서 출력 후 이어붙입니다.
// seg = 0(바닥측) / 1(중간) / 2(레일측)
include <rail_spec.scad>
seg = 0;
NSEG = 3;
$fn=32;

seg_len = RAMP_len/NSEG;
lip = 12;                       // 바퀴 이탈 방지 측벽

// --- 세그먼트 접합 다웰 ---
// 끝면(X=seg_len)에 핀 돌출, 시작면(X=0)에 구멍. 이웃 세그와 암수 결합해
// 진입 하중에 밀리지 않게 한다. 핀은 바닥 근처(항상 재질 안)에 배치.
DOWEL_D    = 6;      // 핀 지름
DOWEL_L    = 8;      // 핀 돌출 길이(=구멍 깊이)
DOWEL_CLR  = 0.35;   // 구멍 여유(끼워맞춤). 프린팅 공차 흡수
DOWEL_Z    = 8;      // 하면에서 핀 중심 높이
dowel_ys   = [RAIL_w*0.25, RAIL_w*0.75];   // 폭방향 2점

function h_at(x) = x/RAMP_len*DECK_Z;

module segment(i){
    x0=i*seg_len; x1=(i+1)*seg_len;
    h0=h_at(x0); h1=h_at(x1);
    echo(str("세그먼트 ",i,": 길이 ",seg_len," / 높이 ",h0," -> ",h1,
             " / 다웰 핀 d",DOWEL_D," L",DOWEL_L));
    difference(){
        union(){
            // 경사 본체
            polyhedron(
              points=[[0,0,0],[seg_len,0,0],[seg_len,RAIL_w,0],[0,RAIL_w,0],
                      [0,0,h0],[seg_len,0,h1],[seg_len,RAIL_w,h1],[0,RAIL_w,h0]],
              faces=[[0,1,2,3],[4,5,1,0],[5,6,2,1],[6,7,3,2],[7,4,0,3],[7,6,5,4]]);
            // 좌우 측벽
            for(y=[-6, RAIL_w])
              translate([0,y,0])
                polyhedron(
                  points=[[0,0,0],[seg_len,0,0],[seg_len,6,0],[0,6,0],
                          [0,0,h0+lip],[seg_len,0,h1+lip],[seg_len,6,h1+lip],[0,6,h0+lip]],
                  faces=[[0,1,2,3],[4,5,1,0],[5,6,2,1],[6,7,3,2],[7,4,0,3],[7,6,5,4]]);
            // 끝면 다웰 핀 (마지막 세그 seg2 는 바깥이라 있어도 무해)
            if(i < NSEG-1)
              for(y=dowel_ys)
                translate([seg_len, y, DOWEL_Z]) rotate([0,90,0])
                  cylinder(d=DOWEL_D, h=DOWEL_L, $fn=24);
        }
        // 시작면 다웰 구멍 (첫 세그 seg0 은 바깥이라 있어도 무해)
        if(i > 0)
          for(y=dowel_ys)
            translate([-0.1, y, DOWEL_Z]) rotate([0,90,0])
              cylinder(d=DOWEL_D+DOWEL_CLR, h=DOWEL_L+0.2, $fn=24);
    }
}
segment(seg);
