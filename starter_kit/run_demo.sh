#!/usr/bin/env bash
set -Eeuo pipefail

KIT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE="loomq-submission"
PORT="${LOOMQ_PORT:-8000}"
REQUIRED_ENV=(LOOMQ_LLM_BASE_URL LOOMQ_LLM_API_KEY LOOMQ_LLM_MODEL)

fail() {
  printf 'LoomQ 启动失败：%s\n' "$1" >&2
  exit 2
}

command -v docker >/dev/null 2>&1 || fail "未找到 Docker，请先安装并启动 Docker。"
command -v python3 >/dev/null 2>&1 || fail "未找到 Python 3。"
docker info >/dev/null 2>&1 || fail "Docker 尚未运行，或当前用户无权访问 Docker。"
[[ "$PORT" =~ ^[0-9]+$ ]] && ((PORT >= 1 && PORT <= 65535)) \
  || fail "LOOMQ_PORT 必须是 1 到 65535 之间的端口号。"

REQUESTED_PORT="$PORT"
PORT="$(python3 - "$PORT" <<'PY'
import socket
import sys

start = int(sys.argv[1])
for port in range(start, min(start + 100, 65536)):
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            continue
    print(port)
    raise SystemExit(0)
raise SystemExit(1)
PY
)" || fail "从 $REQUESTED_PORT 开始的 100 个端口均被占用。"
if [[ "$PORT" != "$REQUESTED_PORT" ]]; then
  printf '端口 %s 已被占用，自动改用 %s。\n' "$REQUESTED_PORT" "$PORT"
fi

ENV_FILE=""
for candidate in "$KIT_DIR/../.env" "$KIT_DIR/.env"; do
  if [[ -f "$candidate" ]]; then
    ENV_FILE="$candidate"
    break
  fi
done

if [[ -n "$ENV_FILE" ]]; then
  for name in "${REQUIRED_ENV[@]}"; do
    grep -Eq "^[[:space:]]*${name}[[:space:]]*=[[:space:]]*.+" "$ENV_FILE" \
      || fail "$(basename "$ENV_FILE") 缺少 $name。"
  done
  ENV_ARGS=(--env-file "$ENV_FILE")
else
  for name in "${REQUIRED_ENV[@]}"; do
    [[ -n "${!name:-}" ]] || fail "缺少 $name；请导出环境变量或在仓库根目录创建 .env。"
  done
  ENV_ARGS=(-e LOOMQ_LLM_BASE_URL -e LOOMQ_LLM_API_KEY -e LOOMQ_LLM_MODEL)
  [[ -z "${LOOMQ_LLM_TIMEOUT_SECONDS:-}" ]] \
    || ENV_ARGS+=(-e LOOMQ_LLM_TIMEOUT_SECONDS)
fi

cd "$KIT_DIR"
docker build --platform linux/amd64 -t "$IMAGE" .

printf '\nLoomQ 已启动：http://127.0.0.1:%s\n按 Ctrl+C 停止。\n\n' "$PORT"
docker run --rm --platform linux/amd64 \
  -p "127.0.0.1:${PORT}:8000" \
  "${ENV_ARGS[@]}" \
  "$IMAGE" \
  python -m agent.server --host 0.0.0.0 --port 8000
