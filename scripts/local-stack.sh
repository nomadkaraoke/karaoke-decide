#!/bin/bash
# decide "local stack": the main API (:8030) and the search service (:8020) on fixture data,
# for the mobile app's Maestro E2E flows. The main API shares gen's local-stack Firestore
# emulator (127.0.0.1:8080, project test-project), so gen sessions minted by gen's local
# stack (:8010) validate here. No BigQuery, no GCS: the catalog and song index are built
# from local fixtures.
#
# Usage:
#   scripts/local-stack.sh up [--search-only]   build fixtures + start (idempotent)
#   scripts/local-stack.sh down [--search-only] stop
#   scripts/local-stack.sh status [--search-only]
#   scripts/local-stack.sh logs [api|search]    tail a log (default api)
#   scripts/local-stack.sh env                  print the mobile app's EXPO_PUBLIC_* lines
#
# --search-only runs just the search service (no Firestore, no catalog); that is what
# scripts/search-local-stack.sh does. Start gen's local stack first (karaoke-gen:
# DECIDE_GEN_PARTNER_SECRET=local-stack-secret scripts/local-stack.sh up); if nothing
# listens on :8080 the main API's `up` starts scripts/start-emulators.sh itself.
#
# Env overrides: DECIDE_LOCAL_PORT (8030), SEARCH_LOCAL_PORT (8020),
# GEN_API_URL (http://127.0.0.1:8010; must be loopback),
# LOCAL_STACK_PARTNER_SECRET (local-stack-secret; the caller's DECIDE_GEN_PARTNER_SECRET is ignored),
# SEARCH_LOCAL_PUBLIC_URL (http://127.0.0.1:$SEARCH_PORT; http://10.0.2.2:... for Android),
# DECIDE_LOCAL_PUBLIC_URL (likewise for the main API, used by `env`).
# See docs/TESTING.md § "Local stack". bash 3.2 compatible (macOS).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="$PROJECT_ROOT/.local-stack"
INDEX_DIR="$STATE_DIR/song-index"
CATALOG_DIR="$STATE_DIR/catalog"
API_PID="$STATE_DIR/api.pid"
API_LOG="$STATE_DIR/api.log"
SEARCH_PID="$STATE_DIR/search.pid"
SEARCH_LOG="$STATE_DIR/search.log"
API_PORT="${DECIDE_LOCAL_PORT:-8030}"
SEARCH_PORT="${SEARCH_LOCAL_PORT:-8020}"
API_BASE="http://127.0.0.1:$API_PORT/api"
SEARCH_BASE="http://127.0.0.1:$SEARCH_PORT/api/search"
SEARCH_PUBLIC_URL="${SEARCH_LOCAL_PUBLIC_URL:-http://127.0.0.1:$SEARCH_PORT}"
API_PUBLIC_URL="${DECIDE_LOCAL_PUBLIC_URL:-http://127.0.0.1:$API_PORT}"
GEN_API_URL="${GEN_API_URL:-http://127.0.0.1:8010}"
# Never inherit DECIDE_GEN_PARTNER_SECRET from the caller's environment (it may be a real one):
# only an explicit LOCAL_STACK_PARTNER_SECRET overrides the fixed local value.
PARTNER_SECRET="${LOCAL_STACK_PARTNER_SECRET:-local-stack-secret}"
case "$GEN_API_URL" in
    http://127.0.0.1|http://127.0.0.1:*|http://localhost|http://localhost:*|http://10.0.2.2|http://10.0.2.2:*) ;;
    *) echo "refusing GEN_API_URL=$GEN_API_URL: the local stack only talks to a loopback gen (127.0.0.1, localhost, 10.0.2.2)" >&2; exit 2 ;;
esac
READY_TRIES="${DECIDE_LOCAL_READY_TRIES:-${SEARCH_LOCAL_READY_TRIES:-60}}"
mkdir -p "$STATE_DIR"

CMD="${1:-}"
[ "$#" -gt 0 ] && shift
SEARCH_ONLY=0
LOG_TARGET=api
for arg in "$@"; do
    case "$arg" in
        --search-only) SEARCH_ONLY=1; LOG_TARGET=search ;;
        api|search) LOG_TARGET="$arg" ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

# Poetry when available, else whatever python/uvicorn is on PATH (CI, conda).
if command -v poetry >/dev/null 2>&1; then RUN=(poetry run); else RUN=(); fi

search_healthy() { curl -sf --max-time 2 "$SEARCH_BASE/health" >/dev/null 2>&1; }
search_ready() { curl -sf --max-time 2 "$SEARCH_BASE/ready" >/dev/null 2>&1; }
api_healthy() { curl -sf --max-time 2 "$API_BASE/health" >/dev/null 2>&1; }
api_catalog_ready() { curl -sf --max-time 2 "$API_BASE/ready" 2>/dev/null | grep -Eq '"catalog": ?true'; }

# True only if the pid file names a live process that is OUR server (a recycled pid belongs
# to something else); a stale pid file is discarded. $1 = pid file, $2 = command marker.
pid_alive() {
    [ -f "$1" ] || return 1
    local pid
    pid="$(cat "$1" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null \
        && ps -p "$pid" -o command= 2>/dev/null | grep -q "$2"; then
        return 0
    fi
    rm -f "$1"
    return 1
}
search_alive() { pid_alive "$SEARCH_PID" "backend.search.app"; }
api_alive() { pid_alive "$API_PID" "backend.main:app"; }

port_held() {
    command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

stop_server() { # $1 = name, $2 = pid file, $3 = alive fn
    if "$3"; then
        local pid
        pid="$(cat "$2")"
        # poetry run / nohup may leave a child: stop the children first.
        pkill -P "$pid" 2>/dev/null || true
        kill "$pid" 2>/dev/null || true
        local i=0
        while [ "$i" -lt 10 ] && kill -0 "$pid" 2>/dev/null; do sleep 1; i=$((i + 1)); done
        kill -9 "$pid" 2>/dev/null || true
        echo "$1 stopped"
    else
        echo "$1 not running"
    fi
    rm -f "$2"
}

up_search() {
    if search_healthy; then
        if search_alive; then echo "search already up on :$SEARCH_PORT"; return 0; fi
        echo "port $SEARCH_PORT is healthy but held by a process we did not start" >&2
        return 1
    fi
    if search_alive; then
        echo "search process $(cat "$SEARCH_PID") is alive but not healthy; run 'down' first" >&2
        return 1
    fi
    if port_held "$SEARCH_PORT"; then
        echo "port $SEARCH_PORT is held by another process (not our search server):" >&2
        lsof -nP -iTCP:"$SEARCH_PORT" -sTCP:LISTEN >&2 || true
        return 1
    fi
    echo "Building fixture index into $INDEX_DIR"
    (cd "$PROJECT_ROOT" && ${RUN[@]+"${RUN[@]}"} python -m karaoke_decide.song_search.fixtures build "$INDEX_DIR") || return 1
    echo "Starting search on :$SEARCH_PORT"
    (
        cd "$PROJECT_ROOT"
        export SONG_INDEX_LOCAL_DIR="$INDEX_DIR"
        export SONG_INDEX_OFFLINE_URL_TEMPLATE="$SEARCH_PUBLIC_URL/api/search/_local/offline.db.gz"
        export GEN_API_URL DECIDE_GEN_PARTNER_SECRET="$PARTNER_SECRET"
        nohup ${RUN[@]+"${RUN[@]}"} uvicorn backend.search.app:search_app --host 127.0.0.1 --port "$SEARCH_PORT" \
            > "$SEARCH_LOG" 2>&1 &
        echo $! > "$SEARCH_PID"
    ) || return 1
    local i=0
    while [ "$i" -lt "$READY_TRIES" ]; do
        search_alive || break
        if search_ready; then
            echo "search ready: $SEARCH_BASE (GEN_API_URL=$GEN_API_URL)"
            return 0
        fi
        sleep 1
        i=$((i + 1))
    done
    echo "search did not become ready; last log lines:" >&2
    tail -40 "$SEARCH_LOG" >&2 || true
    stop_server search "$SEARCH_PID" search_alive >&2
    return 1
}

ensure_firestore_emulator() {
    if (exec 3<>/dev/tcp/127.0.0.1/8080) 2>/dev/null; then
        echo "Firestore emulator already listening on 127.0.0.1:8080 (gen's local stack)"
        return 0
    fi
    echo "Nothing on 127.0.0.1:8080: starting emulators via scripts/start-emulators.sh"
    "$SCRIPT_DIR/start-emulators.sh" || return 1
    (exec 3<>/dev/tcp/127.0.0.1/8080) 2>/dev/null || { echo "Firestore emulator still not listening on :8080" >&2; return 1; }
}

up_api() {
    if api_healthy; then
        if api_alive; then echo "api already up on :$API_PORT"; return 0; fi
        echo "port $API_PORT is healthy but held by a process we did not start" >&2
        return 1
    fi
    if api_alive; then
        echo "api process $(cat "$API_PID") is alive but not healthy; run 'down' first" >&2
        return 1
    fi
    if port_held "$API_PORT"; then
        echo "port $API_PORT is held by another process (not our api server):" >&2
        lsof -nP -iTCP:"$API_PORT" -sTCP:LISTEN >&2 || true
        return 1
    fi
    ensure_firestore_emulator || return 1
    echo "Building fixture catalog into $CATALOG_DIR"
    (cd "$PROJECT_ROOT" && ${RUN[@]+"${RUN[@]}"} python -m karaoke_decide.catalog.fixtures build "$CATALOG_DIR") || return 1
    echo "Starting api on :$API_PORT"
    (
        cd "$PROJECT_ROOT"
        export FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 GOOGLE_CLOUD_PROJECT=test-project
        export ENVIRONMENT=development JWT_SECRET=local-stack-jwt
        export DECIDE_TEST_API=1 BIGQUERY_DISABLED=1
        export CATALOG_LOCAL_DIR="$CATALOG_DIR" SONG_INDEX_LOCAL_DIR="$INDEX_DIR"
        export GEN_API_URL DECIDE_GEN_PARTNER_SECRET="$PARTNER_SECRET"
        nohup ${RUN[@]+"${RUN[@]}"} uvicorn backend.main:app --host 127.0.0.1 --port "$API_PORT" \
            > "$API_LOG" 2>&1 &
        echo $! > "$API_PID"
    ) || return 1
    local i=0
    while [ "$i" -lt "$READY_TRIES" ]; do
        api_alive || break
        if api_catalog_ready; then
            echo "api ready (catalog loaded): $API_BASE"
            return 0
        fi
        sleep 1
        i=$((i + 1))
    done
    echo "api did not become ready (catalog:true); last log lines:" >&2
    tail -40 "$API_LOG" >&2 || true
    stop_server api "$API_PID" api_alive >&2
    return 1
}

cmd_up() {
    if [ "$SEARCH_ONLY" = 1 ]; then up_search; return; fi
    # The api's availability.db comes from the fixture index, so search builds it first.
    up_search
    up_api || { stop_server search "$SEARCH_PID" search_alive >&2; return 1; }
}

cmd_down() {
    if [ "$SEARCH_ONLY" = 0 ]; then stop_server api "$API_PID" api_alive; fi
    stop_server search "$SEARCH_PID" search_alive
    if [ "$SEARCH_ONLY" = 0 ]; then
        echo "note: the docker Firestore/GCS emulators (gen's stack) are left running; stop them with gen's scripts/local-stack.sh down"
    fi
}

cmd_status() {
    local rc=0
    if search_alive; then echo "search pid:    $(cat "$SEARCH_PID")"; else echo "search pid:    none"; fi
    if search_healthy; then echo "search health: ok ($SEARCH_BASE/health)"; else echo "search health: DOWN"; fi
    if search_ready; then echo "search ready:  ok"; else echo "search ready:  NOT READY"; rc=1; fi
    if [ "$SEARCH_ONLY" = 0 ]; then
        if api_alive; then echo "api pid:       $(cat "$API_PID")"; else echo "api pid:       none"; fi
        if api_healthy; then echo "api health:    ok ($API_BASE/health)"; else echo "api health:    DOWN"; fi
        if api_catalog_ready; then echo "api ready:     ok (catalog loaded)"; else echo "api ready:     NOT READY"; rc=1; fi
    fi
    return $rc
}

case "$CMD" in
    up) cmd_up ;;
    down) cmd_down ;;
    status) cmd_status ;;
    logs)
        if [ "$LOG_TARGET" = search ]; then LF="$SEARCH_LOG"; else LF="$API_LOG"; fi
        if [ -f "$LF" ]; then tail -n 100 -f "$LF"; else echo "no log yet: $LF (run 'up' first)" >&2; exit 1; fi
        ;;
    env)
        echo "EXPO_PUBLIC_DECIDE_API_URL=$API_PUBLIC_URL"
        echo "EXPO_PUBLIC_DECIDE_SEARCH_URL=$SEARCH_PUBLIC_URL"
        ;;
    *) echo "usage: $0 up|down|status|logs|env [--search-only]" >&2; exit 2 ;;
esac
