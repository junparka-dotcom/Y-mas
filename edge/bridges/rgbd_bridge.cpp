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
    uint32_t color_width;
    uint32_t color_height;
    uint32_t color_size;   // MJPG 압축된 바이트 수 (가변)
    uint32_t depth_width;
    uint32_t depth_height;
    uint32_t depth_size;   // 항상 width*height*2
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

    const char* SOCKET_PATH = "/tmp/ymas_rgbd.sock";
    unlink(SOCKET_PATH);

    int server_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, SOCKET_PATH, sizeof(addr.sun_path) - 1);
    if (bind(server_fd, (sockaddr*)&addr, sizeof(addr)) != 0) {
        std::cerr << "bind failed\n"; return 1;
    }
    listen(server_fd, 1);
    std::cout << "[rgbd_bridge] listening on " << SOCKET_PATH << "\n";

    ob::Pipeline pipeline;
    auto config = std::make_shared<ob::Config>();

    auto colorProfiles = pipeline.getStreamProfileList(OB_SENSOR_COLOR);
    auto colorProfile = colorProfiles->getVideoStreamProfile(640, 480, OB_FORMAT_RGB888, 30);
    config->enableStream(colorProfile);

    auto depthProfiles = pipeline.getStreamProfileList(OB_SENSOR_DEPTH);
    auto depthProfile = depthProfiles->getProfile(OB_PROFILE_DEFAULT);
    config->enableStream(depthProfile);

    ob::Align align(OB_STREAM_COLOR);  // 뎁스를 컬러 해상도/좌표계로 정렬

    pipeline.start(config);
    std::this_thread::sleep_for(std::chrono::seconds(2));
    std::cout << "[rgbd_bridge] camera streaming started (aligned to color)\n";

    // 내부파라미터는 시작 후 1회만 조회
    auto cameraParam = pipeline.getCameraParam();
    float fx = cameraParam.rgbIntrinsic.fx;
    float fy = cameraParam.rgbIntrinsic.fy;
    float cx = cameraParam.rgbIntrinsic.cx;
    float cy = cameraParam.rgbIntrinsic.cy;
    std::cout << "[rgbd_bridge] color intrinsics: fx=" << fx << " fy=" << fy
               << " cx=" << cx << " cy=" << cy << "\n";

    while (g_running) {
        std::cout << "[rgbd_bridge] waiting for python client...\n";

        fd_set fds;
        FD_ZERO(&fds);
        FD_SET(server_fd, &fds);
        timeval tv{1, 0};
        int sel = select(server_fd + 1, &fds, nullptr, nullptr, &tv);
        if (sel <= 0) continue;

        int client_fd = accept(server_fd, nullptr, nullptr);
        if (client_fd < 0) continue;
        std::cout << "[rgbd_bridge] client connected\n";

        while (g_running) {
            auto frameSet = pipeline.waitForFrames(200);
            if (!frameSet) continue;
            auto colorFrame = frameSet->colorFrame();
            auto depthFrame = frameSet->depthFrame();
            if (!colorFrame || !depthFrame) continue;

            auto newFrame    = align.process(frameSet);
            auto newFrameSet = newFrame->as<ob::FrameSet>();
            auto alignedColor = newFrameSet->colorFrame();
            auto alignedDepth = newFrameSet->depthFrame();
            if (!alignedColor || !alignedDepth) continue;

            FrameHeader hdr{};
            hdr.color_width  = alignedColor->width();
            hdr.color_height = alignedColor->height();
            hdr.color_size   = static_cast<uint32_t>(alignedColor->dataSize());
            hdr.depth_width  = alignedDepth->width();
            hdr.depth_height = alignedDepth->height();
            hdr.depth_size   = static_cast<uint32_t>(alignedDepth->dataSize());
            hdr.timestamp    = alignedDepth->timeStamp();
            hdr.fx = fx; hdr.fy = fy; hdr.cx = cx; hdr.cy = cy;
            hdr.depth_scale  = alignedDepth->as<ob::DepthFrame>()->getValueScale();

            if (send(client_fd, &hdr, sizeof(hdr), MSG_NOSIGNAL) <= 0) break;
            if (send(client_fd, alignedColor->data(), hdr.color_size, MSG_NOSIGNAL) <= 0) break;
            if (send(client_fd, alignedDepth->data(), hdr.depth_size, MSG_NOSIGNAL) <= 0) break;
        }

        std::cout << "[rgbd_bridge] client disconnected, waiting for reconnect\n";
        close(client_fd);
    }

    std::cout << "[rgbd_bridge] shutting down\n";
    pipeline.stop();
    close(server_fd);
    unlink(SOCKET_PATH);
    return 0;
}
