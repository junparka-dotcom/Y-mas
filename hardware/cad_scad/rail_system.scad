// ===== 레일 플랫폼 전체 조립도 (방식 2) =====
// 침대를 들지 않고 굴려서 진입. 레일 2개 x 로드셀 2개 = 총 4채널.
//
// [주의] 이 파일은 mcu_enclosure.scad / hx711_enclosure.scad 를 use 합니다.
//        두 파일은 복구하지 못했으므로, 아래 두 줄을 주석 처리하고
//        mcu_body() / hx711_body() 호출부를 지우면 나머지는 렌더됩니다.
include <rail_spec.scad>
use <rail_loadcell_mount.scad>
use <wheel_cradle.scad>
use <rail_ramp_segment.scad>
use <mcu_enclosure.scad>      // *** 미복구 ***
use <hx711_enclosure.scad>    // *** 미복구 ***
$fn=24;

show_bed = true;

// 침대
bed_l=2000; bed_w=900;
rail_gap = 560;                 // 좌/우 레일 중심 간격 (캐스터 좌우 간격)
cx1=350; cx2=cx1+CASTER_PITCH;  // 캐스터 x 위치 (레일 좌표계)

WHEEL_CZ = DECK_Z + 8 - CRADLE_depth + wheel_d/2;
frame_z  = WHEEL_CZ + wheel_d/2 + 35;

// --- 레일 1개 (원점: 레일 좌측 끝, 폭 중심 y=0) ---
module one_rail(){
    // 알루미늄 프로파일 (30x60) - 구매품
    color("LightSteelBlue")
        translate([0,-RAIL_w/2, DECK_Z-RAIL_h]) cube([RAIL_len, RAIL_w, RAIL_h]);

    // 양단 로드셀 지지부 2세트
    for(px=[cx1, cx2]){
        translate([px-125, -25, 0]) color("SteelBlue") pad();
        // 로드셀
        color("Silver") translate([px-100, -L_wid/2, PAD_thk+SPACER_h])
            cube([L_len, L_wid, L_thk]);
        // 브래킷
        color("Gold") translate([px-71, -RAIL_w/2, PAD_thk+SPACER_h+L_thk])
            cube([90, RAIL_w, BRACKET_t]);
        // HX711 박스
        color("SeaGreen") translate([px+45, -12, PAD_thk]) hx711_body();
    }
    // 바퀴 크래들
    for(px=[cx1,cx2])
        color("Orange") translate([px-55, -RAIL_w/2, DECK_Z]) cradle();

    // 진입 램프 3분할 (레일 좌측 끝 앞)
    for(i=[0:2])
        color("Khaki") translate([-RAMP_len + i*(RAMP_len/3), -RAIL_w/2, 0]) segment(i);
}

module wheel(px,py){
    color("DimGray") translate([px,py,WHEEL_CZ]) rotate([0,90,0])
        cylinder(d=wheel_d,h=wheel_thk,center=true,$fn=40);
    color("Gray") translate([px-16,py-15,WHEEL_CZ+wheel_d/2]) cube([32,30,35]);
}

module bed(){
    for(ry=[-rail_gap/2-30, rail_gap/2-30])
        color("Gainsboro",0.5) translate([cx1-200,ry,frame_z]) cube([bed_l,60,70]);
    for(rx=[cx1-120, cx2+60])
        color("Gainsboro",0.5) translate([rx,-rail_gap/2-30,frame_z]) cube([60,rail_gap+60,70]);
    color("AliceBlue",0.3) translate([cx1-180,-bed_w/2,frame_z+70]) cube([bed_l-40,bed_w,110]);
}

module full(){
    for(sy=[-rail_gap/2, rail_gap/2]) translate([0,sy,0]) one_rail();
    for(px=[cx1,cx2]) for(py=[-rail_gap/2, rail_gap/2]) wheel(px,py);
    color("MediumPurple") translate([(cx1+cx2)/2-40, -30, 0]) mcu_body();
    if(show_bed) bed();
    echo(str(">>> 데크 ",DECK_Z,"mm / 램프 ",RAMP_len,"mm / 레일 ",RAIL_len,"mm"));
}
full();
