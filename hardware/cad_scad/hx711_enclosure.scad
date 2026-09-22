// ===== HX711 인클로저 (바퀴 유닛당 1개) =====
// PDF 부품목록 확정: 외형 34 x 24 x 16mm. 기판 SZH-SSBH-054 25 x 15mm.
// 로드셀 신호 in <-> ESP32(SCK 공유) out. 하중경로 아님 -> PLA 가능.
// 체결: 볼트 + 육각너트 트랩 관통.
//
// part="base" : 박스 본체(바닥 + 4벽 + 기판 지지 립)
// part="lid"  : 뚜껑
include <rail_spec.scad>
part = "base";
$fn = 32;

// --- 외형 (PDF 확정) ---
BOX_L = 34; BOX_W = 24; BOX_H = 16;
WALL  = 2;                   // 소형이라 2mm
FILLET = 1.5;

// --- HX711 기판 (확정 25x15) ---
PCB_L = 25; PCB_W = 15;
LEDGE_H = 3;                 // 기판 안착 립 높이
LEDGE_W = 1.5;               // 립 폭

// --- 뚜껑 체결 (소형 M2) ---
LID_BOLT_D = 2.4;            // M2 관통
LID_NUT_AF = 4.2;           // M2 육각너트 대변 + 여유
LID_NUT_H  = 1.8;

module rrect(l,w,h,r){ hull() for(x=[r,l-r]) for(y=[r,w-r]) translate([x,y,0]) cylinder(r=r,h=h); }

// 케이블 인출구: 짧은 변 양쪽
module cable_slots(){
    // 좌: 로드셀 신호 (Ex/Sig 4선)
    translate([-2, BOX_W/2-5, WALL+2]) cube([WALL+4, 10, 6]);
    // 우: ESP32 (VCC/GND/DT/SCK)
    translate([BOX_L-WALL-2, BOX_W/2-5, WALL+2]) cube([WALL+4, 10, 6]);
}

// 대각 두 모서리 체결(소형이라 2점이면 충분). 기둥(d=5,r=2.5)이 벽 안쪽면과
// 정확히 일치하지 않도록 WALL+2 에 배치 -> 벽 속으로 0.5 겹침(union 안전).
bp = WALL + 2;
bolt_pts = [[bp, bp], [BOX_L-bp, BOX_W-bp]];

module base(){
    echo(str("HX711 박스 base ",BOX_L,"x",BOX_W,"x",BOX_H," 벽",WALL,
             " / PCB ",PCB_L,"x",PCB_W));
    difference(){
        union(){
            // 바닥 + 4벽 (윗면 개방), 내부 캐비티는 단순 큐브
            difference(){
                rrect(BOX_L, BOX_W, BOX_H, FILLET);
                translate([WALL, WALL, WALL])
                    cube([BOX_L-2*WALL, BOX_W-2*WALL, BOX_H]);
            }
            // 기판 안착 립 (좌우 긴 변 안쪽). 벽과 0.5 겹치게 해 면 일치(non-manifold) 회피.
            for(sy=[WALL-0.5, BOX_W-WALL-LEDGE_W+0.5])
                translate([WALL-0.5, sy, WALL]) cube([BOX_L-2*WALL+1, LEDGE_W, LEDGE_H]);
            // 뚜껑 체결 기둥 (바닥부터, 벽과 겹침)
            for(p=bolt_pts)
                translate([p[0],p[1],0]) cylinder(d=5, h=BOX_H-2);
        }
        // 뚜껑 볼트 관통 (바닥~기둥 상단 관통)
        for(p=bolt_pts)
            translate([p[0],p[1],-1]) cylinder(d=LID_BOLT_D, h=BOX_H+2);
        cable_slots();
    }
}

module lid(){
    echo(str("HX711 박스 lid ",BOX_L,"x",BOX_W));
    difference(){
        rrect(BOX_L, BOX_W, WALL, FILLET);
        for(p=bolt_pts){
            translate([p[0],p[1],-1]) cylinder(d=LID_BOLT_D, h=WALL+2);
            translate([p[0],p[1],WALL-LID_NUT_H])
                cylinder(d=LID_NUT_AF/cos(30), h=LID_NUT_H+0.1, $fn=6);
        }
    }
}

// rail_system.scad 조립 뷰 호환 별칭 (박스 본체 형상)
module hx711_body(){ base(); }

if(part=="base") base(); else lid();
