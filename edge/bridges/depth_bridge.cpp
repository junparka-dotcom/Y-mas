#include <libobsensor/ObSensor.hpp>
#include <cstring>
#include <iostream>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <cstdint>
#include <csignal>
#include <atomic>

#pragma pack(push, 1)
struct FrameHeader {
    uint32_t width;
    uint32_t height;
    uint32_t format;
    uint64_t timestamp;
    uint32_t data_size;
};
#pragma pack(pop)

std::atomic<bool> g_running{true};

void signal_handler(int) {
    g_running = false;
}

int main() {
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    const char* SOCKET_PATH = "/tmp/ymas_depth.sock";
    unlink(SOCKET_PATH);

    int server_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, SOCKET_PATH, sizeof(addr.sun_path) - 1);
    if (bind(server_fd, (sockaddr*)&addr, sizeof(addr)) != 0) {
        std::cerr << "bind failed\n"; return 1;
    }
    listen(server_fd, 1);
    std::cout << "[bridge] listening on " << SOCKET_PATH << "\n";

    ob::Pipeline pipeline;
    auto profiles = pipeline.getStreamProfileList(OB_SENSOR_DEPTH);
    auto depth_profile = profiles->getVideoStreamProfile(640, 480, OB_FORMAT_Y16, 30);
    auto config = std::make_shared<ob::Config>();
    config->enableStream(depth_profile);
    pipeline.start(config);
    std::cout << "[bridge] camera streaming started\n";

    while (g_running) {
        std::cout << "[bridge] waiting for python client...\n";

        fd_set fds;
        FD_ZERO(&fds);
        FD_SET(server_fd, &fds);
        timeval tv{1, 0};
        int sel = select(server_fd + 1, &fds, nullptr, nullptr, &tv);
        if (sel <= 0) continue;

        int client_fd = accept(server_fd, nullptr, nullptr);
        if (client_fd < 0) continue;
        std::cout << "[bridge] client connected\n";

        while (g_running) {
            auto frameSet = pipeline.waitForFrames(200);
            if (!frameSet) continue;
            auto depth = frameSet->depthFrame();
            if (!depth) continue;

            FrameHeader hdr{};
            hdr.width = depth->width();
            hdr.height = depth->height();
            hdr.format = 0;
            hdr.timestamp = depth->timeStamp();
            hdr.data_size = static_cast<uint32_t>(depth->dataSize());

            if (send(client_fd, &hdr, sizeof(hdr), MSG_NOSIGNAL) <= 0) break;
            if (send(client_fd, depth->data(), hdr.data_size, MSG_NOSIGNAL) <= 0) break;
        }

        std::cout << "[bridge] client disconnected, waiting for reconnect\n";
        close(client_fd);
    }

    std::cout << "[bridge] shutting down\n";
    pipeline.stop();
    close(server_fd);
    unlink(SOCKET_PATH);
    return 0;
}
