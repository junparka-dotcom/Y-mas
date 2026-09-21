#!/usr/bin/env python3
"""
=============================================================
 Y-mas Tier 1 수신부 + Tier 2 연동  (Jetson 측)

 역할
   1) ESP32 로부터 하중/COG 데이터 수신 (Serial 또는 UDP)
   2) 상태 모니터링 및 로깅
   3) ALERT 수신 시 Tier 2 비전 AI 기동 (threading.Event)
   4) 하드웨어 없이 테스트하는 시뮬레이터 내장

 실행
   실물 :  python3 ymas_tier1_receiver.py --port /dev/ttyUSB0
   UDP  :  python3 ymas_tier1_receiver.py --udp 5005
   모의 :  python3 ymas_tier1_receiver.py --sim
=============================================================
"""
import argparse
import csv
import math
import os
import random
import socket
import threading
import time
from collections import deque
from datetime import datetime

# =============================================================
# 데이터 구조
# =============================================================
class Reading:
    """ESP32 가 보낸 한 프레임"""
    __slots__ = ('ms', 'state', 'total', 'cog_x', 'cog_y',
                 'edge', 'vel', 'd_edge', 'd_w', 'tte', 'reason',
                 'kg', 'recv_time')

    def __init__(self, ms, state, total, cog_x, cog_y, edge, vel,
                 d_edge, d_w, tte, reason, kg):
        self.ms        = ms
        self.state     = state
        self.total     = total
        self.cog_x     = cog_x
        self.cog_y     = cog_y
        self.edge      = edge
        self.vel       = vel
        self.d_edge    = d_edge      # 이탈도 변화율 /s
        self.d_w       = d_w         # 체중 변화율 kg/s
        self.tte       = tte         # 임계 도달 예상 s
        self.reason    = reason
        self.kg        = kg
        self.recv_time = time.time()

    def __repr__(self):
        return (f"[{self.state:7s}] W={self.total:6.2f}kg "
                f"COG=({self.cog_x:6.1f},{self.cog_y:5.1f}) "
                f"edge={self.edge:.3f} dE={self.d_edge:+6.2f}/s "
                f"dW={self.d_w:+7.1f}kg/s")


def parse_line(line):
    """
    YMAS,<ms>,<state>,<total>,<cogX>,<cogY>,<edge>,<vel>,
         <dEdge>,<dW>,<tte>,<reason>,<FL>,<FR>,<RL>,<RR>
    """
    line = line.strip()
    if not line.startswith('YMAS,'):
        return None
    p = line.split(',')
    if len(p) != 16:
        return None
    try:
        return Reading(
            ms     = int(p[1]),
            state  = p[2],
            total  = float(p[3]),
            cog_x  = float(p[4]),
            cog_y  = float(p[5]),
            edge   = float(p[6]),
            vel    = float(p[7]),
            d_edge = float(p[8]),
            d_w    = float(p[9]),
            tte    = float(p[10]),
            reason = p[11],
            kg     = [float(p[12]), float(p[13]),
                      float(p[14]), float(p[15])],
        )
    except ValueError:
        return None


def parse_alert(line):
    """YMAS_ALERT,<ms>,<total>,<cogX>,<cogY>,<edge>,<dW>,<reason>"""
    line = line.strip()
    if not line.startswith('YMAS_ALERT,'):
        return None
    p = line.split(',')
    if len(p) != 8:
        return None
    try:
        return dict(ms=int(p[1]), total=float(p[2]),
                    cog_x=float(p[3]), cog_y=float(p[4]),
                    edge=float(p[5]), d_w=float(p[6]), reason=p[7])
    except ValueError:
        return None


# =============================================================
# Tier 1 -> Tier 2 연동 컨트롤러
# =============================================================
class Tier1Controller:
    """
    ESP32 상태를 받아 Tier 2 기동 여부를 결정.

    ESP32 가 이미 1차 판정(ALERT)을 하지만, Jetson 쪽에서
    한 번 더 지속성을 확인해 순간 노이즈로 카메라가 켜지는 것을 방지.
    """
    def __init__(self, wake_event, log_path=None,
                 sustain_n=1, cooldown_s=8.0):
        self.wake_event = wake_event
        self.sustain_n  = sustain_n      # 연속 N회 ALERT 확인
        self.cooldown_s = cooldown_s     # 재기동 억제 시간
        self.hits       = 0
        self.last_wake  = 0.0
        self.history    = deque(maxlen=200)   # 최근 10초분 (20Hz)
        self.log_file   = None
        self.log_writer = None
        if log_path:
            self._open_log(log_path)

    def _open_log(self, path):
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        new = not os.path.exists(path)
        self.log_file   = open(path, 'a', newline='')
        self.log_writer = csv.writer(self.log_file)
        if new:
            self.log_writer.writerow(
                ['timestamp', 'esp_ms', 'state', 'total_kg',
                 'cog_x', 'cog_y', 'edge', 'vel',
                 'd_edge', 'd_w', 'tte', 'reason',
                 'FL', 'FR', 'RL', 'RR'])

    def feed(self, r: Reading):
        self.history.append(r)

        if self.log_writer:
            self.log_writer.writerow([
                datetime.now().isoformat(timespec='milliseconds'),
                r.ms, r.state, f"{r.total:.2f}",
                f"{r.cog_x:.1f}", f"{r.cog_y:.1f}",
                f"{r.edge:.3f}", f"{r.vel:.1f}",
                f"{r.d_edge:.3f}", f"{r.d_w:.1f}",
                f"{r.tte:.3f}", r.reason,
                *[f"{v:.2f}" for v in r.kg]])
            self.log_file.flush()

        if r.state == 'ALERT':
            self.hits += 1
        else:
            self.hits = max(0, self.hits - 1)

        now = time.time()
        if self.hits >= self.sustain_n and (now - self.last_wake) > self.cooldown_s:
            self.last_wake = now
            self.hits = 0
            self._trigger(r)
            return True
        return False

    def _trigger(self, r: Reading):
        print()
        print("=" * 62)
        print(f" TIER 1 인터럽트 발생  {datetime.now().strftime('%H:%M:%S')}")
        print(f"   총중량   {r.total:.2f} kg")
        print(f"   COG      ({r.cog_x:.1f}, {r.cog_y:.1f}) mm")
        print(f"   이탈도   {r.edge:.3f}  (변화율 {r.d_edge:+.2f}/s)")
        print(f"   체중변화 {r.d_w:+.1f} kg/s")
        print(f"   사유     {r.reason}")
        print(" -> Tier 2 비전 AI 기동")
        print("=" * 62)
        print()
        self.wake_event.set()

    def snapshot(self, seconds=3.0):
        """인터럽트 직전 N초 데이터 (Tier 2 링버퍼와 대응)"""
        now = time.time()
        return [x for x in self.history if now - x.recv_time <= seconds]


# =============================================================
# Tier 2 (비전 AI) 스레드 -- 실제 모델 연결 지점
# =============================================================
def vision_worker(wake_event, stop_event, controller):
    """
    평소 대기(wait) 상태로 전력 소모 최소화.
    Tier 1 인터럽트 시 기상하여 카메라 + ST-GCN 추론 수행.

    실제 배포 시 이 함수 안에서 아래를 수행:
      1. Orbbec Femto W 스트림 시작
      2. 25관절 스켈레톤 취득
      3. YmasRuntime(th_fall=0.77).push(frame) 루프
         (배포 임계값 0.77 = v21 확정값. configs/v21_model_dropout.yaml 참조.
          모델/체크포인트를 바꾸면 이 값과 pmean/pstd 도 함께 맞출 것 -- 교훈 5)
      4. '낙상 확정' 시 간호사 앱으로 MQTT/WebSocket 송출
    """
    while not stop_event.is_set():
        if not wake_event.wait(timeout=0.5):
            continue
        wake_event.clear()

        print("[Tier 2] 기상 -- 카메라 및 ST-GCN 가동")
        pre = controller.snapshot(seconds=3.0)
        if pre:
            print(f"[Tier 2] 인터럽트 직전 {len(pre)}프레임 하중 이력 확보")
            print(f"         COG 이동 "
                  f"({pre[0].cog_x:.0f},{pre[0].cog_y:.0f}) -> "
                  f"({pre[-1].cog_x:.0f},{pre[-1].cog_y:.0f}) mm")

        # ---- 여기에 실제 비전 추론 삽입 ----
        for i in range(1, 4):
            if stop_event.is_set():
                break
            print(f"[Tier 2] 추론 중... {i}/3")
            time.sleep(1.0)
        # -----------------------------------

        print("[Tier 2] 판정 완료. 대기 모드 복귀\n")


# =============================================================
# 입력 소스
# =============================================================
def source_serial(port, baud, on_line, stop_event):
    try:
        import serial
    except ImportError:
        print("pyserial 이 없습니다:  pip install pyserial")
        return
    print(f"시리얼 연결: {port} @ {baud}")
    with serial.Serial(port, baud, timeout=1.0) as ser:
        while not stop_event.is_set():
            try:
                raw = ser.readline().decode('utf-8', errors='ignore')
            except Exception as e:
                print("시리얼 오류:", e)
                break
            if raw:
                on_line(raw)


def source_udp(port, on_line, stop_event):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', port))
    sock.settimeout(1.0)
    print(f"UDP 수신 대기: 0.0.0.0:{port}")
    while not stop_event.is_set():
        try:
            data, _ = sock.recvfrom(512)
            on_line(data.decode('utf-8', errors='ignore'))
        except socket.timeout:
            continue
    sock.close()


# =============================================================
# 시뮬레이터 (하드웨어 없이 로직 검증)
# =============================================================
def source_sim(on_line, stop_event, scenario='fall', hz=80.0):
    """
    실제 침대 낙상 물리를 모사.

    실측 문헌 기준 타이밍
      침대 가장자리로 이동 : 1.5 ~ 3 초 (의도적 행동)
      가장자리 -> 바닥     : 0.5 ~ 0.8 초 (중력 낙하)

    v1 시뮬레이터는 낙하 구간을 2초로 잡아 실제보다 느렸음.
    여기서는 0.6초로 현실화.
    """
    PITCH, GAP = 1700.0, 560.0
    W_PATIENT  = 68.0
    dt = 1.0 / hz
    t0 = time.time()
    ms = 0
    fall_onset = None          # 낙하 시작 시각 (지연 측정 기준점)

    print(f"시뮬레이터 (시나리오={scenario}, {hz:.0f}Hz)")
    if scenario == 'fall':
        print("  0~4초 안정 -> 4~6초 가장자리 이동 -> 6.0초 낙하 시작(0.6초)\n")
    else:
        print()

    while not stop_event.is_set():
        t = time.time() - t0
        ms = int(t * 1000)

        cx_r, cy_r = 0.50, 0.50
        w = W_PATIENT

        if scenario == 'fall':
            if t < 4.0:
                pass                                        # 안정
            elif t < 6.0:
                cy_r = 0.50 + 0.18 * (t - 4.0) / 2.0        # 가장자리 이동
            elif t < 6.6:
                # 낙하 0.6초. 중력 가속이므로 2차 곡선
                u = (t - 6.0) / 0.6
                cy_r = 0.68 + 0.30 * (u * u)
                w    = W_PATIENT * (1.0 - 0.75 * (u * u))
                if fall_onset is None:
                    fall_onset = time.time()
                    print(f"### 낙하 개시 t={t:.2f}s "
                          f"(이 시점부터 인터럽트까지가 실제 반응 시간)\n")
            else:
                cy_r = 0.98
                w    = W_PATIENT * 0.20
        elif scenario == 'edge':
            if   t < 3.0: pass
            elif t < 6.0: cy_r = 0.50 + 0.19 * (t - 3.0) / 3.0
            elif t < 9.0: cy_r = 0.69 - 0.19 * (t - 6.0) / 3.0
            else: t0 = time.time()
        elif scenario == 'exit':
            # 정상 기상: 2.5초에 걸쳐 천천히 체중 감소 (낙상 아님)
            if   t < 3.0: pass
            elif t < 5.5:
                u = (t - 3.0) / 2.5
                cy_r = 0.50 + 0.20 * u
                w    = W_PATIENT * (1.0 - 0.85 * u)
            else:
                cy_r = 0.70; w = W_PATIENT * 0.15
        else:  # normal
            cy_r = 0.50 + 0.04 * math.sin(t * 0.8)
            cx_r = 0.50 + 0.03 * math.cos(t * 0.5)

        # 로드셀 노이즈 (80Hz 에서는 개별 샘플 노이즈가 큼 -> 중앙값 필터 전제)
        cy_r += random.gauss(0, 0.006)
        cx_r += random.gauss(0, 0.006)
        w    += random.gauss(0, 0.15)
        cy_r = min(max(cy_r, 0.02), 0.98)
        cx_r = min(max(cx_r, 0.02), 0.98)
        w    = max(w, 0.5)

        rear  = w * cx_r
        front = w - rear
        fr = front * cy_r; fl = front - fr
        rr = rear  * cy_r; rl = rear  - rr

        cog_x = PITCH * (rl + rr) / max(w, 1e-3)
        cog_y = GAP   * (fr + rr) / max(w, 1e-3)
        edge  = abs(cog_y / GAP - 0.5) * 2.0

        # ---- 변화율 (펌웨어 DERIV_WIN=4 와 동일) ----
        if not hasattr(source_sim, '_hist'):
            source_sim._hist = deque(maxlen=4)
        source_sim._hist.append((edge, w, time.time()))
        d_edge = d_w = 0.0
        vel = 0.0
        if len(source_sim._hist) >= 2:
            e0, w0, t_0 = source_sim._hist[0]
            e1, w1, t_1 = source_sim._hist[-1]
            ddt = max(t_1 - t_0, 1e-4)
            d_edge = (e1 - e0) / ddt
            d_w    = (w1 - w0) / ddt
        if not hasattr(source_sim, '_prev'):
            source_sim._prev = (cog_x, cog_y, time.time())
        px, py, pt = source_sim._prev
        pdt = max(time.time() - pt, 1e-4)
        vel = math.hypot(cog_x - px, cog_y - py) / pdt
        source_sim._prev = (cog_x, cog_y, time.time())

        tte = -1.0
        if d_edge > 0.05 and edge < 0.62:
            tte = (0.62 - edge) / d_edge

        # ---- 펌웨어와 동일한 3경로 판정 ----
        reason = '-'
        wloss = (W_PATIENT - w) / W_PATIENT
        if w < 15:
            st = 'EMPTY'
        elif d_w <= -45.0 and edge >= 0.30:
            st = 'ALERT'; reason = 'FASTPATH_WEIGHT_DROP'
        elif edge >= 0.42 and d_edge >= 1.2 and 0 < tte <= 0.25:
            st = 'ALERT'; reason = 'PREDICTIVE_EDGE_RATE'
        elif edge >= 0.62 and (vel >= 250 or wloss >= 0.25):
            st = 'ALERT'; reason = 'STANDARD_EDGE'
        elif edge >= 0.62:
            st = 'DANGER'
        elif edge >= 0.40:
            st = 'CAUTION'
        else:
            st = 'NORMAL'

        if st == 'ALERT' and fall_onset is not None \
           and not hasattr(source_sim, '_reported'):
            source_sim._reported = True
            lat = (time.time() - fall_onset) * 1000
            print(f"### 반응 시간 = {lat:.0f} ms  (사유 {reason})\n")

        on_line(f"YMAS,{ms},{st},{w:.2f},{cog_x:.1f},{cog_y:.1f},"
                f"{edge:.3f},{vel:.1f},{d_edge:.3f},{d_w:.1f},{tte:.3f},"
                f"{reason},{fl:.2f},{fr:.2f},{rl:.2f},{rr:.2f}")
        time.sleep(dt)


# =============================================================
def main():
    ap = argparse.ArgumentParser(description='Y-mas Tier 1 수신부')
    ap.add_argument('--port', help='시리얼 포트 (예: /dev/ttyUSB0, COM3)')
    ap.add_argument('--baud', type=int, default=115200)
    ap.add_argument('--udp',  type=int, help='UDP 수신 포트 (예: 5005)')
    ap.add_argument('--sim',  action='store_true', help='시뮬레이터 모드')
    ap.add_argument('--scenario', default='fall',
                    choices=['fall', 'edge', 'normal', 'exit'])
    ap.add_argument('--hz', type=float, default=80.0,
                help='시뮬레이터 샘플링 주파수')
    ap.add_argument('--log', default='tier1_log.csv')
    ap.add_argument('--quiet', action='store_true',
                    help='NORMAL 상태는 출력 생략')
    args = ap.parse_args()

    wake_event = threading.Event()
    stop_event = threading.Event()
    ctrl = Tier1Controller(wake_event, log_path=args.log)

    vt = threading.Thread(target=vision_worker,
                          args=(wake_event, stop_event, ctrl),
                          daemon=True)
    vt.start()

    last_state = [None]
    counter = [0]

    def on_line(line):
        r = parse_line(line)
        if r is None:
            return
        ctrl.feed(r)
        counter[0] += 1

        show = True
        if args.quiet and r.state == 'NORMAL' and r.state == last_state[0]:
            show = (counter[0] % 80 == 0)      # 1초에 한 번만
        if show:
            mark = '  <<<' if r.state in ('DANGER', 'ALERT') else ''
            print(f"{r}{mark}")
        last_state[0] = r.state

    try:
        if args.sim:
            source_sim(on_line, stop_event, args.scenario, args.hz)
        elif args.udp:
            source_udp(args.udp, on_line, stop_event)
        elif args.port:
            source_serial(args.port, args.baud, on_line, stop_event)
        else:
            ap.print_help()
            print("\n입력 소스를 지정하세요: --port / --udp / --sim")
            return
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        stop_event.set()
        wake_event.set()
        vt.join(timeout=2.0)
        if ctrl.log_file:
            ctrl.log_file.close()
            print(f"로그 저장: {args.log}")


if __name__ == '__main__':
    main()
