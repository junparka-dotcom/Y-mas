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

module pad(){
    fx1=lc_x+L_fix_x1; fx2=lc_x+L_fix_x2;
    fy1=pad_w/2-L_pitch_y/2; fy2=pad_w/2+L_pitch_y/2;
    echo(str("패드 ",pad_l,"x",pad_w,"x",PAD_thk," / 단상 ",SPACER_h));
    difference(){
        union(){
            rbox(pad_l,pad_w,PAD_thk,2);
            translate([fx1-15,(pad_w-sp_w)/2,PAD_thk]) rbox(sp_l,sp_w,SPACER_h,1);
        }
        for(px=[fx1,fx2]) for(py=[fy1,fy2]){
            translate([px,py,-1]) cylinder(d=6.6,h=PAD_thk+SPACER_h+2);
            translate([px,py,-1]) cylinder(d=11.5,h=4.5);   // 바닥쪽 카운터보어
        }
    }
}

// ---------- 레일 브래킷 ----------
br_l = 90; br_w = RAIL_w;
module bracket(){
    echo(str("브래킷 ",br_l,"x",br_w,"x",BRACKET_t));
    difference(){
        rbox(br_l,br_w,BRACKET_t,2);
        // 로드셀 자유단 M6 4개 (중앙 19x15) - 상면 카운터보어
        for(dx=[-L_pitch_x/2,L_pitch_x/2]) for(dy=[-L_pitch_y/2,L_pitch_y/2]){
            translate([br_l/2+dx, br_w/2+dy, -1]) cylinder(d=6.6,h=BRACKET_t+2);
        }
        // 레일 T너트 체결 M5 x4 (모서리, 홀 패턴과 간섭 없음)
        for(dx=[14, br_l-14]) for(dy=[12, br_w-12])
            translate([dx,dy,-1]) cylinder(d=5.3,h=BRACKET_t+2);
    }
}
if(part=="pad") pad(); else bracket();
