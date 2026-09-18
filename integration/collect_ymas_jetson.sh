#!/bin/bash
# =============================================================
#  Y-mas 젯슨 산출물 수집 스크립트
#  실행: bash collect_ymas_jetson.sh
#  결과: ~/ymas_export/ 아래에 코드/모델 아카이브 + 매니페스트 생성
# =============================================================
set -u

STAMP=$(date +%Y%m%d_%H%M%S)
OUT=~/ymas_export
WORK=$OUT/ymas_code_$STAMP
mkdir -p "$WORK"/{src,config,logs,env}

echo "=== [1/6] 소스 코드 수집 ==="
# 홈 디렉토리 루트의 스크립트류 (venv/데이터셋 제외)
for f in ~/*.py ~/*.sh ~/*.cpp ~/*.c ~/*.h ~/*.hpp ~/*.ino ~/Makefile ~/CMakeLists.txt; do
    [ -f "$f" ] && cp -v "$f" "$WORK/src/" 2>/dev/null
done

# 홈 하위에 흩어진 관련 파일까지 탐색 (가상환경/빌드/데이터셋 폴더는 제외)
echo "--- 하위 디렉토리 탐색 ---"
find ~ -maxdepth 3 \
    \( -path '*/pose_env' -o -path '*/venv' -o -path '*/.git' \
       -o -path '*/build' -o -path '*/site-packages' -o -path '*/node_modules' \
       -o -path '*/NTU*' -o -path '*/ETRI*' -o -path '*/dataset*' \) -prune -o \
    -type f \( -name '*ymas*' -o -name '*bed_*' -o -name '*bridge*' \
               -o -name '*pose*.py' -o -name '*fall*' -o -name 'watchdog*' \
               -o -name 'run_*.sh' \) -print 2>/dev/null \
    | grep -v -E '\.(engine|onnx|pt|pth|log|npy|npz|skeleton)$' \
    | while read -r f; do
        rel=$(realpath --relative-to="$HOME" "$f")
        mkdir -p "$WORK/src/_tree/$(dirname "$rel")"
        cp "$f" "$WORK/src/_tree/$rel" 2>/dev/null && echo "  + $rel"
      done

echo
echo "=== [2/6] 설정 파일 수집 ==="
for f in ~/bed_region.json ~/*.json ~/*.yaml ~/*.yml ~/*.cfg ~/*.ini; do
    [ -f "$f" ] && cp -v "$f" "$WORK/config/" 2>/dev/null
done

echo
echo "=== [3/6] 로그 수집 (최근 20개) ==="
if [ -d ~/logs ]; then
    ls -t ~/logs/* 2>/dev/null | head -20 | while read -r f; do
        cp "$f" "$WORK/logs/" && echo "  + $(basename "$f")"
    done
fi

echo
echo "=== [4/6] 실행 환경 스냅샷 ==="
{
    echo "### 수집 일시: $(date)"
    echo "### 호스트: $(hostname)"
    echo
    echo "### JetPack / L4T"
    cat /etc/nv_tegra_release 2>/dev/null
    dpkg -l | grep -E 'nvidia-jetpack|nvidia-l4t-core' 2>/dev/null
    echo
    echo "### OS"
    lsb_release -a 2>/dev/null
    uname -a
    echo
    echo "### CUDA / TensorRT"
    nvcc --version 2>/dev/null
    dpkg -l | grep -E 'tensorrt|cudnn' 2>/dev/null
    echo
    echo "### Python"
    python3 --version
    which python3
} > "$WORK/env/system_info.txt" 2>&1
echo "  + env/system_info.txt"

# 가상환경 패키지 목록
if [ -d ~/pose_env ]; then
    source ~/pose_env/bin/activate 2>/dev/null
    pip freeze > "$WORK/env/requirements_pose_env.txt" 2>/dev/null
    echo "  + env/requirements_pose_env.txt ($(wc -l < "$WORK/env/requirements_pose_env.txt") 패키지)"
    deactivate 2>/dev/null
fi
pip3 freeze > "$WORK/env/requirements_system.txt" 2>/dev/null
echo "  + env/requirements_system.txt"

# C++ 브릿지 빌드 명령 흔적 (히스토리에서 g++ 라인 추출)
grep -E '^\s*(g\+\+|cmake|make)\b' ~/.bash_history 2>/dev/null | sort -u \
    > "$WORK/env/build_commands.txt"
echo "  + env/build_commands.txt (빌드 명령 추정치 — 확인 필요)"

echo
echo "=== [5/6] 매니페스트 생성 ==="
{
    echo "# Y-mas 젯슨 산출물 매니페스트"
    echo "# 생성: $(date)"
    echo
    echo "## 수집된 파일 (SHA256)"
    cd "$WORK" && find . -type f -exec sha256sum {} \; | sort -k2
    echo
    echo "## 모델 파일 (별도 아카이브, 원본 위치 유지)"
    ls -la ~/*.onnx ~/*.engine ~/*.pt ~/*.pth 2>/dev/null
    echo
    echo "## 실행 바이너리"
    ls -la ~/ir_depth_bridge ~/*_bridge 2>/dev/null
} > "$WORK/MANIFEST.txt" 2>&1
echo "  + MANIFEST.txt"

echo
echo "=== [6/6] 아카이브 생성 ==="
cd "$OUT"
tar czf "ymas_code_$STAMP.tar.gz" "ymas_code_$STAMP"
echo "  + $OUT/ymas_code_$STAMP.tar.gz  ($(du -h "ymas_code_$STAMP.tar.gz" | cut -f1))"

# 모델 파일은 용량이 크므로 별도 아카이브
MODEL_FILES=$(ls ~/*.onnx ~/*.engine ~/*.pt ~/*.pth ~/ir_depth_bridge 2>/dev/null)
if [ -n "$MODEL_FILES" ]; then
    tar czf "ymas_models_$STAMP.tar.gz" -C ~ \
        $(cd ~ && ls *.onnx *.engine *.pt *.pth ir_depth_bridge 2>/dev/null) 2>/dev/null
    echo "  + $OUT/ymas_models_$STAMP.tar.gz  ($(du -h "ymas_models_$STAMP.tar.gz" | cut -f1))"
fi

echo
echo "============================================"
echo " 완료. 아래 파일을 업로드하세요:"
echo "   $OUT/ymas_code_$STAMP.tar.gz     ← 우선 이것만 올려도 됩니다"
[ -n "$MODEL_FILES" ] && echo "   $OUT/ymas_models_$STAMP.tar.gz   ← 용량 크면 생략 가능"
echo
echo " 수집 내역 확인:"
echo "   cat $WORK/MANIFEST.txt"
echo "============================================"
