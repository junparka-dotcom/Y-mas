# integration/ — Tier 1 ↔ Tier 2 통합

ESP32(Tier 1, 하중/COG) 가 UDP 로 ALERT 를 보내면 Jetson(Tier 2, 비전) 이
`threading.Event` 로 깨어나 낙상을 확정하는 2-Tier 연동.

## 파일

| 파일 | 역할 |
|---|---|
| `ymas_integrated.py` | Tier 1(하중) + Tier 2(비전) 통합 실행. ESP32 UDP 수신 → 인터럽트 → 비전 스레드 → ST-GCN → MQTT 알람. `--sim --mock-camera` 로 하드웨어 없이 테스트 가능 |
| `ymas_tier1_receiver.py` | Tier 1 수신부. ESP32 하중/COG 수신(Serial/UDP) + 모니터링/로깅 + ALERT 시 Tier 2 기동. 시뮬레이터 내장 |
| `collect_ymas_jetson.sh` | Jetson 자산 수집 스크립트 |

## [!] 버전 주의

`ymas_integrated.py` 는 헤더가 **ST-GCN v13 / `ymas_v13.onnx`** 기준으로
작성되어 있다. 현재 모델은 v17(+ v18 실험 진행 중)이므로, end-to-end 통합
실증 전에 아래를 v17 이상으로 맞춰야 한다:
- ONNX 경로 (`ymas_v13.onnx` → 최신)
- physics 차원 (v13 은 8, v17 은 10)
- PMEAN/PSTD, 임계값

`edge/ymas_realtime_ir.py` 는 이미 v17(physics 10, TH_FALL 0.75)로 맞춰져
있으므로, 통합 시 그쪽 전처리·상수를 기준으로 통일하는 것을 권장.
