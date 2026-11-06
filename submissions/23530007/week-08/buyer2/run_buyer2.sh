#!/usr/bin/env bash
# Second buyer of the same GitHub owner (user, 11-06: "start a new buyer"): same code as week-08's buyer, its own
# name, key, memory and post state, so its saved rate starts without the 3,400 deals made before patch 3.4.
# The shop is not touched (--buyer-only). Restarts the kit 30 s after it exits. Claim code from the same file as
# run_market.sh (outside git).
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
W=$(cd "$HERE/.." && pwd)
export PATH="$W/.venv/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
OWNER_FILE=$HOME/.config/ai-agent-market/owner
NAME=${BUYER2_NAME:-yms979-buyer}
export BUYER_MEMORY=$HERE/buyer_memory.json REVIEW_STATE=$HERE/review_state.json VOTE_STATE=$HERE/vote_state.json \
       BUYER_KEY=$HERE/buyer_key.pem NO_VOTES=1
mkdir -p "$HERE/logs"
while true; do
    MARKET_OWNER=$(tr -d '[:space:]' < "$OWNER_FILE") \
        python "$W/market_kit.py" join --dir "$HERE" --market https://market.ourolabs.ai --name "$NAME" --buyer-only \
        >> "$HERE/logs/supervisor.out" 2>&1
    echo "$(date -u '+%F %T') kit exited ($?), restarting in 30 s" >> "$HERE/logs/supervisor.out"
    sleep 30
done
