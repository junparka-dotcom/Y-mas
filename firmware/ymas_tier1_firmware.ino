/* =============================================================
 * Y-mas Tier 1 : 레일 플랫폼 하중 감지 펌웨어  (저지연판 v2)
 * Target : keyestudio ESP32-WROOM-32D (KS0413)
 * Sensor : CAS BCA-100L x4  ->  HX711 x4
 *
 * ============ v1 대비 지연 개선 ============
 *  (1) HX711 80Hz 모드      100ms -> 12.5ms 샘플 간격
 *  (2) 중앙값 필터          이동평균 대비 지연 절반, 스파이크 제거는 우수
 *  (3) 예측 감지            이탈도 변화율로 임계 도달 전 선행 알람
 *  (4) Fast Path            체중 급락 감지 시 확정 절차 생략
 *  (5) LAN UDP 전송         전송 지연 1ms 미만
 *  목표 반응 시간 : 0.15 ~ 0.30 초
 *
 * ============ 필수 하드웨어 개조 ============
 *  HX711 모듈의 RATE 핀(IC 15번)이 GND 에 연결되어 출고됩니다(10Hz).
 *  이 상태로는 샘플 간격이 100ms 라 저지연이 불가능합니다.
 *
 *   1. 모듈 뒷면에서 RATE(15번) -> GND 패턴을 커터로 절단
 *   2. 15번 핀을 VCC(DVDD) 로 점퍼
 *   3. 4개 모듈 모두 동일하게 작업
 *   -> 80Hz 모드. 노이즈는 약간 증가하나 중앙값 필터로 상쇄됩니다.
 *
 *  개조를 안 했다면 HX711_80HZ 를 0 으로 두십시오. 동작은 하되 느립니다.
 *
 * ============ 배선 ============
 *   HX711        ESP32
 *   VCC    <->   3V3 (또는 5V)
 *   GND    <->   GND
 *   SCK    <->   GPIO4   (4개 공통 -- 동시 샘플링)
 *   DT_FL  <->   GPIO16
 *   DT_FR  <->   GPIO17
 *   DT_RL  <->   GPIO18
 *   DT_RR  <->   GPIO19
 *
 *   로드셀: Ex+ 빨강 / Ex- 흰색 / Sig+ 파랑 / Sig- 초록 / Shield 검정
 *
 *   LAN (W5500 SPI 모듈 기준)
 *   MOSI GPIO23 / MISO GPIO19* / SCLK GPIO18* / CS GPIO5 / RST GPIO26
 *   * DT 핀과 충돌하므로 W5500 사용 시 DT 핀을 아래로 변경:
 *     DT_FL 32 / DT_FR 33 / DT_RL 25 / DT_RR 27
 * ============================================================= */

#include <Preferences.h>

// ---------- 빌드 옵션 ----------
#define HX711_80HZ    1     // RATE 핀 개조 완료 시 1
#define NET_MODE      1     // 0=시리얼만  1=W5500 LAN  2=WiFi

#if NET_MODE == 1
  #include <SPI.h>
  #include <Ethernet.h>
  #include <EthernetUdp.h>
  byte MAC_ADDR[] = {0xDE, 0xAD, 0xBE, 0xEF, 0xFE, 0x01};
  IPAddress ESP_IP   (192, 168, 0, 50);
  IPAddress JETSON_IP(192, 168, 0, 100);
  const uint16_t JETSON_PORT = 5005;
  const int W5500_CS = 5;
  EthernetUDP udp;
#elif NET_MODE == 2
  #include <WiFi.h>
  #include <WiFiUdp.h>
  const char* WIFI_SSID = "YOUR_SSID";
  const char* WIFI_PASS = "YOUR_PASS";
  IPAddress JETSON_IP(192, 168, 0, 100);
  const uint16_t JETSON_PORT = 5005;
  WiFiUDP udp;
#endif

// ---------- 핀 ----------
const int PIN_SCK = 4;
#if NET_MODE == 1
  const int PIN_DT[4] = {32, 33, 25, 27};   // W5500 과 충돌 회피
#else
  const int PIN_DT[4] = {16, 17, 18, 19};
#endif
enum { FL = 0, FR = 1, RL = 2, RR = 3 };
const char* CH_NAME[4] = {"FL", "FR", "RL", "RR"};

// ---------- 침대 실측값 ----------
float CASTER_PITCH = 1700.0f;   // mm
float RAIL_GAP     =  560.0f;   // mm

// ---------- 캘리브레이션 ----------
long  zeroOffset[4]  = {0, 0, 0, 0};
float scaleFactor[4] = {1.0f, 1.0f, 1.0f, 1.0f};

// ---------- 중앙값 필터 ----------
// 이동평균은 창 길이의 절반만큼 지연됩니다.
// 중앙값은 스파이크를 완전히 제거하면서 계단 응답 지연이 훨씬 작습니다.
#define MED_WIN 5
long  medBuf[4][MED_WIN];
int   medIdx = 0;
bool  medFilled = false;

void medPush(long raw[4]) {
  for (int i = 0; i < 4; i++) medBuf[i][medIdx] = raw[i];
  medIdx = (medIdx + 1) % MED_WIN;
  if (medIdx == 0) medFilled = true;
}

long median5(long a[MED_WIN], int n) {
  long t[MED_WIN];
  for (int i = 0; i < n; i++) t[i] = a[i];
  for (int i = 1; i < n; i++) {          // 삽입 정렬 (n=5, 매우 빠름)
    long k = t[i]; int j = i - 1;
    while (j >= 0 && t[j] > k) { t[j+1] = t[j]; j--; }
    t[j+1] = k;
  }
  return t[n / 2];
}

void medGet(float out[4]) {
  int n = medFilled ? MED_WIN : medIdx;
  if (n == 0) { for (int i=0;i<4;i++) out[i]=0; return; }
  for (int i = 0; i < 4; i++) out[i] = (float)median5(medBuf[i], n);
}

// ---------- 기준선 ----------
bool  baselineSet = false;
float baseW = 0.0f, baseCogX = 0.0f, baseCogY = 0.0f;

// ---------- 상태 ----------
enum State { S_EMPTY, S_NORMAL, S_CAUTION, S_DANGER, S_ALERT };
State curState = S_EMPTY;
const char* STATE_NAME[5] = {"EMPTY","NORMAL","CAUTION","DANGER","ALERT"};
const char* alertReason = "";

// ---------- 임계값 ----------
float TH_OCCUPIED  = 15.0f;    // kg
float TH_EDGE_CAUT = 0.40f;
float TH_EDGE_DANG = 0.62f;
float TH_COG_VEL   = 250.0f;   // mm/s
float TH_WLOSS     = 0.25f;    // 누적 체중 감소 비율

// 저지연 신규 임계값
float TH_WRATE     = -45.0f;   // kg/s, 체중 급락 속도 (Fast Path)
float TH_EDGE_RATE =  1.2f;    // /s, 이탈도 증가 속도
float TH_TTE       = 0.25f;    // s, 임계 도달 예상 시간
float TH_EDGE_PRED = 0.42f;    // 예측 감지를 켜는 최소 이탈도
int   CONFIRM_N    = 2;

int  dangerHits = 0;
unsigned long lastAlertMs = 0;
const unsigned long ALERT_COOLDOWN_MS = 8000;

// ---------- 자동 영점 보정 (auto-tare) ----------
// 목적: PETG 지지 구조의 크리프(creep)로 zeroOffset 이 서서히 밀리는 것을 보정.
// 원리: 침대가 확실히 비어 있고(raw 가 zeroOffset 근처) 값이 안정적일 때만,
//       zeroOffset 을 아주 천천히(EMA) raw 쪽으로 끌어당긴다.
//       크리프처럼 느린 드리프트만 추종하고, 사람이 올라오는 급변은 무시.
bool  autoTareEnabled = true;
float AT_ALPHA        = 0.02f;    // EMA 계수 (작을수록 느리게 추종)
float AT_STABLE_KG    = 0.30f;    // 이 kg 이하 변동이어야 "안정"으로 간주
uint32_t AT_HOLD_MS   = 3000;     // 빈 상태가 이만큼 지속돼야 보정 시작
long  AT_MAX_STEP     = 500;      // 1회 보정 최대 이동량(raw count) — 폭주 방지
uint32_t atEmptySince = 0;        // S_EMPTY 진입 시각 (0=미진입)
uint32_t lastAutoTareMs = 0;
long  lastAutoTareDelta[4] = {0,0,0,0};   // 진단용: 마지막 보정 이동량

// ---------- 미분 계산 (지연 최소화를 위해 짧은 창) ----------
#define DERIV_WIN 4
float  hEdge[DERIV_WIN], hW[DERIV_WIN];
uint32_t hT[DERIV_WIN];
int   hIdx = 0; bool hFilled = false;

void derivPush(float edge, float w, uint32_t ms) {
  hEdge[hIdx] = edge; hW[hIdx] = w; hT[hIdx] = ms;
  hIdx = (hIdx + 1) % DERIV_WIN;
  if (hIdx == 0) hFilled = true;
}

// 가장 오래된 값과 최신 값의 차분 (선형 근사)
bool derivGet(float* dEdge, float* dW) {
  int n = hFilled ? DERIV_WIN : hIdx;
  if (n < 2) return false;
  int newest = (hIdx - 1 + DERIV_WIN) % DERIV_WIN;
  int oldest = hFilled ? hIdx : 0;
  float dt = (hT[newest] - hT[oldest]) / 1000.0f;
  if (dt < 1e-4f) return false;
  *dEdge = (hEdge[newest] - hEdge[oldest]) / dt;
  *dW    = (hW[newest]    - hW[oldest])    / dt;
  return true;
}

float prevCogX = 0, prevCogY = 0;
uint32_t prevMs = 0; bool prevValid = false;

Preferences prefs;

// =============================================================
// HX711 4채널 동시 리딩
// =============================================================
bool hxAllReady() {
  for (int i = 0; i < 4; i++)
    if (digitalRead(PIN_DT[i]) == HIGH) return false;
  return true;
}

void hxReadAll(long out[4]) {
  uint32_t v[4] = {0,0,0,0};
  noInterrupts();
  for (int b = 0; b < 24; b++) {
    digitalWrite(PIN_SCK, HIGH);
    delayMicroseconds(1);
    for (int i = 0; i < 4; i++)
      v[i] = (v[i] << 1) | (uint32_t)digitalRead(PIN_DT[i]);
    digitalWrite(PIN_SCK, LOW);
    delayMicroseconds(1);
  }
  digitalWrite(PIN_SCK, HIGH); delayMicroseconds(1);   // 25th: gain 128
  digitalWrite(PIN_SCK, LOW);  delayMicroseconds(1);
  interrupts();
  for (int i = 0; i < 4; i++) {
    if (v[i] & 0x800000UL) v[i] |= 0xFF000000UL;
    out[i] = (long)(int32_t)v[i];
  }
}

// 논블로킹: 준비 안 됐으면 즉시 false 반환 (루프를 막지 않음)
bool hxPoll(long out[4]) {
  if (!hxAllReady()) return false;
  hxReadAll(out);
  return true;
}

bool hxReadBlocking(long out[4], uint32_t timeoutMs = 300) {
  uint32_t t0 = millis();
  while (!hxAllReady()) {
    if (millis() - t0 > timeoutMs) return false;
    delayMicroseconds(200);
  }
  hxReadAll(out);
  return true;
}

// =============================================================
void toKg(const float rawAvg[4], float kg[4]) {
  for (int i = 0; i < 4; i++) {
    float d = rawAvg[i] - (float)zeroOffset[i];
    kg[i] = (fabsf(scaleFactor[i]) < 1e-6f) ? 0.0f : d / scaleFactor[i];
  }
}

struct Measure {
  float kg[4];
  float total, cogX, cogY, edgeRatio, cogVel;
  float dEdge, dW;      // 변화율
  float tte;            // 임계 도달 예상 시간 (s), 음수면 접근 안 함
  bool  valid;
};

Measure computeMeasure(const float kg[4]) {
  Measure m;
  for (int i = 0; i < 4; i++) m.kg[i] = kg[i];
  m.total = kg[FL] + kg[FR] + kg[RL] + kg[RR];
  m.dEdge = m.dW = 0.0f;
  m.tte = -1.0f;

  if (m.total < 1.0f) {
    m.cogX = m.cogY = m.edgeRatio = m.cogVel = 0.0f;
    m.valid = false;
    return m;
  }

  m.cogX = CASTER_PITCH * (kg[RL] + kg[RR]) / m.total;
  m.cogY = RAIL_GAP     * (kg[FR] + kg[RR]) / m.total;
  m.edgeRatio = fabsf(m.cogY / RAIL_GAP - 0.5f) * 2.0f;

  uint32_t now = millis();
  if (prevValid && now > prevMs) {
    float dt = (now - prevMs) / 1000.0f;
    float dx = m.cogX - prevCogX, dy = m.cogY - prevCogY;
    m.cogVel = sqrtf(dx*dx + dy*dy) / dt;
  } else m.cogVel = 0.0f;
  prevCogX = m.cogX; prevCogY = m.cogY; prevMs = now; prevValid = true;

  derivPush(m.edgeRatio, m.total, now);
  derivGet(&m.dEdge, &m.dW);

  // 임계 도달 예상 시간: 현재 증가 속도가 유지된다고 가정한 선형 외삽
  if (m.dEdge > 0.05f && m.edgeRatio < TH_EDGE_DANG)
    m.tte = (TH_EDGE_DANG - m.edgeRatio) / m.dEdge;

  m.valid = true;
  return m;
}

// =============================================================
// 상태 판정 -- 3경로 구조
//
//  Fast Path  : 체중 급락. 이미 낙하 중이므로 확정 절차 생략
//  Predictive : 이탈도가 임계 도달 예정. 도달 전에 선행 알람
//  Standard   : 기존 임계값 기반. 느리지만 확실
// =============================================================
State judge(const Measure& m) {
  alertReason = "";

  if (m.total < TH_OCCUPIED) { dangerHits = 0; return S_EMPTY; }
  if (!baselineSet) return S_NORMAL;

  float wLoss = (baseW > 1.0f) ? (baseW - m.total) / baseW : 0.0f;

  // ---- Fast Path : 체중 급락 ----
  // 정상 기상은 2~3초에 걸쳐 감소, 낙상은 0.3~0.5초에 급락.
  // dW/dt 가 -45kg/s 아래면 물리적으로 낙하 중.
  if (m.dW <= TH_WRATE && m.edgeRatio >= 0.30f) {
    alertReason = "FASTPATH_WEIGHT_DROP";
    return S_ALERT;                      // 확정 절차 생략
  }

  // ---- Predictive : 임계 도달 예측 ----
  if (m.edgeRatio >= TH_EDGE_PRED &&
      m.dEdge >= TH_EDGE_RATE &&
      m.tte > 0.0f && m.tte <= TH_TTE) {
    dangerHits++;
    if (dangerHits >= CONFIRM_N) {
      alertReason = "PREDICTIVE_EDGE_RATE";
      return S_ALERT;
    }
    return S_DANGER;
  }

  // ---- Standard ----
  bool edgeCaution = (m.edgeRatio >= TH_EDGE_CAUT);
  bool edgeDanger  = (m.edgeRatio >= TH_EDGE_DANG);
  bool fastMove    = (m.cogVel   >= TH_COG_VEL);
  bool weightDrop  = (wLoss      >= TH_WLOSS);

  if (edgeDanger && (fastMove || weightDrop)) {
    dangerHits++;
    if (dangerHits >= CONFIRM_N) {
      alertReason = "STANDARD_EDGE";
      return S_ALERT;
    }
    return S_DANGER;
  }
  if (edgeDanger || (edgeCaution && fastMove)) {
    dangerHits = max(0, dangerHits - 1);
    return S_DANGER;
  }
  if (edgeCaution) { dangerHits = 0; return S_CAUTION; }

  dangerHits = 0;
  return S_NORMAL;
}

// =============================================================
// 자동 영점 보정 (auto-tare)
//   st       : 이번 프레임 상태 (S_EMPTY 여야 후보)
//   avg[4]   : 중앙값 필터를 거친 raw 평균 (zeroOffset 차감 전)
//   m        : 현재 측정 (안정성 판단에 total 사용)
// 조건을 모두 만족할 때만 zeroOffset 을 EMA 로 소폭 이동한다.
// =============================================================
void autoTare(State st, const float avg[4], const Measure& m) {
  if (!autoTareEnabled) { atEmptySince = 0; return; }

  // 빈 상태가 아니면 타이머 리셋 후 종료 (사람이 있으면 절대 건드리지 않음)
  if (st != S_EMPTY) { atEmptySince = 0; return; }

  uint32_t now = millis();
  if (atEmptySince == 0) { atEmptySince = now; return; }

  // 빈 상태가 충분히 지속됐는지
  if (now - atEmptySince < AT_HOLD_MS) return;

  // 값이 안정적인지: 현재 total(빈 상태라 0 근처여야) 의 절대값이 작아야 함
  if (fabsf(m.total) > AT_STABLE_KG) return;

  // 초당 1회 정도로만 (과보정 방지)
  if (now - lastAutoTareMs < 1000) return;
  lastAutoTareMs = now;

  // 각 채널 zeroOffset 을 raw 쪽으로 EMA. 단, 1회 이동량은 AT_MAX_STEP 로 제한.
  for (int i = 0; i < 4; i++) {
    long target = (long)avg[i];
    long delta  = (long)((target - zeroOffset[i]) * AT_ALPHA);
    if (delta >  AT_MAX_STEP) delta =  AT_MAX_STEP;
    if (delta < -AT_MAX_STEP) delta = -AT_MAX_STEP;
    zeroOffset[i] += delta;
    lastAutoTareDelta[i] = delta;
  }
}

// =============================================================
// NVS
// =============================================================
void saveCal() {
  prefs.begin("ymas", false);
  for (int i = 0; i < 4; i++) {
    char k1[8], k2[8];
    snprintf(k1, sizeof(k1), "z%d", i);
    snprintf(k2, sizeof(k2), "s%d", i);
    prefs.putLong(k1, zeroOffset[i]);
    prefs.putFloat(k2, scaleFactor[i]);
  }
  prefs.putFloat("pitch", CASTER_PITCH);
  prefs.putFloat("gap",   RAIL_GAP);
  prefs.end();
  Serial.println("[OK] 캘리브레이션 저장");
}

void loadCal() {
  prefs.begin("ymas", true);
  for (int i = 0; i < 4; i++) {
    char k1[8], k2[8];
    snprintf(k1, sizeof(k1), "z%d", i);
    snprintf(k2, sizeof(k2), "s%d", i);
    zeroOffset[i]  = prefs.getLong (k1, 0);
    scaleFactor[i] = prefs.getFloat(k2, 1.0f);
  }
  CASTER_PITCH = prefs.getFloat("pitch", CASTER_PITCH);
  RAIL_GAP     = prefs.getFloat("gap",   RAIL_GAP);
  prefs.end();
  Serial.println("[OK] 캘리브레이션 복원");
}

// =============================================================
// 명령
// =============================================================
void printHelp() {
  Serial.println();
  Serial.println("===== Y-mas Tier 1 명령어 =====");
  Serial.println("  tare              무부하 영점 조정");
  Serial.println("  cal <ch> <kg>     채널 개별 보정");
  Serial.println("  calall <kg>       중앙 배치 4채널 보정");
  Serial.println("  base              환자 기준선 저장");
  Serial.println("  clearbase         기준선 해제");
  Serial.println("  dim <pitch> <gap> 캐스터 간격 (mm)");
  Serial.println("  th                임계값 출력");
  Serial.println("  set <name> <val>  임계값 변경");
  Serial.println("     occ caut dang vel wloss wrate erate tte epred confirm");
  Serial.println("     atalpha atstable athold");
  Serial.println("  rate              샘플링 속도 측정");
  Serial.println("  autotare [on|off] 빈 상태 자동 영점(크리프 보정) 상태/전환");
  Serial.println("  save / load / raw / help");
  Serial.println("===============================");
  Serial.println();
}

void doTare() {
  Serial.println("영점 조정 중... 침대에서 손을 떼세요.");
  long acc[4] = {0,0,0,0}; int ok = 0;
  for (int k = 0; k < 40; k++) {
    long r[4];
    if (hxReadBlocking(r)) { for (int i=0;i<4;i++) acc[i]+=r[i]; ok++; }
  }
  if (!ok) { Serial.println("[ERR] HX711 응답 없음"); return; }
  for (int i = 0; i < 4; i++) zeroOffset[i] = acc[i]/ok;
  medFilled = false; medIdx = 0; hFilled = false; hIdx = 0;
  Serial.print("[OK] 영점  ");
  for (int i=0;i<4;i++){ Serial.print(CH_NAME[i]); Serial.print("=");
                         Serial.print(zeroOffset[i]); Serial.print(" "); }
  Serial.println();
}

void doCal(int ch, float kg) {
  if (ch < 0 || ch > 3 || kg <= 0) { Serial.println("[ERR] cal <0~3> <kg>"); return; }
  Serial.print(CH_NAME[ch]); Serial.print(" 에 ");
  Serial.print(kg); Serial.println("kg 올린 상태로 측정...");
  long acc = 0; int ok = 0;
  for (int k = 0; k < 40; k++) { long r[4];
    if (hxReadBlocking(r)) { acc += r[ch]; ok++; } }
  if (!ok) { Serial.println("[ERR] 읽기 실패"); return; }
  float d = (float)acc/ok - (float)zeroOffset[ch];
  if (fabsf(d) < 100.0f) { Serial.println("[ERR] 변화량 부족"); return; }
  scaleFactor[ch] = d / kg;
  Serial.print("[OK] scale="); Serial.println(scaleFactor[ch], 2);
}

void doCalAll(float kg) {
  if (kg <= 0) { Serial.println("[ERR] calall <kg>"); return; }
  Serial.print("중앙에 "); Serial.print(kg); Serial.println("kg 배치 후 측정...");
  long acc[4] = {0,0,0,0}; int ok = 0;
  for (int k = 0; k < 40; k++) { long r[4];
    if (hxReadBlocking(r)) { for(int i=0;i<4;i++) acc[i]+=r[i]; ok++; } }
  if (!ok) { Serial.println("[ERR] 읽기 실패"); return; }
  float d[4], sum = 0;
  for (int i=0;i<4;i++){ d[i]=(float)acc[i]/ok-(float)zeroOffset[i]; sum+=d[i]; }
  if (fabsf(sum) < 400.0f) { Serial.println("[ERR] 변화량 부족"); return; }
  for (int i = 0; i < 4; i++) {
    float share = kg * (d[i]/sum);
    if (fabsf(share) > 1e-3f) scaleFactor[i] = d[i]/share;
    Serial.print("  "); Serial.print(CH_NAME[i]);
    Serial.print(" "); Serial.print(share,2);
    Serial.print("kg  scale "); Serial.println(scaleFactor[i],2);
  }
  Serial.println("[OK] 완료");
}

void doBaseline(const Measure& m) {
  if (m.total < TH_OCCUPIED) { Serial.println("[ERR] 침대가 비어 있음"); return; }
  baseW = m.total; baseCogX = m.cogX; baseCogY = m.cogY;
  baselineSet = true;
  Serial.print("[OK] 기준선 W="); Serial.print(baseW,2);
  Serial.print("kg COG=("); Serial.print(baseCogX,1);
  Serial.print(","); Serial.print(baseCogY,1); Serial.println(")");
}

void printTh() {
  Serial.println("--- 임계값 ---");
  Serial.print("  occ     재실 kg        : "); Serial.println(TH_OCCUPIED,1);
  Serial.print("  caut    주의 이탈도    : "); Serial.println(TH_EDGE_CAUT,3);
  Serial.print("  dang    위험 이탈도    : "); Serial.println(TH_EDGE_DANG,3);
  Serial.print("  vel     COG 속도 mm/s  : "); Serial.println(TH_COG_VEL,1);
  Serial.print("  wloss   누적 감소 비율 : "); Serial.println(TH_WLOSS,3);
  Serial.print("  wrate   체중 급락 kg/s : "); Serial.println(TH_WRATE,1);
  Serial.print("  erate   이탈도 속도 /s : "); Serial.println(TH_EDGE_RATE,2);
  Serial.print("  tte     도달예측 s     : "); Serial.println(TH_TTE,3);
  Serial.print("  epred   예측 최소 이탈 : "); Serial.println(TH_EDGE_PRED,3);
  Serial.print("  confirm 확정 회수      : "); Serial.println(CONFIRM_N);
  Serial.print("  pitch/gap mm           : ");
  Serial.print(CASTER_PITCH,1); Serial.print(" / "); Serial.println(RAIL_GAP,1);
  Serial.print("  auto-tare              : ");
  Serial.print(autoTareEnabled ? "ON":"OFF");
  Serial.print("  alpha="); Serial.print(AT_ALPHA,3);
  Serial.print(" stable="); Serial.print(AT_STABLE_KG,2);
  Serial.print("kg hold="); Serial.print(AT_HOLD_MS); Serial.println("ms");
}

void doSet(String n, float v) {
  if      (n=="occ")     TH_OCCUPIED=v;
  else if (n=="caut")    TH_EDGE_CAUT=v;
  else if (n=="dang")    TH_EDGE_DANG=v;
  else if (n=="vel")     TH_COG_VEL=v;
  else if (n=="wloss")   TH_WLOSS=v;
  else if (n=="wrate")   TH_WRATE=v;
  else if (n=="erate")   TH_EDGE_RATE=v;
  else if (n=="tte")     TH_TTE=v;
  else if (n=="epred")   TH_EDGE_PRED=v;
  else if (n=="confirm") CONFIRM_N=(int)v;
  else if (n=="atalpha") AT_ALPHA=v;
  else if (n=="atstable")AT_STABLE_KG=v;
  else if (n=="athold")  AT_HOLD_MS=(uint32_t)v;
  else { Serial.println("[ERR] 알 수 없는 항목"); return; }
  Serial.print("[OK] "); Serial.print(n);
  Serial.print("="); Serial.println(v,3);
}

void doRateTest() {
  Serial.println("샘플링 속도 측정 (2초)...");
  uint32_t t0 = millis(); int cnt = 0; long r[4];
  while (millis() - t0 < 2000) if (hxPoll(r)) cnt++;
  float hz = cnt / 2.0f;
  Serial.print("[결과] "); Serial.print(hz,1); Serial.println(" Hz");
  if (hz < 20.0f) {
    Serial.println("  -> 10Hz 모드입니다. RATE 핀 개조 시 80Hz 가능.");
    Serial.println("     현재 상태로는 반응 지연 300ms 이상 예상.");
  } else if (hz > 60.0f) {
    Serial.println("  -> 80Hz 모드 정상. 저지연 동작 가능.");
  }
}

void handleCommand(String line, const Measure& m) {
  line.trim();
  if (!line.length()) return;
  int s1 = line.indexOf(' ');
  String cmd = (s1<0)? line : line.substring(0,s1);
  String rest = (s1<0)? "" : line.substring(s1+1);
  rest.trim(); cmd.toLowerCase();

  if      (cmd=="help")      printHelp();
  else if (cmd=="tare")      doTare();
  else if (cmd=="base")      doBaseline(m);
  else if (cmd=="clearbase"){ baselineSet=false; Serial.println("[OK] 해제"); }
  else if (cmd=="th")        printTh();
  else if (cmd=="save")      saveCal();
  else if (cmd=="load")      loadCal();
  else if (cmd=="rate")      doRateTest();
  else if (cmd=="autotare") {
    String a = rest; a.toLowerCase();
    if      (a=="on")  { autoTareEnabled = true;  atEmptySince = 0; }
    else if (a=="off") { autoTareEnabled = false; atEmptySince = 0; }
    Serial.print("[OK] auto-tare "); Serial.println(autoTareEnabled ? "ON":"OFF");
    Serial.print("  alpha="); Serial.print(AT_ALPHA,3);
    Serial.print(" stable="); Serial.print(AT_STABLE_KG,2);
    Serial.print("kg hold="); Serial.print(AT_HOLD_MS); Serial.println("ms");
    Serial.print("  last delta ");
    for (int i=0;i<4;i++){ Serial.print(CH_NAME[i]); Serial.print("=");
                           Serial.print(lastAutoTareDelta[i]); Serial.print(" "); }
    Serial.println();
  }
  else if (cmd=="raw") {
    long r[4];
    if (hxReadBlocking(r)) { Serial.print("RAW ");
      for(int i=0;i<4;i++){ Serial.print(CH_NAME[i]); Serial.print("=");
                            Serial.print(r[i]); Serial.print(" "); }
      Serial.println(); }
    else Serial.println("[ERR]");
  }
  else if (cmd=="cal") {
    int s2 = rest.indexOf(' ');
    if (s2<0){ Serial.println("[ERR] cal <ch> <kg>"); return; }
    doCal(rest.substring(0,s2).toInt(), rest.substring(s2+1).toFloat());
  }
  else if (cmd=="calall") doCalAll(rest.toFloat());
  else if (cmd=="dim") {
    int s2 = rest.indexOf(' ');
    if (s2<0){ Serial.println("[ERR] dim <pitch> <gap>"); return; }
    CASTER_PITCH = rest.substring(0,s2).toFloat();
    RAIL_GAP     = rest.substring(s2+1).toFloat();
    Serial.print("[OK] "); Serial.print(CASTER_PITCH);
    Serial.print(" / "); Serial.println(RAIL_GAP);
  }
  else if (cmd=="set") {
    int s2 = rest.indexOf(' ');
    if (s2<0){ Serial.println("[ERR] set <name> <val>"); return; }
    String nm = rest.substring(0,s2); nm.toLowerCase();
    doSet(nm, rest.substring(s2+1).toFloat());
  }
  else Serial.println("[ERR] 알 수 없는 명령");
}

// =============================================================
// 전송
//  YMAS,<ms>,<state>,<total>,<cogX>,<cogY>,<edge>,<vel>,
//       <dEdge>,<dW>,<tte>,<reason>,<FL>,<FR>,<RL>,<RR>
// =============================================================
void emit(const Measure& m, State st) {
  char buf[256];
  snprintf(buf, sizeof(buf),
    "YMAS,%lu,%s,%.2f,%.1f,%.1f,%.3f,%.1f,%.3f,%.1f,%.3f,%s,"
    "%.2f,%.2f,%.2f,%.2f",
    millis(), STATE_NAME[st], m.total, m.cogX, m.cogY,
    m.edgeRatio, m.cogVel, m.dEdge, m.dW, m.tte,
    (alertReason[0] ? alertReason : "-"),
    m.kg[FL], m.kg[FR], m.kg[RL], m.kg[RR]);

  Serial.println(buf);

#if NET_MODE == 1
  udp.beginPacket(JETSON_IP, JETSON_PORT);
  udp.write((const uint8_t*)buf, strlen(buf));
  udp.endPacket();
#elif NET_MODE == 2
  if (WiFi.status() == WL_CONNECTED) {
    udp.beginPacket(JETSON_IP, JETSON_PORT);
    udp.print(buf);
    udp.endPacket();
  }
#endif
}

// 알람은 지연 없이 즉시 별도 전송
void emitAlert(const Measure& m) {
  char buf[160];
  snprintf(buf, sizeof(buf),
    "YMAS_ALERT,%lu,%.2f,%.1f,%.1f,%.3f,%.1f,%s",
    millis(), m.total, m.cogX, m.cogY, m.edgeRatio, m.dW, alertReason);
  Serial.println(buf);
#if NET_MODE == 1
  for (int i = 0; i < 3; i++) {          // UDP 유실 대비 3회 중복 전송
    udp.beginPacket(JETSON_IP, JETSON_PORT);
    udp.write((const uint8_t*)buf, strlen(buf));
    udp.endPacket();
  }
#elif NET_MODE == 2
  for (int i = 0; i < 3; i++) {
    udp.beginPacket(JETSON_IP, JETSON_PORT);
    udp.print(buf);
    udp.endPacket();
  }
#endif
}

// =============================================================
void setup() {
  Serial.begin(115200);
  delay(200);

  pinMode(PIN_SCK, OUTPUT);
  digitalWrite(PIN_SCK, LOW);
  for (int i = 0; i < 4; i++) pinMode(PIN_DT[i], INPUT);
  for (int i = 0; i < 4; i++)
    for (int j = 0; j < MED_WIN; j++) medBuf[i][j] = 0;

  loadCal();

#if NET_MODE == 1
  Ethernet.init(W5500_CS);
  Ethernet.begin(MAC_ADDR, ESP_IP);
  delay(500);
  if (Ethernet.hardwareStatus() == EthernetNoHardware)
    Serial.println("[ERR] W5500 미검출. 배선 확인.");
  else if (Ethernet.linkStatus() == LinkOFF)
    Serial.println("[WARN] LAN 케이블 미연결");
  else {
    Serial.print("[OK] LAN  IP="); Serial.println(Ethernet.localIP());
  }
  udp.begin(JETSON_PORT);
#elif NET_MODE == 2
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(250);
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("[OK] WiFi IP="); Serial.println(WiFi.localIP());
  } else Serial.println("[WARN] WiFi 실패");
  udp.begin(JETSON_PORT);
#endif

  Serial.println();
  Serial.println("=== Y-mas Tier 1 (저지연판) ===");
#if HX711_80HZ
  Serial.println("HX711 80Hz 모드 전제. rate 명령으로 실측 확인 권장.");
#else
  Serial.println("HX711 10Hz 모드. RATE 핀 개조 시 응답 대폭 개선됩니다.");
#endif
  Serial.println("최초: tare -> calall <kg> -> save -> base");
  printHelp();
}

void loop() {
  static uint32_t lastEmit = 0;
  static Measure lastM;
  static bool everMeasured = false;

  long raw[4];
  if (hxPoll(raw)) {                 // 논블로킹 폴링
    medPush(raw);
    float avg[4], kg[4];
    medGet(avg);
    toKg(avg, kg);
    lastM = computeMeasure(kg);
    everMeasured = true;

    State st = judge(lastM);

    // 빈 상태에서 크리프 드리프트 자동 보정 (avg = zeroOffset 차감 전 raw)
    autoTare(st, avg, lastM);

    if (st == S_ALERT) {
      if (millis() - lastAlertMs < ALERT_COOLDOWN_MS) {
        st = S_DANGER;
      } else {
        lastAlertMs = millis();
        emitAlert(lastM);            // 주기 전송과 무관하게 즉시
        Serial.print(">>> INTERRUPT ("); Serial.print(alertReason);
        Serial.println(") Tier 2 기동 요청 <<<");
      }
    }
    curState = st;

    // 주기 전송 25ms (40Hz). 상태 변화 시에는 즉시.
    static State lastSent = S_EMPTY;
    if (millis() - lastEmit >= 25 || st != lastSent) {
      lastEmit = millis(); lastSent = st;
      emit(lastM, curState);
    }
  }

  if (Serial.available())
    handleCommand(Serial.readStringUntil('\n'),
                  everMeasured ? lastM : Measure{});
}
