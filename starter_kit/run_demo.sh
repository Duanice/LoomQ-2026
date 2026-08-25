#!/usr/bin/env bash
set -Eeuo pipefail

KIT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE="loomq-submission"
PORT="${LOOMQ_PORT:-8000}"
REQUIRED_ENV=(LOOMQ_LLM_BASE_URL LOOMQ_LLM_API_KEY LOOMQ_LLM_MODEL)
CONTAINER_ID=""

fail() {
  printf 'LoomQ 启动失败：%s\n' "$1" >&2
  exit 2
}

platform() {
  case "$(uname -s 2>/dev/null || true)" in
    Darwin) printf 'macos' ;;
    Linux) printf 'linux' ;;
    MINGW*|MSYS*|CYGWIN*) printf 'windows' ;;
    *) printf 'other' ;;
  esac
}

open_url() {
  local url="$1"
  case "$(platform)" in
    macos) open "$url" >/dev/null 2>&1 ;;
    linux) command -v xdg-open >/dev/null 2>&1 && xdg-open "$url" >/dev/null 2>&1 ;;
    windows) cmd.exe /c start "" "$url" >/dev/null 2>&1 ;;
    *) return 1 ;;
  esac
}

show_docker_install_help() {
  local label url answer
  case "$(platform)" in
    macos)
      label="macOS 的 Docker Desktop"
      url="https://docs.docker.com/desktop/setup/install/mac-install/"
      ;;
    linux)
      label="Linux 的 Docker Engine"
      url="https://docs.docker.com/engine/install/"
      ;;
    windows)
      label="Windows 的 Docker Desktop"
      url="https://docs.docker.com/desktop/setup/install/windows-install/"
      ;;
    *)
      label="适合当前系统的 Docker"
      url="https://docs.docker.com/get-started/get-docker/"
      ;;
  esac

  printf '\n没有检测到 Docker。LoomQ 不会擅自安装系统软件。\n'
  printf '请按官方教程安装 %s：\n%s\n' "$label" "$url"
  if [[ -t 0 && -t 1 ]]; then
    printf '现在打开 Docker 官方安装页吗？[y/N] '
    read -r answer
    case "$answer" in
      y|Y|yes|YES) open_url "$url" || printf '无法自动打开浏览器，请手动复制上面的地址。\n' ;;
    esac
  fi
  fail "安装并启动 Docker 后，请重新运行本命令。"
}

wait_for_docker() {
  docker info >/dev/null 2>&1 && return
  if [[ "$(platform)" == "macos" ]]; then
    printf '检测到 Docker 已安装但尚未就绪，正在打开 Docker Desktop…\n'
    open -a Docker >/dev/null 2>&1 \
      || fail "无法打开 Docker Desktop，请手动启动后重试。"
    printf '等待 Docker Desktop 启动'
    local attempt
    for ((attempt=1; attempt<=60; attempt++)); do
      if docker info >/dev/null 2>&1; then
        printf ' 已就绪。\n'
        return
      fi
      printf '.'
      sleep 2
    done
    printf '\n'
    fail "等待 Docker Desktop 超时，请确认它已完成启动。"
  fi

  case "$(platform)" in
    linux) fail "Docker 未运行或当前用户无权限；请启动 Docker 服务后重试。" ;;
    windows) fail "Docker 未运行；请启动 Docker Desktop 后重试。" ;;
    *) fail "Docker 未运行，或当前用户无权访问 Docker。" ;;
  esac
}

cleanup() {
  if [[ -n "$CONTAINER_ID" ]]; then
    docker stop "$CONTAINER_ID" >/dev/null 2>&1 || true
  fi
}

command -v docker >/dev/null 2>&1 || show_docker_install_help
wait_for_docker
[[ "$PORT" =~ ^[0-9]+$ ]] && ((PORT >= 1 && PORT <= 65535)) \
  || fail "LOOMQ_PORT 必须是 1 到 65535 之间的端口号。"

ENV_ARGS=()
ENV_FILE=""
for candidate in "$KIT_DIR/../.env" "$KIT_DIR/.env"; do
  if [[ -f "$candidate" ]]; then
    ENV_FILE="$candidate"
    break
  fi
done

if [[ -n "$ENV_FILE" ]]; then
  ENV_FILE_COMPLETE=true
  for name in "${REQUIRED_ENV[@]}"; do
    if ! grep -Eq "^[[:space:]]*${name}[[:space:]]*=[[:space:]]*.+" "$ENV_FILE"; then
      ENV_FILE_COMPLETE=false
    fi
  done
  if [[ "$ENV_FILE_COMPLETE" == true ]]; then
    ENV_ARGS=(--env-file "$ENV_FILE")
  else
    printf '提示：%s 的模型配置不完整，将忽略该文件。\n' "$(basename "$ENV_FILE")"
  fi
fi

if [[ ${#ENV_ARGS[@]} -eq 0 ]]; then
  SHELL_ENV_COMPLETE=true
  for name in "${REQUIRED_ENV[@]}"; do
    if [[ -z "${!name:-}" ]]; then
      SHELL_ENV_COMPLETE=false
    fi
  done
  if [[ "$SHELL_ENV_COMPLETE" == true ]]; then
    ENV_ARGS=(-e LOOMQ_LLM_BASE_URL -e LOOMQ_LLM_API_KEY -e LOOMQ_LLM_MODEL)
    [[ -z "${LOOMQ_LLM_TIMEOUT_SECONDS:-}" ]] \
      || ENV_ARGS+=(-e LOOMQ_LLM_TIMEOUT_SECONDS)
  else
    printf '\n未配置 LOOMQ_LLM_*：将以离线体验模式启动。\n'
    printf '入门教程和 LAUNCH DEMO 可直接使用；调用 Agent 时页面才会提示配置模型。\n'
  fi
fi

cd "$KIT_DIR"
docker build --platform linux/amd64 -t "$IMAGE" .

start_container() {
  local candidate="$1"
  if [[ ${#ENV_ARGS[@]} -gt 0 ]]; then
    docker run --rm -d --platform linux/amd64 \
      -p "127.0.0.1:${candidate}:8000" \
      "${ENV_ARGS[@]}" \
      "$IMAGE" \
      python -m agent.server --host 0.0.0.0 --port 8000
  else
    docker run --rm -d --platform linux/amd64 \
      -p "127.0.0.1:${candidate}:8000" \
      "$IMAGE" \
      python -m agent.server --host 0.0.0.0 --port 8000
  fi
}

wait_for_http() {
  local attempt
  printf '等待 Web 页面就绪'
  for ((attempt=1; attempt<=30; attempt++)); do
    if docker exec "$CONTAINER_ID" python -c \
      'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/", timeout=1).read(1)' \
      >/dev/null 2>&1; then
      printf ' 已就绪。\n'
      return
    fi
    if [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER_ID" 2>/dev/null || true)" != "true" ]]; then
      docker logs "$CONTAINER_ID" 2>&1 || true
      fail "Web 容器在启动过程中退出。"
    fi
    printf '.'
    sleep 1
  done
  fail "Web 页面在 30 秒内没有就绪。"
}

REQUESTED_PORT="$PORT"
LAST_PORT=$((REQUESTED_PORT + 99))
((LAST_PORT <= 65535)) || LAST_PORT=65535
RUN_OUTPUT=""
for ((candidate=REQUESTED_PORT; candidate<=LAST_PORT; candidate++)); do
  if RUN_OUTPUT="$(start_container "$candidate" 2>&1)"; then
    CONTAINER_ID="${RUN_OUTPUT##*$'\n'}"
    PORT="$candidate"
    break
  fi
  case "$RUN_OUTPUT" in
    *"port is already allocated"*|*"address already in use"*|*"ports are not available"*) continue ;;
    *) fail "$RUN_OUTPUT" ;;
  esac
done
[[ -n "$CONTAINER_ID" ]] \
  || fail "从 $REQUESTED_PORT 开始的 100 个端口均被占用。"

trap cleanup EXIT
trap 'exit 130' INT TERM
wait_for_http
if [[ "$PORT" != "$REQUESTED_PORT" ]]; then
  printf '端口 %s 已被占用，自动改用 %s。\n' "$REQUESTED_PORT" "$PORT"
fi

URL="http://127.0.0.1:${PORT}"
printf '\nLoomQ 已启动：%s\n按 Ctrl+C 停止。\n\n' "$URL"
open_url "$URL" || true
docker logs -f "$CONTAINER_ID"
