# Y-mas Tier 1 — 레일 플랫폼 CAD 파일

2026-08-18 설계분을 대화 기록에서 복원한 것입니다.

## 복원 검증

원본 생성 당시의 OpenSCAD `echo` 출력과 이번 재생성 출력이 일치합니다.

| 항목 | 원본(2026-08-18) | 현재(개선 반영) |
|---|---|---|
| 패드 | 200x50x6 / 단상 5 | + 하면 4점 지지 발 12x2.5 (전체 Z 13.5) |
| 브래킷 | 90x60x6 | **90x60x8** (굽힘강성 보강) |
| 램프 세그먼트 | 108.229mm, 0→29→58→87 | **110.718mm, 0→29.7→59.3→89** + 다웰 결합 |
| 데크 높이 | 87mm | **89mm** (브래킷 6→8 여파) |

> **개선 이력:** 계측 안정성/조립성 개선으로 아래가 바뀌었습니다.
> - 패드: 하면 전면접촉 → **4점 지지 발**(바닥 요철에도 영점 안정)
> - 브래킷: 6 → **8mm**(평판 처짐 보완)
> - 크래들: V홈을 **바퀴 지름 기반 파라메트릭**으로 정리 + 실측 강제 assert
> - 램프: 세그먼트 접합면에 **다웰 핀/구멍**(밀림 방지) → 세그 X길이 +8mm
> - 펌웨어: 빈 상태 **자동 영점 보정(auto-tare)**로 PETG 크리프 드리프트 대응

## 포함 파일

### 소스 (.scad) — 치수 수정은 여기서
```
rail_spec.scad             공용 파라미터  ← 실측값은 여기만 고치면 전체 연동
wheel_cradle.scad          바퀴 크래들
rail_loadcell_mount.scad   패드 + 브래킷 (part="pad" / "bracket")
rail_ramp_segment.scad     램프 분할 (seg=0/1/2)
wheel_chock.scad           고임목
rail_system.scad           전체 조립 뷰 (아래 '미복구' 참고)
```

### 출력용 (.stl)
```
rail_pad.stl          바닥 패드 + 로드셀 고정단 단상   x4
rail_bracket.stl      로드셀 자유단 ↔ 레일 연결 브래킷  x4
wheel_cradle.stl      얕은 V 크래들                    x4
wheel_chock.stl       고임목                           x8
rail_ramp_seg0/1/2    램프 3분할                       각 x2
```

## 미복구 파일

`hx711_enclosure.scad`, `mcu_enclosure.scad` 두 개는 대화 기록에서 소스를
찾지 못했습니다. `rail_system.scad`가 이 둘을 `use` 하므로 조립 뷰는 그대로
렌더되지 않습니다. 파일 상단 주석에 우회 방법을 적어두었습니다.

알려진 치수: HX711 박스 34x24x16mm, MCU 박스 80x60x30mm,
벽 두께 2~2.5mm, 모서리 필렛 R1.5~2, 볼트+육각너트 트랩 관통 체결.

## STL 재생성

```bash
openscad -D 'part="pad"'     -o rail_pad.stl      rail_loadcell_mount.scad
openscad -D 'part="bracket"' -o rail_bracket.stl  rail_loadcell_mount.scad
openscad                     -o wheel_cradle.stl  wheel_cradle.scad
openscad                     -o wheel_chock.stl   wheel_chock.scad
for i in 0 1 2; do openscad -D "seg=$i" -o rail_ramp_seg$i.stl rail_ramp_segment.scad; done
```

## 실측 후 수정할 파라미터 (rail_spec.scad)

```
wheel_d=60; wheel_thk=22;   // 바퀴 지름/두께 — 캘리퍼스 실측 필수
CASTER_PITCH = 1700;        // 앞뒤 캐스터 간격 — COG_X 계산에 직결
CRADLE_depth = 9;           // V홈 깊이 — 실물 굴림 테스트 필요
```
`rail_system.scad`의 `rail_gap = 560`(좌우 캐스터 간격)도 COG_Y에 직결됩니다.
