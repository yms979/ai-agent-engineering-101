#!/usr/bin/env bash
# Watches for what Round 3 depends on and writes it to logs/round-watch.log (cron, every 15 min).
#
#   watch_round.sh          one check (what cron runs)
#   watch_round.sh install  add the cron line        watch_round.sh remove   remove it
#
# Each check
#   - upstream main (the course repo): new commits are fetched and listed; lines marked !! when they touch
#     weeks/week-08/, scripts/check_week08.py or the kit (a KIT_VERSION change needs a kit restart),
#   - the market round (/api/board "current"): on a change, saves the board of the round that just ended
#     to logs/board-round<N>-final-<time>.json, the "before" side of the price analysis.
# Only the remote-tracking ref upstream/main is updated; the working tree is never touched.
set -u

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
MARKET=https://market.ourolabs.ai
STATE=$HERE/.market/watch.state               # gitignored: "<upstream hash> <round>"
LOG=$HERE/logs/round-watch.log
CRON_LINE="*/15 * * * * $HERE/watch_round.sh >/dev/null 2>&1"

say() { echo "$(date '+%F %T') $*" >> "$LOG"; }

case "${1:-}" in
    install) ( crontab -l 2>/dev/null | grep -vF "$HERE/watch_round.sh"; echo "$CRON_LINE" ) | crontab -
             echo "cron: every 15 min"; exit 0 ;;
    remove)  ( crontab -l 2>/dev/null | grep -vF "$HERE/watch_round.sh" ) | crontab -; echo "removed"; exit 0 ;;
esac

mkdir -p "$HERE/.market" "$HERE/logs"
read -r old_hash old_round 2>/dev/null < "$STATE" || { old_hash=""; old_round=""; }

hash=$(git -C "$REPO" ls-remote upstream refs/heads/main 2>/dev/null | cut -f1)
if [ -n "$hash" ] && [ "$hash" != "$old_hash" ]; then
    git -C "$REPO" fetch -q upstream main 2>/dev/null
    if [ -n "$old_hash" ]; then
        say "upstream main moved: $(git -C "$REPO" log --oneline "$old_hash..$hash" | tr '\n' ';')"
        files=$(git -C "$REPO" diff --name-only "$old_hash" "$hash")
        echo "$files" | grep -qE '^weeks/week-08/' && say "!! weeks/week-08/ changed (assignment spec): $(echo "$files" | grep '^weeks/week-08/' | tr '\n' ' ')"
        echo "$files" | grep -qE '^scripts/check_week08\.py$' && say "!! scripts/check_week08.py changed (CI check)"
        if echo "$files" | grep -qE '^scripts/market_kit\.py$'; then
            say "!! the kit changed, $(git -C "$REPO" show "$hash:scripts/market_kit.py" | grep -m1 '^KIT_VERSION'); merge upstream/main and restart the kit"
        fi
    else
        say "watching upstream main from $hash"
    fi
fi

round=$(curl -fsS -m 20 "$MARKET/api/board" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["current"])' 2>/dev/null)
if [ -n "$round" ] && [ "$round" != "$old_round" ]; then
    if [ -n "$old_round" ]; then
        snap=$HERE/logs/board-round$old_round-final-$(date '+%Y%m%d-%H%M').json
        curl -fsS -m 30 "$MARKET/api/board?round=$old_round" -o "$snap" 2>/dev/null
        say "!! market round $old_round -> $round; board of round $old_round saved to logs/$(basename "$snap")"
    else
        say "watching the market from round $round"
    fi
fi

echo "${hash:-$old_hash} ${round:-$old_round}" > "$STATE"
