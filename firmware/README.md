# firmware/ — ESP32 Tier 1 펌웨어

## `ymas_tier1_firmware.ino`

- **타겟:** keyestudio ESP32-WROOM-32D (KS0413)
- **센서:** CAS BCA-100L ×4 → HX711 ×4
- **역할:** 4채널 하중 수집 → COG 산출 → 3-Path 감지 판정 → LAN UDP 로 Jetson 전송

### 저지연 설계 (v2)

1. HX711 **80Hz 모드** — 샘플 간격 100ms → 12.5ms (**RATE 핀 하드웨어 개조 필수**)
2. 중앙값 필터 — 이동평균 대비 지연 절반, 스파이크 제거 우수
3. 예측 감지 — 이탈도 변화율로 임계 도달 전 선행 알람
4. Fast Path — 체중 급락 시 확정 절차 생략
5. LAN UDP 전송 — 전송 지연 1ms 미만

목표 반응 시간: **0.15 ~ 0.30초**

### COG 산출 (플랫폼 저울 원리)

```
COG_X = CASTER_PITCH × (RL+RR)/total
COG_Y = RAIL_GAP    × (FR+RR)/total
```

`CASTER_PITCH`, `RAIL_GAP` 은 실측 미확정 가정값(1700mm / 560mm). 실물 침대
측정 후 펌웨어 상수를 확정해야 COG 정확도가 보장된다.

### 빌드

Arduino IDE 또는 arduino-cli 로 ESP32 보드에 업로드. HX711 라이브러리 필요.
업로드 전 반드시 HX711 4개의 RATE 핀 개조(트레이스 컷 + 15번 핀 VCC 점퍼)를
완료할 것 — 개조 없이는 10Hz 에 묶여 지연이 1.5초대로 올라간다.
