# Y-mas Tier 1 — 레일 플랫폼 CAD 파일

2026-08-18 설계분을 대화 기록에서 복원한 것입니다.

## 복원 검증

원본 생성 당시의 OpenSCAD `echo` 출력과 이번 재생성 출력이 일치합니다.

| 항목 | 원본(2026-08-18) | 현재(개선 반영) |
|---|---|---|
| 패드 | 200x50x6 / 단상 5 | + 하면 4점 지지 발 12x2.5 (전체 Z 13.5) |
| 브래킷→**어댑터** | 90x60x6 | **80x80x12** (PDF 확정치, 로드셀 40mm 대응 오프셋) |
| 램프 세그먼트 | 108.229mm, 0→29→58→87 | **115.694mm, 0→31→62→93** + 다웰 결합 |
| 데크 높이 | 87mm | **93mm** (어댑터 12mm 도입) |
| MCU 박스 | 미복구 | **복원** 80x60x30 (ESP32 55x26x13 실측 반영) |

> **개선 이력:** 계측 안정성/조립성 개선으로 아래가 바뀌었습니다.
> - 패드: 하면 전면접촉 → **4점 지지 발**(바닥 요철에도 영점 안정)
> - 브래킷 → **어댑터 플레이트(80×80×12)**: 로드셀 40mm 대응 + 자유단 체결홀이
>   바퀴 슬롯을 관통하지 않도록 레일 체결점을 오프셋. **오프셋 30mm는 임의값**이라
>   실측 확정 필요(MEASUREMENTS_TODO C). 두께 12mm로 굽힘강성도 확보.
> - 크래들: V홈을 **바퀴 지름 기반 파라메트릭**으로 정리 + 실측 강제 assert
> - 램프: 세그먼트 접합면에 **다웰 핀/구멍**(밀림 방지). 데크 93mm라 세그 115.7mm
> - MCU 박스: `mcu_enclosure.scad` **신규 복원**(base/lid), ESP32 실측 55×26×13 반영
> - 펌웨어: 빈 상태 **자동 영점 보정(auto-tare)**로 PETG 크리프 드리프트 대응

## 포함 파일

### 소스 (.scad) — 치수 수정은 여기서
```
rail_spec.scad             공용 파라미터  ← 실측값은 여기만 고치면 전체 연동
wheel_cradle.scad          바퀴 크래들
rail_loadcell_mount.scad   패드 + 어댑터 (part="pad" / "bracket")
rail_ramp_segment.scad     램프 분할 (seg=0/1/2)
wheel_chock.scad           고임목
mcu_enclosure.scad         중앙 MCU 박스 (part="base" / "lid")
rail_system.scad           전체 조립 뷰 (아래 '미복구' 참고)
```

### 출력용 (.stl)
```
rail_pad.stl          바닥 패드 + 로드셀 고정단 단상        x4
rail_adapter.stl      로드셀 자유단 ↔ 레일 어댑터(80x80x12) x4
wheel_cradle.stl      얕은 V 크래들                         x4
wheel_chock.stl       고임목                                x8
rail_ramp_seg0/1/2    램프 3분할                            각 x2
mcu_box_base.stl      중앙 MCU 박스 본체                    x1
mcu_box_lid.stl       중앙 MCU 박스 뚜껑                    x1
```

## 미복구 파일

`hx711_enclosure.scad` 는 아직 미복구입니다(HX711 박스 34x24x16mm).
`mcu_enclosure.scad` 는 **복원 완료**(ESP32 55×26×13 실측 반영).
`rail_system.scad` 가 이 둘을 `use` 하므로, hx711 인클로저를 만들기 전까지
전체 조립 뷰는 그대로 렌더되지 않습니다.

공통 규격: 벽 두께 2~2.5mm, 모서리 필렛 R1.5~2, 볼트+육각너트 트랩 관통 체결.

## STL 재생성

```bash
openscad -D 'part="pad"'     -o rail_pad.stl      rail_loadcell_mount.scad
openscad -D 'part="bracket"' -o rail_adapter.stl  rail_loadcell_mount.scad
openscad                     -o wheel_cradle.stl  wheel_cradle.scad
openscad                     -o wheel_chock.stl   wheel_chock.scad
for i in 0 1 2; do openscad -D "seg=$i" -o rail_ramp_seg$i.stl rail_ramp_segment.scad; done
openscad -D 'part="base"'    -o mcu_box_base.stl  mcu_enclosure.scad
openscad -D 'part="lid"'     -o mcu_box_lid.stl   mcu_enclosure.scad
```

또는 배치 출력용 plate 재생성: `python3 make_plates.py`

## 실측 후 수정할 파라미터 (rail_spec.scad)

```
wheel_d=60; wheel_thk=22;   // 바퀴 지름/두께 — 캘리퍼스 실측 필수
CASTER_PITCH = 1700;        // 앞뒤 캐스터 간격 — COG_X 계산에 직결
CRADLE_depth = 9;           // V홈 깊이 — 실물 굴림 테스트 필요
```
`rail_system.scad`의 `rail_gap = 560`(좌우 캐스터 간격)도 COG_Y에 직결됩니다.
