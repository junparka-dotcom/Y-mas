# integration/ — Tier 1 ↔ Tier 2 통합

ESP32(Tier 1, 하중/COG) 가 UDP 로 ALERT 를 보내면 Jetson(Tier 2, 비전) 이
`threading.Event` 로 깨어나 낙상을 확정하는 2-Tier 연동.

## 파일

| 파일 | 역할 |
|---|---|
| `ymas_integrated.py` | Tier 1(하중) + Tier 2(비전) 통합 실행. ESP32 UDP 수신 → 인터럽트 → 비전 스레드 → ST-GCN → MQTT 알람. `--sim --mock-camera` 로 하드웨어 없이 테스트 가능 |
| `ymas_tier1_receiver.py` | Tier 1 수신부. ESP32 하중/COG 수신(Serial/UDP) + 모니터링/로깅 + ALERT 시 Tier 2 기동. 시뮬레이터 내장 |
| `collect_ymas_jetson.sh` | Jetson 자산 수집 스크립트 |

## 하드웨어 없이 Tier 1 검증하기 (시뮬레이터)

ESP32·센서·납땜 없이 `ymas_tier1_receiver.py` 의 내장 시뮬레이터(`--sim`)로
Tier 1 로직 전체(3-Path 감지 / 낙상 vs 정상기상 구분 / ALERT → Tier 2 기동)를
검증할 수 있다.

```bash
# 낙상: ALERT 발생 + Tier 2 기상 + 반응시간 측정
python3 ymas_tier1_receiver.py --sim --scenario fall --quiet

# 정상 기상: ALERT 가 뜨지 않아야 정상 (오탐 없음)
python3 ymas_tier1_receiver.py --sim --scenario exit --quiet

# 그 외 시나리오: edge(가장자리 접근), normal(안정)
python3 ymas_tier1_receiver.py --sim --scenario edge
```

### 검증 완료 (2026-09, 시뮬레이터)
- **낙상(fall):** 낙하 개시 → ALERT 까지 **반응 시간 166ms** (사유
  FASTPATH_WEIGHT_DROP). 인수인계 목표 141~181ms 범위 내. Tier 2 기상 시
  인터럽트 직전 200프레임 하중 이력 확보 확인.
- **정상 기상(exit):** ALERT/인터럽트 미발생 → 침대에서 내려가는 동작을
  낙상으로 오탐하지 않음 (낙상 0.6s 급락 vs 정상기상 2.5s 완만 감소를 dW/dt 로 구분).
- **파싱 정합성:** 펌웨어 출력 포맷(YMAS 16필드 / YMAS_ALERT 8필드)과
  수신부 파서 일치 확인.
- 결론: **Tier 1 소프트웨어 로직은 검증 완료.** 남은 것은 실물(ESP32 +
  HX711·로드셀 납땜) 검증 — 하드웨어 준비 후 펌웨어 업로드 → rate(80Hz 확인)
  → tare → calall → 실측.

## [!] 버전 주의 — ymas_integrated.py 는 구버전(v13) 기준

`ymas_integrated.py` 는 헤더가 **ST-GCN v13 / `ymas_v13.onnx`** 기준으로
작성되어 있다. 현재 확정 모델은 **v21**(배포 th=0.77, physics 10)이므로,
end-to-end 통합 실증 전에 아래를 v21 기준으로 맞춰야 한다:
- ONNX 경로 (`ymas_v13.onnx` → `ymas_v21_model_dropout` 기반 ONNX)
- physics 차원 (v13 은 8, v17+ 은 10)
- PMEAN/PSTD, 임계값 (TH_FALL 0.77)

`ymas_tier1_receiver.py` 의 Tier 2 연동 주석은 이미 th_fall=0.77(v21)로
맞춰져 있고, `edge/ymas_realtime_ir.py` 는 physics 10 / 전처리 불변식이
학습 코드와 동일하므로, 통합 시 edge 쪽 전처리·상수를 기준으로 통일 권장.
