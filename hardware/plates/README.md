# H2D 배치 출력용 Plate STL

밤부랩 H2D 베드(**350×320mm**)에 부품을 묶어 배치한 plate STL입니다.
**전 부품 PETG 출력** 전제. 개별 부품 STL(`../stl/`)을 `make_plates.py`로
합친 결과물이며, Bambu Studio에서 열면 각 부품이 **개별 오브젝트로 인식**되어
필요 시 재배치·개별 삭제가 가능합니다.

## 구성 (총 26개 부품 → 3판)

| Plate 파일 | 담긴 부품 | 개수 | 풋프린트 |
|---|---|---|---|
| `plate1_pads.stl` | rail_pad | 4 | 200×218mm |
| `plate2_bracket_cradle.stl` | rail_bracket ×4 + wheel_cradle ×4 | 8 | 206×258mm |
| `plate3_chock_ramp.stl` | wheel_chock ×8 + rail_ramp seg0/1/2 각 ×2 | 14 | 324×228mm |

모든 판이 베드(350×320) 안에 들어가며, 부품 간 겹침 0, 간격 6mm·가장자리 여백 8mm로 배치했습니다.

## 슬라이서 인필 — 판마다 다르게

전부 PETG지만 부품 성격에 따라 인필을 달리 잡으세요 (Bambu Studio에서 오브젝트별 설정 가능):

| Plate | 인필 | 벽 | 이유 |
|---|---|---|---|
| `plate1_pads` | **60%+** | 5겹 | 하중 경로. 크리프 방지 |
| `plate2_bracket_cradle` | bracket **60%+** / cradle 40% | 5 / 4겹 | bracket은 하중 경로, cradle은 바퀴 하중+마찰 |
| `plate3_chock_ramp` | chock 30% / **ramp 10~15%** | 3겹 | 램프는 하중 경로와 분리된 부품 |

## ⚠️ plate3는 시간이 오래 걸립니다

램프 3종(seg0/1/2 각 2개)은 솔리드 부피가 크고(seg2 하나만 581cm³), 전체 출력량의 대부분을 차지합니다. plate3는 베드엔 들어가지만 **한 번에 뽑으면 매우 오래 걸립니다.** 권장:

- 램프 인필을 **10~15%**로 낮추고 상/하면 3겹 (강도 충분 — 진입 시 순간 하중만 받음)
- 램프는 애초에 정밀도가 필요 없는 15° 경사 형상이라, **합판/각목으로 대체 제작**도 가능
- 시제품 검증 단계면 chock만 먼저 뽑고 램프는 나중에

## 재생성

개별 STL(`../stl/`)이 바뀌면 다시 생성:

```bash
cd hardware
python3 make_plates.py     # plates/*.stl 재생성
```
