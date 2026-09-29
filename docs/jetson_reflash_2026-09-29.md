# Jetson AGX Orin 재플래시 작업 정리

**작업일:** 2026-09-29
잠겨 있던 Jetson 초기화 → JetPack 6.2.3 설치 → 노트북 파티션 정리

## 1. 작업 요약

이전 사용자가 쓰던 **Jetson AGX Orin 64GB 개발자 키트**가 비밀번호로 잠겨 있어,
듀얼부팅 노트북(Ubuntu 22.04)의 SDK Manager로 OS를 통째로 재설치(재플래시).
플래시 후 CUDA·개발 도구까지 설치, 작업 공간으로 쓰던 옛 Ubuntu 파티션(p8)은
삭제해 Windows D: 드라이브에 합쳤다.

| 항목 | 결과 |
|---|---|
| Jetson 기기 | Jetson AGX Orin 64GB Developer Kit (64GB eMMC 5.1) |
| 설치 소프트웨어 | JetPack 6.2.3 (Jetson Linux), CUDA 12.6 (nvcc V12.6.68) |
| Jetson 계정 | 사용자명 `ymas` (Pre-Config로 플래시 전 지정) |
| **Jetson IP** | **192.168.0.9** (`ssh ymas@192.168.0.9`) |
| 호스트 PC | 듀얼부팅 노트북, Ubuntu 22.04.5 LTS, x86_64, SDK Manager 2.4.1 |

> **ESP32 펌웨어 연동:** `firmware/ymas_tier1_firmware.ino` 의 `JETSON_IP` 를
> 192.168.0.9 로 맞춰 두었다(UDP 5005). 네트워크가 바뀌면 이 값도 함께 갱신할 것.

## 2. 재플래시가 필요했던 이유

Jetson OS 비밀번호는 OS 안(`/etc/shadow`)에 저장되므로, 모르면 OS 재설치가
가장 확실. Recovery 모드에서는 OS 부팅 전 부트롬이 USB로 명령을 받으므로
잠긴 로그인 화면과 무관하게 저장장치 전체를 덮어쓸 수 있다.

## 3. 사전 확인 (노트북, Ubuntu 터미널)

| 항목 | 명령 | 결과 |
|---|---|---|
| Ubuntu 버전 | `lsb_release -a` | 22.04.5 LTS (jammy) |
| CPU 구조 | `uname -m` | x86_64 |
| 여유 공간 | `df -h /` | / 에 61GB 여유 |

> SDK Manager는 Windows/WSL/VM/Mac에서 플래시가 불안정 → **실제 Ubuntu 부팅** 필수.

## 4. 진행 순서

### 4-1. 작업 폴더 준비 (p8 파티션)
마운트 안 된 ext4 파티션(`nvme0n1p8`, 144.9GB)을 SDK Manager 다운로드용으로 사용.
```
sudo mkdir -p /mnt/p8
sudo mount /dev/nvme0n1p8 /mnt/p8
sudo mkdir /mnt/p8/nvidia
sudo chown $USER:$USER /mnt/p8/nvidia
```

### 4-2. SDK Manager 설치
NVIDIA 개발자 사이트에서 Ubuntu(.deb x86_64) 다운로드 후:
```
cd ~/Downloads
sudo apt install ./sdkmanager_*.deb
sdkmanager
```

### 4-3. SDK Manager 설정
| 단계 | 설정 |
|---|---|
| Step 01 Target HW | 기본값 AGX Thor → **Jetson AGX Orin [64GB developer kit version]** 로 변경 |
| Step 01 SDK Version | **JetPack 6.2.3**, Direct Flash (JetPack 7.x는 Thor용이라 해당 없음) |
| Step 01 Additional SDKs | DeepStream, Holoscan **체크 해제** |
| Step 02 경로 | Download: `/mnt/p8/nvidia/sdkm_downloads`, Install: `/mnt/p8/nvidia/nvidia_sdk` |
| Step 02 체크박스 | 약관 동의, "Download now. Install later" 체크 안 함 |
| Flash 창 | OEM Config: **Pre-Config**(사용자명/비번 지정), Storage: **EMMC** |

### 4-4. Recovery 모드 진입 (AGX Orin 개발자 키트)
1. 전원 어댑터 연결한 채, Jetson 끈 상태(전원 LED 꺼짐)
2. USB-C ↔ USB-A 케이블로 노트북과 **직접** 연결 (허브 금지)
3. **Force Recovery 버튼(가운데)** 누른 채 유지
4. 그 상태에서 **Power 버튼**을 눌렀다 뗀다
5. 2~3초 뒤 Force Recovery 버튼을 뗀다 (화면 꺼져 있는 게 정상)
6. 확인: `lsusb` 에 `NVIDIA Corp. APX` 계열이 보이면 성공

### 4-5. 플래시 및 컴포넌트 설치
- Flash 약 20~40분. 진행 중 USB 케이블/노트북 재부팅 금지(`/mnt/p8` 마운트 유지)
- 플래시 후 Jetson 재부팅 시 SDK Manager가 USB 가상 네트워크(`192.168.55.1`)로
  접속해 Runtime Components(CUDA Runtime, Container Runtime 등) 설치
- 접속 확인: `ping -c 3 192.168.55.1` (응답 3/3)
- 최종: **INSTALLATION COMPLETED SUCCESSFULLY**

## 5. 발생한 문제와 해결

| 문제 | 원인 | 해결 |
|---|---|---|
| Target HW가 AGX Thor로 표시 | Jetson 미연결로 자동감지 실패, 기본값 표시 | 연결 후 수동으로 Orin [64GB dev kit] 지정 |
| "running in normal mode" 메시지 | Recovery 아닌 일반 부팅으로 인식 | 기기 종류만 선택하고 진행, 실제 Recovery는 Flash 단계에서 |
| "Verifying System Readiness" 실패 (apt exitCode 100) | 옛 설치의 `cdrom:` 저장소 항목이 apt update 실패시킴 | `sources.list` 의 `deb cdrom:` 줄 주석 처리 후 Retry |

```
sudo cp /etc/apt/sources.list /etc/apt/sources.list.bak
sudo sed -i '/^deb cdrom:/s/^/# /' /etc/apt/sources.list
sudo apt-get update   # 에러 없이 Done이면 해결
```

## 6. Jetson 설치 결과 확인

| 항목 | 결과 |
|---|---|
| JetPack | `nvidia-jetpack`, `-dev`, `-runtime` 모두 6.2.3+b81 |
| CUDA | `nvcc --version` → release 12.6, V12.6.68 |
| 저장공간 | `/dev/mmcblk0p1` (eMMC) 57GB 중 35GB 여유 |
| 메모리 | 62,828MB 인식 (64GB) |
| Docker | 29.8.1, docker 그룹에 사용자 추가 |
| 네트워크 | **192.168.0.9** (`ssh ymas@192.168.0.9`) |
| 대기 온도 | CPU ~46°C, GPU ~41°C (`tegrastats`) |
| 전력 모드 | 기본 50W → `nvpmodel -m 0`(MAXN) + `jetson_clocks` 로 확인 |
| 추가 설치 | Terminator, Firefox, ibus-hangul, jtop |

## 7. 노트북 파티션 정리 (p8 삭제 → D: 확장)

| 파티션 | 크기 | 종류 | 비고 |
|---|---|---|---|
| p1 | 260MB | EFI | 부팅 (그대로) |
| p3 | 480GB | NTFS | Windows C: (그대로) |
| p9 | 85GB | ext4 | 현재 Ubuntu / (삭제 금지) |
| p5 | 199.6→344GB | NTFS | Back Up (D:), p8 합쳐 확장 |
| p8 | 145GB | ext4 | 옛 Ubuntu, **삭제됨** |
| p4/p6/p7 | 921MB/19GB/1GB | — | 삼성 복구용 (그대로) |

순서: `umount /mnt/p8` → `/etc/fstab` 에 p8 없음 확인 → Windows 디스크 관리에서
145GB 삭제 → D: 확장 → Ubuntu `sudo update-grub`. 결과: D: 344GB(273GB 가용),
Windows/Ubuntu 듀얼부팅 정상.

## 8. 앞으로 주의할 점

- **비밀번호는 따로 기록**할 것 (이번 재플래시 원인이 분실).
- `do-release-upgrade` 로 **Ubuntu 24.04 업그레이드 금지** — JetPack 6은 22.04 기준,
  CUDA/드라이버 깨짐.
- `jetson_clocks` 는 재부팅하면 풀림. MAXN은 발열·전력이 높으니 학습/벤치 때만.
  평소: `sudo jetson_clocks --restore` + `sudo nvpmodel -m 3` (50W).
- Docker 그룹 변경은 **로그아웃 후 재로그인** 해야 적용.
- **PyTorch: `pip install torch`(CPU판) 금지** → JetPack 6.2.3용 NVIDIA wheel/컨테이너.
- eMMC 여유 35GB — Docker/데이터셋 많이 쓰면 부족. M.2 Key M 슬롯에 NVMe 추가.
- 재플래시 시 `/mnt/p8` 없으므로 다운로드 폴더를 Ubuntu `/`(약 61GB)로 지정,
  Jetson Linux만 선택하면 공간 충분.
