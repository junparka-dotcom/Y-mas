import socket
import struct
import json
import numpy as np
import cv2

SOCKET_PATH = "/tmp/ymas_irdepth.sock"
HEADER_FMT = "<4IQ5f"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf += chunk
    return buf

clicked_points = []

def on_mouse(event, x, y, flags, param):
    if event == cv2.EVENT_LBUTTONDOWN:
        if len(clicked_points) < 4:
            clicked_points.append((x, y))
            print(f"모서리 {len(clicked_points)}: ({x}, {y})")

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(SOCKET_PATH)
print("연결됨. 침대의 네 모서리를 순서대로 클릭하세요 (좌상->우상->우하->좌하 권장).")
print("4개 다 찍으면 's'로 저장, 'r'로 다시 시작, 'q'로 종료.\n")

cv2.namedWindow("Bed Calibration")
cv2.setMouseCallback("Bed Calibration", on_mouse)

while True:
    header = recv_exact(sock, HEADER_SIZE)
    w, h, ir_size, depth_size, ts, fx, fy, cx, cy, depth_scale = struct.unpack(HEADER_FMT, header)
    ir_bytes = recv_exact(sock, ir_size)
    depth_bytes = recv_exact(sock, depth_size)

    ir_arr = np.frombuffer(ir_bytes, dtype=np.uint16).reshape((h, w))
    ir_8u = cv2.normalize(ir_arr, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
    display_img = cv2.cvtColor(ir_8u, cv2.COLOR_GRAY2BGR)

    for i, p in enumerate(clicked_points):
        cv2.circle(display_img, p, 6, (0, 0, 255), -1)
        cv2.putText(display_img, str(i+1), (p[0]+8, p[1]-8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    if len(clicked_points) >= 2:
        pts = np.array(clicked_points, dtype=np.int32)
        cv2.polylines(display_img, [pts], len(clicked_points) == 4, (0, 255, 0), 2)

    cv2.putText(display_img, f"{len(clicked_points)}/4 points  (s=save, r=reset, q=quit)",
                (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 1)

    cv2.imshow("Bed Calibration", display_img)
    key = cv2.waitKey(1) & 0xFF
    if key == ord('q'):
        break
    elif key == ord('r'):
        clicked_points.clear()
        print("초기화됨.")
    elif key == ord('s'):
        if len(clicked_points) == 4:
            depth_arr = np.frombuffer(depth_bytes, dtype=np.uint16).reshape((h, w))
            bed_data = {
                "width": w, "height": h,
                "polygon_px": clicked_points,
                "fx": fx, "fy": fy, "cx": cx, "cy": cy,
                "depth_scale": depth_scale
            }
            with open("/home/y-mas/bed_region.json", "w") as f:
                json.dump(bed_data, f, indent=2)
            print("저장 완료: /home/y-mas/bed_region.json")
        else:
            print(f"4개 점이 필요합니다 (현재 {len(clicked_points)}개).")

cv2.destroyAllWindows()
sock.close()
