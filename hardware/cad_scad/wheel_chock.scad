// ===== 바퀴 고임목 (안착 후 삽입해 고정) =====
// 크래들에 바퀴가 들어온 뒤 앞뒤로 하나씩 끼워 이탈 방지.
include <rail_spec.scad>
$fn=32;
ch_w = wheel_thk + 2;
ch_l = 45; ch_h = 22;
difference(){
    hull(){
        translate([0,0,0]) cube([ch_l, ch_w, 1]);
        translate([ch_l-14,0,0]) cube([14, ch_w, ch_h]);
    }
    // 바퀴 곡률 릴리프
    translate([ch_l+wheel_d/2-6, -1, ch_h*0.55]) rotate([-90,0,0])
        cylinder(d=wheel_d, h=ch_w+2);
    // 손잡이 홀
    translate([12, ch_w/2, -1]) cylinder(d=8, h=ch_h+2);
}
