#include <libobsensor/ObSensor.hpp>
#include <cstring>
#include <iostream>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <cstdint>
#include <csignal>
#include <atomic>
#include <thread>
#include <chrono>

#pragma pack(push, 1)
struct FrameHeader {
    uint32_t width;
    uint32_t height;
    uint32_t ir_size;
    uint32_t depth_size;
    uint64_t timestamp;
    float fx, fy, cx, cy;
    float depth_scale;
};
#pragma pack(pop)

std::atomic<bool> g_running{true};
void signal_handler(int) { g_running = false; }

int main() {
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    const char* SOCKET_PATH = "/tmp/ymas_irdepth.sock";
    unlink(SOCKET_PATH);

    int server_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, SOCKET_PATH, sizeof(addr.sun_path) - 1);
    bind(server_fd, (sockaddr*)&addr, sizeof(addr));
    listen(server_fd, 1);
    std::cout << "[ir_depth_bridge] listening on " << SOCKET_PATH << "\n";

    ob::Pipeline pipeline;
    auto config = std::make_shared<ob::Config>();

    auto irProfiles = pipeline.getStreamProfileList(OB_SENSOR_IR);
    auto irProfile = irProfiles->getVideoStreamProfile(640, 480, OB_FORMAT_Y16, 30);
    config->enableStream(irProfile);

    auto depthProfiles = pipeline.getStreamProfileList(OB_SENSOR_DEPTH);
    auto depthProfile = depthProfiles->getVideoStreamProfile(640, 480, OB_FORMAT_Y16, 30);
    config->enableStream(depthProfile);

    pipeline.start(config);
    std::this_thread::sleep_for(std::chrono::seconds(2));
    std::cout << "[ir_depth_bridge] streaming started (no align needed)\n";

    auto cameraParam = pipeline.getCameraParam();
    float fx = cameraParam.depthIntrinsic.fx;
    float fy = cameraParam.depthIntrinsic.fy;
    float cx = cameraParam.depthIntrinsic.cx;
    float cy = cameraParam.depthIntrinsic.cy;
    std::cout << "[ir_depth_bridge] depth intrinsics: fx=" << fx << " fy=" << fy
               << " cx=" << cx << " cy=" << cy << "\n";

    while (g_running) {
        std::cout << "[ir_depth_bridge] waiting for python client...\n";
        fd_set fds; FD_ZERO(&fds); FD_SET(server_fd, &fds);
        timeval tv{1, 0};
        if (select(server_fd + 1, &fds, nullptr, nullptr, &tv) <= 0) continue;

        int client_fd = accept(server_fd, nullptr, nullptr);
        if (client_fd < 0) continue;
        std::cout << "[ir_depth_bridge] client connected\n";

        while (g_running) {
            auto frameSet = pipeline.waitForFrames(200);
            if (!frameSet) continue;
            auto irFrame = frameSet->irFrame();
            auto depthFrame = frameSet->depthFrame();
            if (!irFrame || !depthFrame) continue;

            FrameHeader hdr{};
            hdr.width = irFrame->width();
            hdr.height = irFrame->height();
            hdr.ir_size = static_cast<uint32_t>(irFrame->dataSize());
            hdr.depth_size = static_cast<uint32_t>(depthFrame->dataSize());
            hdr.timestamp = depthFrame->timeStamp();
            hdr.fx = fx; hdr.fy = fy; hdr.cx = cx; hdr.cy = cy;
            hdr.depth_scale = depthFrame->as<ob::DepthFrame>()->getValueScale();

            if (send(client_fd, &hdr, sizeof(hdr), MSG_NOSIGNAL) <= 0) break;
            if (send(client_fd, irFrame->data(), hdr.ir_size, MSG_NOSIGNAL) <= 0) break;
            if (send(client_fd, depthFrame->data(), hdr.depth_size, MSG_NOSIGNAL) <= 0) break;
        }
        std::cout << "[ir_depth_bridge] client disconnected, waiting for reconnect\n";
        close(client_fd);
    }

    pipeline.stop();
    close(server_fd);
    unlink(SOCKET_PATH);
    return 0;
}
