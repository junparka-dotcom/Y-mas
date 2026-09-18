// ===== 바퀴 크래들 (레일 위 캐스터 안착부) =====
// 얕은 V홈이라 앞바퀴가 굴러 지나갈 수 있고, 뒷바퀴는 여기 안착.
// 안착 후 wheel_chock(고임목)을 끼워 고정.
include <rail_spec.scad>
$fn=48;

cr_l = 110;             // 레일 길이방향
cr_w = RAIL_w;
cr_base = 8;            // 레일에 얹히는 바닥 두께
lip_h = 16;             // 바퀴 이탈 방지 측벽

slot_w = wheel_thk + 3;

module cradle(){
    difference(){
        union(){
            cube([cr_l, cr_w, cr_base]);
            // 좌우 측벽(리브)
            for(y=[0, cr_w-(cr_w-slot_w)/2])
                translate([0, y==0?0:cr_w-(cr_w-slot_w)/2, cr_base])
                    cube([cr_l, (cr_w-slot_w)/2, lip_h]);
        }
        // 얕은 V 크래들
        translate([cr_l/2, cr_w/2, cr_base])
            rotate([90,0,0])
              translate([0,0,-cr_w])
                linear_extrude(cr_w*2)
                  polygon([[-38,0.01],[38,0.01],[0,-CRADLE_depth]]);
        // 레일 체결 M5 x4
        for(dx=[18, cr_l-18]) for(dy=[12, cr_w-12])
            translate([dx,dy,-1]) cylinder(d=5.3,h=cr_base+2);
    }
}
cradle();
