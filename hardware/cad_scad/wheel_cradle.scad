// ===== 바퀴 크래들 (레일 위 캐스터 안착부) =====
// 얕은 V홈이라 앞바퀴가 굴러 지나갈 수 있고, 뒷바퀴는 여기 안착.
// 안착 후 wheel_chock(고임목)을 끼워 고정.
include <rail_spec.scad>
$fn=48;

cr_l = 110;             // 레일 길이방향
cr_w = RAIL_w;
cr_base = 8;            // 레일에 얹히는 바닥 두께
lip_h = 16;             // 바퀴 이탈 방지 측벽

slot_w  = wheel_thk + 3;              // 바퀴 두께 + 여유 (바퀴가 들어갈 홈 폭)
wall_w  = (cr_w - slot_w) / 2;        // 좌우 측벽 각각의 폭

// V홈: 바퀴 지름 기반으로 개구 폭 산출.
// 얕은 V(깊이 CRADLE_depth)에서 바퀴 원과의 접점이 만드는 현(chord) 폭.
// 바퀴 반지름 r, 홈 깊이 d 일 때 개구 반폭 = sqrt(r^2 - (r-d)^2).
V_r     = wheel_d / 2;
V_open  = 2 * sqrt(max(V_r*V_r - (V_r - CRADLE_depth)*(V_r - CRADLE_depth), 0.01));

// --- 실측 강제 (rail_spec 기본값=가정치이면 실물과 안 맞음) ---
// 주: V홈 개구폭은 슬롯폭보다 넓은 게 정상이다. 측벽 상단 립은 남고
//     그 아래를 바퀴 곡률에 맞춰 넓게 파내는 구조이기 때문. 다만 크래들
//     전체 폭(cr_w)을 넘으면 측벽이 통째로 사라지므로 그건 막는다.
assert(wheel_thk > 0, "wheel_thk 실측값을 rail_spec.scad 에 설정하세요");
assert(wheel_d   > 0, "wheel_d 실측값을 rail_spec.scad 에 설정하세요");
assert(V_open <= cr_w,
       "V홈 개구폭이 크래들 전체폭을 넘음 — CRADLE_depth 를 줄이거나 바퀴 지름 확인");

module cradle(){
    echo(str("크래들 ",cr_l,"x",cr_w," / 슬롯 ",slot_w," / 측벽 ",wall_w,
             " / V홈폭 ",V_open," 깊이 ",CRADLE_depth," (바퀴 d",wheel_d," t",wheel_thk,")"));
    difference(){
        union(){
            cube([cr_l, cr_w, cr_base]);
            // 좌우 측벽(리브) — y=0 쪽과 y=cr_w-wall_w 쪽, 각 폭 wall_w
            for(yoff = [0, cr_w - wall_w])
                translate([0, yoff, cr_base])
                    cube([cr_l, wall_w, lip_h]);
        }
        // 얕은 V 크래들 (개구 폭 = V_open, 깊이 = CRADLE_depth)
        translate([cr_l/2, cr_w/2, cr_base])
            rotate([90,0,0])
              translate([0,0,-cr_w])
                linear_extrude(cr_w*2)
                  polygon([[-V_open/2, 0.01],
                           [ V_open/2, 0.01],
                           [ 0, -CRADLE_depth]]);
        // 레일 체결 M5 x4
        for(dx=[18, cr_l-18]) for(dy=[12, cr_w-12])
            translate([dx,dy,-1]) cylinder(d=5.3,h=cr_base+2);
    }
}
cradle();
