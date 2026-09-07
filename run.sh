#!/usr/bin/env bash
# ============================================================
# LiveProfit - 一键运行脚本
# 自动完成：Docker 启动 → 安装依赖 → 启动服务 → 运行分析 → 日志查看器
# ============================================================
set -e

# 确保 Python 输出 UTF-8（避免 Windows 中文乱码）
export PYTHONIOENCODING=utf-8

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ============================================================
# 用法：
#   ./run.sh                # 一键全栈启动：后端平台 + 前端 dev server + 打开浏览器（推荐）
#   ./run.sh stop           # 停止一键启动的前端与后端进程（不停止 Docker 基础设施）
#   ./run.sh classic        # 经典模式：运行 AI 分析（main.py，不启动 Web）
#   ./run.sh platform       # 仅启动平台后端（infra + 迁移 + API/Worker/Dispatcher）
#   ./run.sh frontend-dev   # 仅前端开发模式（前台 Vite dev server，/api 代理 → 127.0.0.1:8000）
#   ./run.sh frontend-check # 前端质量检查：typecheck + 单测 + 构建
#   ./run.sh frontend-e2e   # 前端 E2E（需后端已运行；自动拉起 dev server）
#   ./run.sh stack          # 全栈容器：构建前端产物后 compose --profile app up
#   ./run.sh ingest-market  # 采集 CN 指数日线（大盘数据；一键启动时自动执行，幂等）
# ============================================================

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
            # </dev/null 防止 cmd 交互式挂起脚本（同"打开浏览器"一步）
            start "" "$DOCKER_PATH" 2>/dev/null || \
                powershell.exe -NoProfile -Command "Start-Process '$DOCKER_PATH'" </dev/null 2>/dev/null || \
                cmd.exe /c "start \"\" \"$DOCKER_PATH\"" </dev/null 2>/dev/null || true
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

run_classic() {
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
    log_info ".env 已加载 (数据源: ${LIVEPROFIT_DATA_SOURCE})"
else
    log_warn "未找到 .env 文件"
fi

# --------------- 5. 启动日志查看器（后台 + 打开浏览器） ---------------
VIEWER_PORT=8501
VIEWER_URL="http://localhost:${VIEWER_PORT}"
if curl -sf -o /dev/null "${VIEWER_URL}/healthz"; then
    log_info "日志查看器已在运行 (${VIEWER_URL})"
else
    log_info "启动日志查看器 (${VIEWER_URL})，日志输出到 logs/viewer.log ..."
    nohup streamlit run AI/logviewer/app.py --server.headless true \
        --server.port ${VIEWER_PORT} > logs/viewer.log 2>&1 &
    # 等待就绪（最多 20 秒）
    for i in $(seq 1 20); do
        if curl -sf -o /dev/null "${VIEWER_URL}/healthz"; then
            log_info "日志查看器已就绪 ($i 秒)"
            break
        fi
        sleep 1
    done
fi
log_info "打开浏览器: ${VIEWER_URL}"
# Git Bash 下 cmd.exe /c "start ..." 的 /c 会被 MSYS 路径转换破坏，
# 导致 cmd 进入交互式会话挂起脚本 → 用 PowerShell Start-Process 打开默认浏览器，
# </dev/null 兜底防交互式挂起，失败不阻塞主流程
powershell.exe -NoProfile -Command "Start-Process '${VIEWER_URL}'" </dev/null 2>/dev/null || \
    cmd.exe /c "start \"\" \"${VIEWER_URL}\"" </dev/null 2>/dev/null || true

# --------------- 6. 运行 ---------------
echo ""
log_info "============================================"
log_info "  启动 LiveProfit 多智能体交易分析系统"
log_info "  LLM: ${LIVEPROFIT_DEEP_MODEL:-unknown}"
log_info "  数据源: ${LIVEPROFIT_DATA_SOURCE:-unknown}"
log_info "============================================"
echo ""

python main.py

log_info "运行结束"
}


# ============================================================
# 平台模式（Web 后端：API / Worker / Dispatcher）
# 详见 README.md「平台模式启动」与 docs/API契约.md
# ============================================================
PLATFORM_PID_FILE="logs/.platform.pids"

start_platform() {
    log_info "===== 启动平台后端（loopback local-only）====="

    # 基础设施（PostgreSQL + Redis；端口仅绑定 127.0.0.1）
    start_docker || exit 1
    docker compose up -d
    log_info "等待 PostgreSQL / Redis 健康检查..."
    sleep 6

    # 虚拟环境与依赖（platform 依赖组）
    if [ -f ".venv/Scripts/activate" ]; then
        source .venv/Scripts/activate
    else
        log_error "未找到 .venv/Scripts/activate，请先创建虚拟环境"
        exit 1
    fi
    if ! python -c "import backend, sqlalchemy, dramatiq" 2>/dev/null; then
        log_warn "平台依赖未安装，正在安装..."
        pip install -e ".[platform]"
    fi

    # 环境变量（.env 提供 LIVEPROFIT_* / PG_* / REDIS_*）
    if [ -f ".env" ]; then
        set -a
        while IFS= read -r line; do
            case "$line" in
                ""|\#*) continue ;;
                *) export "${line%%#*}" 2>/dev/null || true ;;
            esac
        done < .env
        set +a
    fi

    # 本地已缓存 HuggingFace 模型（如 bge-m3）：离线模式跳过联网检查，
    # 否则国内网络下 5 次超时重试会阻塞 Worker 启动/领取任务（.env 可显式设 HF_HUB_OFFLINE=0 覆盖）
    export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"

    # 数据库迁移（只新增平台表，不动事件研究既有表）
    log_info "执行数据库迁移 alembic upgrade head ..."
    alembic upgrade head

    mkdir -p logs
    : > "$PLATFORM_PID_FILE"
    start_daemon() {  # $1=名称 $2=可执行文件
        local name="$1" cmd="$2"
        if pgrep_name "$name"; then
            log_info "$name 已在运行"
            return
        fi
        log_info "启动 $name（日志：logs/$name.log）..."
        nohup "$cmd" > "logs/$name.log" 2>&1 &
        echo "$name $!" >> "$PLATFORM_PID_FILE"
    }
    start_daemon "api" ".venv/Scripts/liveprofit-api.exe"
    start_daemon "worker" ".venv/Scripts/liveprofit-worker.exe"
    start_daemon "dispatcher" ".venv/Scripts/liveprofit-dispatcher.exe"

    # 等待 API 就绪
    for i in $(seq 1 30); do
        if curl -sf -o /dev/null http://127.0.0.1:${LIVEPROFIT_API_PORT:-8000}/health/live; then
            log_info "API 已就绪：http://127.0.0.1:${LIVEPROFIT_API_PORT:-8000}（经 30 秒内等待 $i 次）"
            break
        fi
        sleep 1
    done

    echo ""
    log_info "平台后端已启动（本机 loopback）"
    log_info "  健康检查：http://127.0.0.1:${LIVEPROFIT_API_PORT:-8000}/health/live"
    log_info "  就绪检查：http://127.0.0.1:${LIVEPROFIT_API_PORT:-8000}/health/ready"
    log_info "  API 文档：http://127.0.0.1:${LIVEPROFIT_API_PORT:-8000}/docs"
    log_info "  停止方法：./run.sh stop-platform"
    log_info "  （前端经 docker compose --profile app 的 Nginx 或前端 dev server 访问）"
}

pgrep_name() {  # 按命令行关键字查平台进程（Windows 无 pgrep -f 语义）
    powershell.exe -NoProfile -Command         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" |          Where-Object { \$_.CommandLine -match 'liveprofit-$1' } | Select-Object -First 1" </dev/null 2>/dev/null | grep -q "liveprofit-$1"
}

stop_platform() {
    log_info "停止平台后端进程..."
    if [ -f "$PLATFORM_PID_FILE" ]; then
        while read -r name pid; do
            if kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null || true
                log_info "已停止 $name (pid $pid)"
            fi
        done < "$PLATFORM_PID_FILE"
        rm -f "$PLATFORM_PID_FILE"
    fi
    # 兜底：按命令行关键字清理
    for name in api worker dispatcher; do
        powershell.exe -NoProfile -Command             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" |              Where-Object { \$_.CommandLine -match 'liveprofit-$name' } |              ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }" </dev/null 2>/dev/null || true
    done
    log_info "平台后端已停止（Docker 基础设施未停止；如需停止：docker compose down）"
}

# ============================================================
# 市场数据采集（CN 指数日线 → market_bars_daily；幂等，可重复执行）
# ============================================================
market_ingest() {
    if [ -f ".venv/Scripts/activate" ]; then
        source .venv/Scripts/activate
    else
        log_error "未找到 .venv/Scripts/activate，请先创建虚拟环境"
        return 1
    fi
    if [ -f ".env" ]; then
        set -a
        while IFS= read -r line; do
            case "$line" in
                ""|\#*) continue ;;
                *) export "${line%%#*}" 2>/dev/null || true ;;
            esac
        done < .env
        set +a
    fi
    python -m backend.workers.market_ingest
}

# ============================================================
# 一键全栈启动（默认命令）：平台后端 + 前端 dev server + 打开浏览器
# ============================================================
start_all() {
    log_info "===== LiveProfit 一键全栈启动（后端 + 前端）====="

    # 后端平台（幂等：进程已在运行则跳过）
    start_platform

    # 大盘数据采集（幂等；失败不阻断启动，仅告警）
    log_info "采集 CN 指数日线（大盘数据）..."
    if ! market_ingest; then
        log_warn "市场数据采集未完成（可稍后手动执行 ./run.sh ingest-market）"
    fi

    # 前端 dev server（后台拉起，幂等）
    ensure_pnpm
    frontend_install_if_needed
    if curl -sf -o /dev/null "http://127.0.0.1:5173"; then
        log_info "前端 dev server 已在运行"
    else
        log_info "启动前端 dev server（日志：logs/vite-dev.log）..."
        mkdir -p logs
        ( cd "$FRONTEND_DIR" && nohup pnpm dev > "$SCRIPT_DIR/logs/vite-dev.log" 2>&1 &
          echo $! > "$SCRIPT_DIR/logs/.vite-dev.pid" )
        for i in $(seq 1 30); do
            if curl -sf -o /dev/null "http://127.0.0.1:5173"; then
                log_info "前端已就绪（$i 秒）"
                break
            fi
            sleep 1
        done
    fi

    local front_url="http://localhost:5173"
    log_info "打开浏览器: ${front_url}"
    powershell.exe -NoProfile -Command "Start-Process '${front_url}'" </dev/null 2>/dev/null ||         cmd.exe /c "start \"\" \"${front_url}\"" </dev/null 2>/dev/null || true

    echo ""
    log_info "============================================"
    log_info "  全栈已启动："
    log_info "    Web 工作台：${front_url}（前端 Vite /api 代理 → 127.0.0.1:${LIVEPROFIT_API_PORT:-8000}）"
    log_info "    API 文档：  http://127.0.0.1:${LIVEPROFIT_API_PORT:-8000}/docs"
    log_info "  停止全部：./run.sh stop"
    log_info "  日志实时输出：logs/api.log / worker.log / dispatcher.log（Ctrl+C 退出查看，服务保持运行）"
    log_info "  前端日志：logs/vite-dev.log；任务内核明细：logs/{ts}/（Streamlit 查看器）"
    log_info "============================================"
    echo ""

    # 前台持续展示日志（多文件带文件名头）；退出查看不影响已启动的服务
    tail -n 30 -f "$SCRIPT_DIR/logs/api.log" "$SCRIPT_DIR/logs/worker.log" "$SCRIPT_DIR/logs/dispatcher.log"
}

stop_all() {
    log_info "停止一键启动的前端与后端..."
    stop_platform
    if [ -f "$SCRIPT_DIR/logs/.vite-dev.pid" ]; then
        kill "$(cat "$SCRIPT_DIR/logs/.vite-dev.pid")" 2>/dev/null || true
        rm -f "$SCRIPT_DIR/logs/.vite-dev.pid"
        log_info "已停止前端 dev server"
    fi
    # 兜底：清理仍监听 5173 的 node 进程
    powershell.exe -NoProfile -Command "Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id \$_.OwningProcess -Force }" </dev/null 2>/dev/null || true
    log_info "全栈已停止（Docker 基础设施未停止；如需停止：docker compose down）"
}

# ============================================================
# 前端（React 投研工作台；frontend/ 为独立 Node 工程）
# 详见 README.md「前端（Web 投研工作台）」与 docs/done/前端平台技术方案.md
# ============================================================
FRONTEND_DIR="$SCRIPT_DIR/frontend"

ensure_pnpm() {
    if command -v pnpm >/dev/null 2>&1; then
        return 0
    fi
    log_warn "未找到 pnpm，尝试通过 corepack 启用..."
    if command -v corepack >/dev/null 2>&1; then
        corepack enable >/dev/null 2>&1 || true
        corepack prepare pnpm@latest --activate >/dev/null 2>&1 || true
        if command -v pnpm >/dev/null 2>&1; then
            log_info "pnpm 已就绪（$(pnpm --version)）"
            return 0
        fi
    fi
    log_error "未找到 pnpm 且 corepack 不可用：请安装 Node.js 22+（自带 corepack）"
    exit 1
}

frontend_install_if_needed() {
    if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
        log_info "安装前端依赖（首次，约 1-2 分钟）..."
        ( cd "$FRONTEND_DIR" && pnpm install )
    fi
}

frontend_dev() {
    ensure_pnpm
    frontend_install_if_needed
    log_info "启动前端开发服务器：http://localhost:5173（/api 代理 → 127.0.0.1:${LIVEPROFIT_API_PORT:-8000}）"
    log_info "后端请先在另一个终端运行：./run.sh platform"
    ( cd "$FRONTEND_DIR" && pnpm dev )
}

frontend_check() {
    ensure_pnpm
    frontend_install_if_needed
    log_info "前端质量检查：typecheck + 单测 + 构建"
    ( cd "$FRONTEND_DIR" && pnpm typecheck && pnpm test && pnpm build )
    log_info "前端质量检查全部通过"
}

frontend_e2e() {
    ensure_pnpm
    frontend_install_if_needed

    # 后端必须已运行（E2E 会触发真实 AI 分析，建议使用受控/假 Worker）
    if ! curl -sf -o /dev/null "http://127.0.0.1:${LIVEPROFIT_API_PORT:-8000}/health/live"; then
        log_error "后端 API 未运行：请先执行 ./run.sh platform"
        log_error "注意：E2E 的「任务闭环」会触发真实 AI 分析（真实 LLM），建议使用受控/假 Worker 环境"
        exit 1
    fi

    local started_dev=""
    if [ -z "$FRONTEND_BASE_URL" ] && ! curl -sf -o /dev/null "http://127.0.0.1:5173"; then
        log_info "dev server 未运行，自动拉起（日志：logs/vite-e2e.log）..."
        ( cd "$FRONTEND_DIR" && nohup pnpm dev > "$SCRIPT_DIR/logs/vite-e2e.log" 2>&1 &
          echo $! > "$SCRIPT_DIR/logs/.vite-e2e.pid" )
        started_dev="1"
        for i in $(seq 1 30); do
            if curl -sf -o /dev/null "http://127.0.0.1:5173"; then
                log_info "dev server 已就绪（$i 秒）"
                break
            fi
            sleep 1
        done
    fi

    local base="${FRONTEND_BASE_URL:-http://127.0.0.1:5173}"
    log_info "运行 Playwright E2E（base: $base）..."
    ( cd "$FRONTEND_DIR" && FRONTEND_BASE_URL="$base" pnpm e2e )

    if [ -n "$started_dev" ] && [ -f "$SCRIPT_DIR/logs/.vite-e2e.pid" ]; then
        kill "$(cat "$SCRIPT_DIR/logs/.vite-e2e.pid")" 2>/dev/null || true
        rm -f "$SCRIPT_DIR/logs/.vite-e2e.pid"
        log_info "已停止临时 dev server"
    fi
}

stack_up() {
    ensure_pnpm
    frontend_install_if_needed
    log_info "构建前端静态产物（frontend/dist）..."
    ( cd "$FRONTEND_DIR" && pnpm build )
    log_info "启动全栈 Compose（infra + API/Worker/Dispatcher + Frontend Nginx）..."
    docker compose --profile app up -d --build
    echo ""
    log_info "全栈已启动，访问：http://127.0.0.1:${FRONTEND_HOST_PORT:-3000}"
    log_info "  （仅 frontend 发布宿主端口；API/PG/Redis/Worker 不暴露宿主机）"
}

# ============================================================
# 命令分发（位于文件末尾：所有函数已定义后再调用）
# ============================================================
COMMAND="${1:-all}"
case "$COMMAND" in
    all)            start_all ;;
    stop)           stop_all ;;
    classic)        run_classic ;;
    platform)       start_platform ;;
    stop-platform)  stop_platform ;;
    frontend-dev)   frontend_dev ;;
    frontend-check) frontend_check ;;
    frontend-e2e)   frontend_e2e ;;
    stack)          stack_up ;;
    ingest-market)  market_ingest ;;
    *)
        echo "未知命令: $COMMAND（支持 all/stop/classic/platform/stop-platform/frontend-dev/frontend-check/frontend-e2e/stack/ingest-market，无参数默认 all）" >&2
        exit 2
        ;;
esac
