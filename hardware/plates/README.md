# H2D 배치 출력용 Plate (재료별 분리)

밤부랩 H2D 베드(**350×320mm**)에 부품을 묶어 배치한 plate입니다.
**재료별로 폴더가 나뉘어** 있어 PETG는 PETG끼리, PLA는 PLA끼리 출력합니다.
각 plate는 **STL + 3MF** 둘 다 제공합니다.

- **3MF 권장** — Bambu Studio 네이티브 포맷. 오브젝트 분리·배치·재료 지정이 STL보다 매끄러움.
- STL도 동일 내용(멀티오브젝트). 다른 슬라이서 호환용.
- 어느 쪽이든 열면 각 부품이 **개별 오브젝트로 인식**되어 재배치·개별 삭제 가능.

## 폴더 구조

```
plates/
├── petg/   ← 하중 경로 부품 (PETG로 출력)
│   ├── plate_pads.stl / .3mf
│   └── plate_adapter_cradle.stl / .3mf
└── pla/    ← 비하중 부품 (PLA로 출력)
    ├── plate_chock_ramp.stl / .3mf
    └── plate_enclosures.stl / .3mf
```

## 구성 (총 28개 부품 → 4판)

### 🔵 PETG — 하중 경로 (`petg/`)

| Plate | 담긴 부품 | 개수 | 풋프린트 |
|---|---|---|---|
| `plate_pads` | rail_pad | 4 | 200×218mm |
| `plate_adapter_cradle` | rail_adapter ×4 + wheel_cradle ×4 | 8 | 282×258mm |

### 🟢 PLA — 비하중 (`pla/`)

| Plate | 담긴 부품 | 개수 | 풋프린트 |
|---|---|---|---|
| `plate_chock_ramp` | wheel_chock ×8 + rail_ramp seg0/1/2 각 ×2 | 14 | 307×234mm |
| `plate_enclosures` | mcu_box(base+lid) + hx711_box(base×4+lid×4) | 10 | 166×120mm |

모든 판이 베드(350×320) 안에 들어가며, 부품 간 겹침 0, 간격 6mm·가장자리 여백 8mm로 배치했습니다.
생성기(`make_plates.py`)가 **재료 혼합을 assert로 차단**하므로 판에 다른 재료가 섞이지 않습니다.

## 재료 분류 근거

| 부품 | 재료 | 이유 |
|---|---|---|
| rail_pad, rail_adapter, wheel_cradle | **PETG** | 침대 하중이 상시 통과하는 하중 경로 — PLA 크리프로 영점 드리프트 |
| wheel_chock | PLA | 측면 걸림 역할, 지속 압축 없음 |
| rail_ramp seg0/1/2 | PLA | 하중 경로와 분리된 독립 부품, 진입 시 순간 하중만 |
| mcu_box, hx711_box | PLA | 전자부 보호용, 하중 없음 |

## 슬라이서 인필 — 판/오브젝트별로 다르게

| Plate | 재료 | 인필 | 벽 | 이유 |
|---|---|---|---|---|
| `plate_pads` | PETG | **60%+** | 5겹 | 하중 경로. 크리프 방지 |
| `plate_adapter_cradle` | PETG | adapter **60%+** / cradle 40% | 5 / 4겹 | 어댑터는 하중 경로, cradle은 바퀴 하중+마찰 |
| `plate_chock_ramp` | PLA | chock 30% / **ramp 10~15%** | 3겹 | 램프는 하중 분리 부품 |
| `plate_enclosures` | PLA | 20~30% | 3겹 | 전자부 보호용 |

## ⚠️ plate_chock_ramp 는 시간이 오래 걸립니다

램프 3종(seg0/1/2 각 2개)은 솔리드 부피가 커서(seg2 하나만 ~580cm³) 전체 출력량의 대부분을 차지합니다. 권장:

- 램프 인필을 **10~15%**로 낮추고 상/하면 3겹 (강도 충분 — 진입 시 순간 하중만)
- 램프는 정밀도가 필요 없는 15° 경사 형상이라 **합판/각목으로 대체 제작**도 가능
- 시제품 검증 단계면 chock만 먼저 뽑고 램프는 나중에

## 재생성

개별 STL(`../stl/`)이 바뀌면 다시 생성:

```bash
cd hardware
python3 make_plates.py     # plates/{petg,pla}/ 에 STL + 3MF 재생성
```

> 3MF 생성에는 `trimesh` + `networkx` 가 필요합니다(`pip install trimesh networkx`).
> 없으면 STL만 생성되고 안내 메시지가 출력됩니다.
