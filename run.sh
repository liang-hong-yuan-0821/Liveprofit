#!/usr/bin/env bash
# ============================================================
# YoHo - 一键运行脚本
# 自动完成：Docker 启动 → 安装依赖 → 启动服务 → 运行分析
# ============================================================
set -e

# 确保 Python 输出 UTF-8（避免 Windows 中文乱码）
export PYTHONIOENCODING=utf-8

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# --------------- 颜色输出 ---------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# --------------- 0. 启动 Docker Desktop ---------------
start_docker() {
    # 检查 Docker 是否已在运行
    if docker ps --format '{{.Names}}' &>/dev/null; then
        log_info "Docker 已在运行"
        return 0
    fi

    log_info "Docker 未运行，正在启动 Docker Desktop..."

    # Windows: 尝试多个可能的安装路径
    DOCKER_PATHS=(
        "C:/Program Files/Docker/Docker/Docker Desktop.exe"
        "C:/Program Files (x86)/Docker/Docker/Docker Desktop.exe"
        "$LOCALAPPDATA/Docker/Docker Desktop.exe"
    )

    for DOCKER_PATH in "${DOCKER_PATHS[@]}"; do
        DOCKER_PATH="${DOCKER_PATH//\\//}"  # 反斜杠转正斜杠
        if [ -f "$DOCKER_PATH" ]; then
            log_info "找到 Docker Desktop: $DOCKER_PATH"
            start "" "$DOCKER_PATH" 2>/dev/null || cmd.exe /c "start \"\" \"$DOCKER_PATH\"" 2>/dev/null || true
            break
        fi
    done

    # 等待 Docker 就绪（最多等 60 秒）
    log_info "等待 Docker 就绪（最多 60 秒）..."
    for i in $(seq 1 60); do
        if docker ps --format '{{.Names}}' &>/dev/null; then
            log_info "Docker 已就绪 ($i 秒)"
            return 0
        fi
        sleep 1
        # 每 10 秒显示一次进度
        if [ $((i % 10)) -eq 0 ]; then
            echo -n "."
        fi
    done

    log_error "Docker 启动超时，请手动启动 Docker Desktop 后重试"
    return 1
}

start_docker || exit 1

# --------------- 1. 启动 Docker Compose 服务 ---------------
log_info "启动 PostgreSQL + Redis 服务..."
docker-compose up -d

# 等待 PostgreSQL 和 Redis 就绪
log_info "等待 Redis 健康检查..."
sleep 5

# --------------- 2. 激活虚拟环境 ---------------
log_info "激活虚拟环境..."
if [ -f ".venv/Scripts/activate" ]; then
    source .venv/Scripts/activate
    log_info "虚拟环境已激活 ($(which python))"
else
    log_error "未找到 .venv/Scripts/activate，请先创建虚拟环境"
    exit 1
fi

# --------------- 3. 安装依赖 ---------------
log_info "检查依赖..."
if ! python -c "import langgraph" 2>/dev/null; then
    log_warn "依赖未安装，正在安装..."
    pip install -e .
    log_info "依赖安装完成"
else
    log_info "依赖已就绪"
fi

# --------------- 4. 加载 .env ---------------
log_info "加载 .env 环境变量..."
if [ -f ".env" ]; then
    set -a
    while IFS= read -r line; do
        case "$line" in
            ""|\#*) continue ;;
            *) export "${line%%#*}" 2>/dev/null || true ;;
        esac
    done < .env
    set +a
    log_info ".env 已加载 (数据源: ${YOHO_DATA_SOURCE})"
else
    log_warn "未找到 .env 文件"
fi

# --------------- 5. 运行 ---------------
echo ""
log_info "============================================"
log_info "  启动 YoHo 多智能体交易分析系统"
log_info "  LLM: ${YOHO_DEEP_MODEL:-unknown}"
log_info "  数据源: ${YOHO_DATA_SOURCE:-unknown}"
log_info "============================================"
echo ""

python main.py

log_info "运行结束"
