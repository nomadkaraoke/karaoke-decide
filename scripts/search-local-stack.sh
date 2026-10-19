#!/bin/bash
# decide "search local stack": the search service (backend/search) on a small fixture
# index, for the mobile app's Maestro E2E flows (run together with gen's local stack).
#
# Usage:
#   scripts/search-local-stack.sh up       rebuild the fixture index + start on :8020
#   scripts/search-local-stack.sh down     stop it
#   scripts/search-local-stack.sh status   show health / ready
#   scripts/search-local-stack.sh logs     tail the server log
#
# Env overrides: SEARCH_LOCAL_PORT (8020), GEN_API_URL (http://127.0.0.1:8010),
# DECIDE_GEN_PARTNER_SECRET (local-stack-secret), SEARCH_LOCAL_PUBLIC_URL
# (http://127.0.0.1:$PORT; set to http://10.0.2.2:$PORT for an Android emulator).
# /resolve only works if gen's local stack was started with the same
# DECIDE_GEN_PARTNER_SECRET. See docs/TESTING.md § "Search local stack".
# bash 3.2 compatible (macOS).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="$PROJECT_ROOT/.local-stack"
INDEX_DIR="$STATE_DIR/song-index"
PID_FILE="$STATE_DIR/search.pid"
LOG_FILE="$STATE_DIR/search.log"
PORT="${SEARCH_LOCAL_PORT:-8020}"
BASE="http://127.0.0.1:$PORT/api/search"
PUBLIC_URL="${SEARCH_LOCAL_PUBLIC_URL:-http://127.0.0.1:$PORT}"
mkdir -p "$STATE_DIR"

# Poetry when available, else whatever python/uvicorn is on PATH (CI, conda).
if command -v poetry >/dev/null 2>&1; then RUN=(poetry run); else RUN=(); fi

is_healthy() { curl -sf --max-time 2 "$BASE/health" >/dev/null 2>&1; }
is_ready() { curl -sf --max-time 2 "$BASE/ready" >/dev/null 2>&1; }
# True only if the pid file names a live process that is OUR server (a recycled pid
# belongs to something else); a stale pid file is discarded.
pid_alive() {
    [ -f "$PID_FILE" ] || return 1
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null \
        && ps -p "$pid" -o command= 2>/dev/null | grep -q "backend.search.app"; then
        return 0
    fi
    rm -f "$PID_FILE"
    return 1
}

cmd_up() {
    if is_healthy; then
        echo "search already up on :$PORT"
        return 0
    fi
    if pid_alive; then
        echo "search process $(cat "$PID_FILE") is alive but not healthy; run 'down' first" >&2
        return 1
    fi
    if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "port $PORT is held by another process (not our search server):" >&2
        lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2 || true
        return 1
    fi
    echo "Building fixture index into $INDEX_DIR"
    (cd "$PROJECT_ROOT" && ${RUN[@]+"${RUN[@]}"} python -m karaoke_decide.song_search.fixtures build "$INDEX_DIR")

    echo "Starting search on :$PORT"
    (
        cd "$PROJECT_ROOT"
        export SONG_INDEX_LOCAL_DIR="$INDEX_DIR"
        export SONG_INDEX_OFFLINE_URL_TEMPLATE="$PUBLIC_URL/api/search/_local/offline.db.gz"
        export GEN_API_URL="${GEN_API_URL:-http://127.0.0.1:8010}"
        export DECIDE_GEN_PARTNER_SECRET="${DECIDE_GEN_PARTNER_SECRET:-local-stack-secret}"
        nohup ${RUN[@]+"${RUN[@]}"} uvicorn backend.search.app:search_app --host 127.0.0.1 --port "$PORT" \
            > "$LOG_FILE" 2>&1 &
        echo $! > "$PID_FILE"
    )
    local i=0
    while [ "$i" -lt "${SEARCH_LOCAL_READY_TRIES:-60}" ]; do
        if ! pid_alive; then break; fi
        if is_ready; then
            echo "search ready: $BASE (GEN_API_URL=${GEN_API_URL:-http://127.0.0.1:8010})"
            return 0
        fi
        sleep 1
        i=$((i + 1))
    done
    echo "search did not become ready; last log lines:" >&2
    tail -40 "$LOG_FILE" >&2 || true
    cmd_down >&2  # never leave a half-started server behind
    return 1
}

cmd_down() {
    if pid_alive; then
        local pid
        pid="$(cat "$PID_FILE")"
        # poetry run / nohup may leave a child: stop the whole group's children first.
        pkill -P "$pid" 2>/dev/null || true
        kill "$pid" 2>/dev/null || true
        local i=0
        while [ "$i" -lt 10 ] && kill -0 "$pid" 2>/dev/null; do sleep 1; i=$((i + 1)); done
        kill -9 "$pid" 2>/dev/null || true
        echo "search stopped"
    else
        echo "search not running"
    fi
    rm -f "$PID_FILE"
}

cmd_status() {
    if pid_alive; then echo "pid:    $(cat "$PID_FILE")"; else echo "pid:    none"; fi
    if is_healthy; then echo "health: ok ($BASE/health)"; else echo "health: DOWN"; fi
    if is_ready; then echo "ready:  ok"; else echo "ready:  NOT READY"; fi
    if is_ready; then return 0; else return 1; fi
}

case "${1:-}" in
    up) cmd_up ;;
    down) cmd_down ;;
    status) cmd_status ;;
    logs) tail -n 100 -f "$LOG_FILE" ;;
    *) echo "usage: $0 up|down|status|logs" >&2; exit 2 ;;
esac
