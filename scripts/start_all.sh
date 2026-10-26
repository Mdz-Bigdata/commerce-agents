#!/usr/bin/env bash
# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0
#
# Set up the repository and start every vertical: each API, storefront, and merchant portal.
#
#   scripts/start_all.sh                       # all four verticals: APIs :8000-8003,
#                                              # storefronts :3000-3003, portals :3100-3103
#   scripts/start_all.sh retail travel         # only the verticals named
#   scripts/start_all.sh --prod --fresh-memory # other flags pass through to scripts/run_demo.py
#   scripts/start_all.sh --turbopack           # bundle with Turbopack instead of webpack
#
# Runs from any directory. Creates .venv (Python 3.11+) and installs requirements.txt when a
# package is missing, copies .env.example to .env when there is none, and runs `npm ci` in
# examples/ when the workspace is not installed. Each vertical then runs under
# scripts/run_demo.py --all on an API port assigned here: its own when free (or already
# running this vertical), else the next free one from :8010. Ctrl-C stops everything.
# The web apps bundle with webpack by default: Turbopack cannot load next/font/google files
# that Google Fonts serves from /l/font?kit=... URLs.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ALL_VERTICALS=(retail travel telecom entertainment)
GREEN=$'\033[32m' YELLOW=$'\033[33m' RED=$'\033[31m' DIM=$'\033[2m' RESET=$'\033[0m'

usage() { sed -n '5,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
step() { echo "${GREEN}==>${RESET} $*"; }
warn() { echo "${YELLOW}$*${RESET}" >&2; }
die() { echo "${RED}error:${RESET} $*" >&2; exit 1; }

VERTICALS=()
DEMO_FLAGS=()
BUNDLER=(--webpack)
REUSE=1
for arg in "$@"; do
  case "$arg" in
    -h|--help) usage; exit 0 ;;
    --turbopack) BUNDLER=() ;;
    --no-reuse|--fresh-memory) REUSE=0; DEMO_FLAGS+=("$arg") ;;
    --merchant|--all|--api-only|--web-only|--api-port|--api-port=*|--no-install)
      die "$arg is set per vertical by this script; use scripts/run_demo.py for it" ;;
    -*) DEMO_FLAGS+=("$arg") ;;
    *)
      [[ " ${ALL_VERTICALS[*]} " == *" $arg "* ]] \
        || die "unknown vertical: $arg (one of: ${ALL_VERTICALS[*]})"
      VERTICALS+=("$arg") ;;
  esac
done
[[ ${#VERTICALS[@]} -gt 0 ]] || VERTICALS=("${ALL_VERTICALS[@]}")

cd "$REPO_ROOT"

# Python: the repo's .venv, created from the first interpreter that is 3.11 or newer.
step "Python environment"
PYTHON="$REPO_ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  BASE=""
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 \
      && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
      BASE="$candidate"
      break
    fi
  done
  [[ -n "$BASE" ]] || die "Python 3.11+ is required"
  echo "  creating .venv with $("$BASE" --version)"
  "$BASE" -m venv "$REPO_ROOT/.venv"
fi
MODULES="commerce_common, shopping_agent, shopping_agent_runtime, merchant_agent"
MODULES+=", merchant_agent_runtime, fastapi, uvicorn, dotenv"
if ! "$PYTHON" -c "import $MODULES" 2>/dev/null; then
  echo "  installing requirements.txt"
  "$PYTHON" -m pip install -r requirements.txt
fi
echo "  ${DIM}$("$PYTHON" --version) at .venv${RESET}"

# Credentials: the example APIs read the repo-root .env.
step "Credentials"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "  created .env from .env.example"
fi
# The provider and key the APIs will use, resolved the way they resolve them.
PROVIDER_STATUS="$("$PYTHON" - <<'PY'
import os, sys
sys.path.insert(0, "examples")
from dotenv import load_dotenv
load_dotenv(".env", override=False)
from demo_common.model_provider import resolve_provider
try:
    p = resolve_provider()
except ValueError as error:
    print(f"error|{error}")
else:
    print(f"{'ok' if p.has_key() else 'nokey'}|{p.label}|{p.model or 'default model'}|{p.key_hint}")
PY
)"
IFS='|' read -r KEY_STATE PROVIDER_LABEL PROVIDER_MODEL KEY_HINT <<<"$PROVIDER_STATUS"
case "$KEY_STATE" in
  ok) echo "  ${DIM}$PROVIDER_LABEL, $PROVIDER_MODEL; key set${RESET}" ;;
  nokey)
    warn "  No API key for $PROVIDER_LABEL: browsing and /showcase work, chat returns an error event."
    warn "  Set $KEY_HINT in $REPO_ROOT/.env (MODEL_PROVIDER picks the model) and rerun." ;;
  *) die "model settings in .env: $PROVIDER_LABEL" ;;
esac

# Node: the eight web apps share one npm workspace at examples/.
step "Web workspace"
command -v node >/dev/null 2>&1 || die "Node 22 is required"
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[[ "$NODE_MAJOR" -ge 22 ]] || warn "  Node $(node --version) found; the web apps are built for Node 22."
if [[ ! -x examples/node_modules/.bin/next ]]; then
  echo "  running npm ci in examples/"
  (cd examples && npm ci --no-audit --no-fund)
fi
echo "  ${DIM}node $(node --version)${RESET}"

# API ports are assigned here so the verticals do not race for the same free port.
default_port() {
  case "$1" in retail) echo 8000 ;; travel) echo 8001 ;; telecom) echo 8002 ;; entertainment) echo 8003 ;; esac
}
store_name() {
  case "$1" in retail) echo "ACME" ;; travel) echo "ACME Travel" ;; telecom) echo "ACME Mobile" ;; entertainment) echo "ACME Tickets" ;; esac
}
port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
serves_store() {
  curl -s -m 2 "http://localhost:$1/api/health" 2>/dev/null \
    | "$PYTHON" -c 'import json, sys; sys.exit(0 if json.load(sys.stdin).get("store") == sys.argv[1] else 1)' "$2" 2>/dev/null
}
step "API ports"
TAKEN=" "
NEXT_PORT=8010
API_PORTS=()
for vertical in "${VERTICALS[@]}"; do
  port="$(default_port "$vertical")"
  if port_busy "$port" && ! { [[ $REUSE -eq 1 ]] && serves_store "$port" "$(store_name "$vertical")"; }; then
    while port_busy "$NEXT_PORT" || [[ "$TAKEN" == *" $NEXT_PORT "* ]]; do NEXT_PORT=$((NEXT_PORT + 1)); done
    warn "  :$port is in use by another program; the $vertical API takes :$NEXT_PORT."
    port="$NEXT_PORT"
  fi
  TAKEN+="$port "
  API_PORTS+=("$port")
  echo "  ${DIM}$vertical :$port${RESET}"
done

# Start each vertical under run_demo.py, which starts its web apps and stops its own tree.
PIDS=()
stop_all() {
  trap - INT TERM EXIT
  [[ ${#PIDS[@]} -gt 0 ]] || return 0
  echo
  step "Stopping ${#PIDS[@]} vertical(s)"
  for pid in "${PIDS[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap stop_all EXIT
trap 'exit 130' INT TERM

step "Starting ${VERTICALS[*]}"
for i in "${!VERTICALS[@]}"; do
  "$PYTHON" scripts/run_demo.py "${VERTICALS[$i]}" --all --no-install --api-port "${API_PORTS[$i]}" \
    ${BUNDLER[@]+"${BUNDLER[@]}"} ${DEMO_FLAGS[@]+"${DEMO_FLAGS[@]}"} &
  PIDS+=("$!")
done

echo "${DIM}Each vertical prints its URLs when it is up; Ctrl-C stops everything.${RESET}"
# A vertical that fails stops the rest; one that exits cleanly (everything reused) is dropped.
while [[ ${#PIDS[@]} -gt 0 ]]; do
  RUNNING=()
  for pid in "${PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      RUNNING+=("$pid")
      continue
    fi
    status=0
    wait "$pid" || status=$?
    if [[ $status -ne 0 ]]; then
      warn "A vertical exited with code $status; stopping the rest."
      exit "$status"
    fi
  done
  PIDS=("${RUNNING[@]+"${RUNNING[@]}"}")
  sleep 1
done
