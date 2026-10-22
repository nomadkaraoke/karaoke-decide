#!/bin/bash
# Thin wrapper kept for existing callers: the search service alone on :8020
# (usage: up|down|status|logs). The full stack is scripts/local-stack.sh.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/local-stack.sh" "$@" --search-only
