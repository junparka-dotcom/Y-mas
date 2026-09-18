// ===== 진입 램프 (분할 출력용) =====
// 전체 램프 길이 약 325mm를 3분할해서 출력 후 이어붙입니다.
// seg = 0(바닥측) / 1(중간) / 2(레일측)
include <rail_spec.scad>
seg = 0;
NSEG = 3;
$fn=32;

seg_len = RAMP_len/NSEG;
lip = 12;                       // 바퀴 이탈 방지 측벽

function h_at(x) = x/RAMP_len*DECK_Z;

module segment(i){
    x0=i*seg_len; x1=(i+1)*seg_len;
    h0=h_at(x0); h1=h_at(x1);
    echo(str("세그먼트 ",i,": 길이 ",seg_len," / 높이 ",h0," -> ",h1));
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
    }
}
segment(seg);
