import socket
import struct
import numpy as np

SOCKET_PATH = "/tmp/ymas_depth.sock"
HEADER_FMT = "<IIIQI"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf += chunk
    return buf

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(SOCKET_PATH)
print("Connected to C++ bridge")

for i in range(20):
    header = recv_exact(sock, HEADER_SIZE)
    width, height, fmt, ts, data_size = struct.unpack(HEADER_FMT, header)
    data = recv_exact(sock, data_size)
    depth = np.frombuffer(data, dtype=np.uint16).reshape((height, width))
    print(f"[{i}] {width}x{height}  min={depth.min()} max={depth.max()} mean={depth.mean():.1f}  nonzero={np.count_nonzero(depth)}")

sock.close()
