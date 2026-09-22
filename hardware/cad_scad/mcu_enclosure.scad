// ===== 중앙 MCU 인클로저 (ESP32-WROOM-32D) =====
// PDF 부품목록 확정: 외형 80 x 60 x 30mm, 4채널 신호 집결.
// ESP32 실측 외형 55 x 26 x 13mm 반영(기존 55x28 가정값 대체).
// 체결: 볼트 + 육각너트 트랩 관통(자탭 나사 아님). 하중경로 아님 -> PLA 가능.
//
// part="base" : 박스 본체(바닥 + 4벽 + 보드 스탠드오프)
// part="lid"  : 뚜껑
include <rail_spec.scad>
part = "base";
$fn = 40;

// --- 외형 ---
BOX_L = 80; BOX_W = 60; BOX_H = 30;
WALL  = 2.5;                 // 벽 두께
FILLET = 2;                  // 모서리 필렛 R

// --- ESP32 보드 (실측) ---
ESP_L = 55; ESP_W = 26; ESP_H = 13;
STANDOFF_H = 4;              // 보드 하부 부품 공간 확보용 스탠드오프
STANDOFF_D = 6;
BOARD_HOLE_D = 2.6;          // M2.5 self-tap 또는 트랩용 관통

// --- 뚜껑 체결 ---
LID_BOLT_D  = 3.4;           // M3 관통
LID_NUT_AF  = 6.2;           // M3 육각너트 대변(across-flats) + 여유
LID_NUT_H   = 2.6;           // 너트 트랩 깊이

module rrect(l,w,h,r){ hull() for(x=[r,l-r]) for(y=[r,w-r]) translate([x,y,0]) cylinder(r=r,h=h); }

// 케이블 인출구: 짧은 변 양쪽(로드셀 신호 in / UDP·전원 out)
module cable_slots(){
    // 좌: HX711 4채널 신호 다발 (좌벽 확실히 관통)
    translate([-2, BOX_W/2-9, WALL+3]) cube([WALL+4, 18, 8]);
    // 우: USB/전원 + LAN (우벽 확실히 관통)
    translate([BOX_L-WALL-2, BOX_W/2-7, WALL+3]) cube([WALL+4, 14, 8]);
}

// 네 모서리 뚜껑 체결 기둥. 기둥 반경 4가 벽 안쪽면(WALL)과 0.5 겹치도록
// WALL+3.5 에 배치 -> 면 정확 일치(non-manifold) 회피, 벽과 확실히 union.
bp = WALL + 3.5;
bolt_pts = [[bp, bp],[BOX_L-bp, bp],
            [bp, BOX_W-bp],[BOX_L-bp, BOX_W-bp]];

module base(){
    echo(str("MCU 박스 base ",BOX_L,"x",BOX_W,"x",BOX_H," 벽",WALL,
             " / ESP32 ",ESP_L,"x",ESP_W,"x",ESP_H));
    difference(){
        union(){
            // 바닥 + 4벽 (윗면 개방). 내부 캐비티는 단순 큐브로(2-manifold 안전)
            difference(){
                rrect(BOX_L, BOX_W, BOX_H, FILLET);
                translate([WALL, WALL, WALL])
                    cube([BOX_L-2*WALL, BOX_W-2*WALL, BOX_H]);  // 윗면 관통(개방)
            }
            // 보드 스탠드오프 4개 (ESP32 중앙 배치)
            for(sx=[(BOX_L-ESP_L)/2+3, (BOX_L+ESP_L)/2-3])
              for(sy=[(BOX_W-ESP_W)/2+3, (BOX_W+ESP_W)/2-3])
                translate([sx, sy, WALL]) cylinder(d=STANDOFF_D, h=STANDOFF_H);
            // 뚜껑 체결 기둥 — 바닥(z=0)부터 세워 바닥벽과 완전 합침(면 겹침 회피)
            for(p=bolt_pts)
                translate([p[0],p[1],0]) cylinder(d=8, h=BOX_H-3);
        }
        // 스탠드오프 나사 구멍 (바닥까지 관통 — 2-manifold 안전)
        for(sx=[(BOX_L-ESP_L)/2+3, (BOX_L+ESP_L)/2-3])
          for(sy=[(BOX_W-ESP_W)/2+3, (BOX_W+ESP_W)/2-3])
            translate([sx, sy, -1]) cylinder(d=BOARD_HOLE_D, h=WALL+STANDOFF_H+2);
        // 뚜껑 볼트 관통 (기둥 중심) — 바닥부터 기둥 상단까지 확실히 관통
        for(p=bolt_pts)
            translate([p[0],p[1],-1]) cylinder(d=LID_BOLT_D, h=BOX_H+2);
        cable_slots();
    }
}

module lid(){
    echo(str("MCU 박스 lid ",BOX_L,"x",BOX_W));
    difference(){
        rrect(BOX_L, BOX_W, WALL, FILLET);
        // 볼트 관통 + 육각너트 트랩(윗면에서 삽입)
        for(p=bolt_pts){
            translate([p[0],p[1],-1]) cylinder(d=LID_BOLT_D, h=WALL+2);
            translate([p[0],p[1],WALL-LID_NUT_H])
                cylinder(d=LID_NUT_AF/cos(30), h=LID_NUT_H+0.1, $fn=6);
        }
    }
}

// rail_system.scad 조립 뷰 호환 별칭 (박스 본체 형상)
module mcu_body(){ base(); }

if(part=="base") base(); else lid();
