// ===== 레일-로드셀 마운트 세트 =====
// part="pad"     : 바닥 패드 + 로드셀 고정단 단상 (하면 카운터보어)
// part="bracket" : 로드셀 자유단 상면 <-> 알루미늄 레일 연결 브래킷
include <rail_spec.scad>
part = "pad";
$fn=48;

module rbox(l,w,h,r){ hull() for(x=[r,l-r]) for(y=[r,w-r]) translate([x,y,0]) cylinder(r=r,h=h); }

// ---------- 바닥 패드 ----------
pad_l = 200; pad_w = 50;
lc_x  = 25;                    // 패드 좌측 ~ 로드셀 좌측
sp_l  = 50; sp_w = 41;

// 지지 발 위치: 네 모서리. 볼트 카운터보어 영역(X 139.5~158.5)을 피함.
foot_inset = 16;
foot_pts = [[foot_inset, foot_inset],
            [foot_inset, pad_w-foot_inset],
            [pad_l-foot_inset, foot_inset],
            [pad_l-foot_inset, pad_w-foot_inset]];

module pad(){
    fx1=lc_x+L_fix_x1; fx2=lc_x+L_fix_x2;
    fy1=pad_w/2-L_pitch_y/2; fy2=pad_w/2+L_pitch_y/2;
    echo(str("패드 ",pad_l,"x",pad_w,"x",PAD_thk," / 단상 ",SPACER_h,
             " / 지지발 ",FOOT_d,"x",FOOT_h,"x4"));
    // 지지 발 높이만큼 본체를 위로 올려, 바닥엔 4점만 닿게 한다.
    translate([0,0,FOOT_h])
    difference(){
        union(){
            rbox(pad_l,pad_w,PAD_thk,2);
            translate([fx1-15,(pad_w-sp_w)/2,PAD_thk]) rbox(sp_l,sp_w,SPACER_h,1);
            // 하면 4점 지지 발 (아래로 돌출)
            for(p=foot_pts)
                translate([p[0],p[1],-FOOT_h]) cylinder(d=FOOT_d,h=FOOT_h);
        }
        // 로드셀 고정단 M6 관통 + 바닥쪽 카운터보어
        // (관통홀은 발 두께까지 고려해 넉넉히 -FOOT_h 아래부터)
        for(px=[fx1,fx2]) for(py=[fy1,fy2]){
            translate([px,py,-FOOT_h-1]) cylinder(d=6.6,h=PAD_thk+SPACER_h+FOOT_h+2);
            translate([px,py,-1]) cylinder(d=11.5,h=4.5);   // 바닥쪽 카운터보어
        }
    }
}

// ---------- 어댑터 플레이트 (구 bracket) ----------
// 로드셀 자유단 상면 <-> 알루미늄 레일 연결. 80x80x12.
// 배치(플레이트 로컬 좌표, 원점=좌하단):
//   - 자유단 체결 4홀: 플레이트 한쪽 끝(x=ADP_end_x 근처)에 19x15 피치로.
//     여기가 로드셀 자유단 위에 얹혀 M6 로 물린다.
//   - 레일 체결 4홀: 자유단 홀에서 ADP_OFFSET 만큼 떨어진 위치(슬롯 반대쪽).
//     -> 레일/바퀴 슬롯이 자유단 볼트 자리를 관통하지 않도록 수평 이격.
//   - 자유단 체결부를 제외한 하면은 로드셀 몸통과 떠 있어야 함(외팔보 단락 방지).
//     조립 배치로 보장되나, 안전하게 자유단 반대쪽 하면에 릴리프 포켓을 판다.
br_l = ADP_l; br_w = ADP_w;

// 자유단 홀 그룹 중심 x (플레이트 끝에서 안쪽으로 살짝 들인 위치)
adp_free_cx = 20;
// 레일 홀 그룹 중심 x = 자유단에서 오프셋만큼 이격
adp_rail_cx = adp_free_cx + ADP_OFFSET;

module bracket(){
    echo(str("어댑터 ",br_l,"x",br_w,"x",ADP_t,
             " / 자유단홀 cx=",adp_free_cx," 레일홀 cx=",adp_rail_cx,
             " 오프셋=",ADP_OFFSET," (임의값, 실측확정 요)"));
    difference(){
        rbox(br_l,br_w,ADP_t,2);
        // (1) 로드셀 자유단 M6 4개 (19x15 피치) - 한쪽 끝
        for(dx=[-L_pitch_x/2,L_pitch_x/2]) for(dy=[-L_pitch_y/2,L_pitch_y/2])
            translate([adp_free_cx+dx, br_w/2+dy, -1])
                cylinder(d=6.6, h=ADP_t+2);
        // (2) 레일 T너트 체결 M5 4개 - 오프셋된 위치(슬롯 반대쪽)
        for(dx=[-L_pitch_x/2, L_pitch_x/2]) for(dy=[-18, 18])
            translate([adp_rail_cx+dx, br_w/2+dy, -1])
                cylinder(d=5.3, h=ADP_t+2);
        // 주: 자유단 체결부 외 하면은 로드셀 몸통과 떠 있어야 함(외팔보 단락 방지).
        //     릴리프 포켓은 로드셀↔어댑터 상대배치(rail_system, 현재 미복구)와
        //     실측이 확정된 뒤 추가할 것. 지금은 강성 유지를 위해 생략.
    }
}
if(part=="pad") pad(); else bracket();
