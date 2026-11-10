"""How the agents' `claude -p` calls authenticate (user 11-08: use my API key).

The key lives outside git, in ~/.config/ai-agent-market/anthropic_api_key (mode 600); it is never read into
logs or commits. When the file is there a call runs with ANTHROPIC_API_KEY set from it; if that call fails
(non-zero exit or no answer, e.g. the key was revoked or hit its limit) it runs once more on the CLI login,
which is what every call used before. Without the file nothing changes.
"""
import os
import subprocess
from pathlib import Path

KEY_FILE = Path.home() / ".config" / "ai-agent-market" / "anthropic_api_key"


def env(use_key=True):
    e = dict(os.environ)
    if use_key and KEY_FILE.is_file():
        key = KEY_FILE.read_text().strip()
        if key:
            e["ANTHROPIC_API_KEY"] = key
    return e


def run(cmd, prompt, cwd, timeout):
    """subprocess.run of cmd with the key; once more on the CLI login if that fails. Timeouts propagate."""
    p = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env())
    if (p.returncode != 0 or not p.stdout.strip()) and KEY_FILE.is_file():
        p = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           env=env(use_key=False))
    return p


ALERTS = Path(__file__).resolve().parent / ".market" / "agent_alerts.json"


def alert_user(role, items, min_gap=1800):
    """Push an agent's 'alert_user' items to the user (user 11-08: 'why did I find this, not the agent?' - the
    agents found it but nothing told the user). At most one push per role per min_gap seconds, and an alert whose
    text was pushed in the last 6 h is not pushed again. Returns what was sent."""
    import json
    import subprocess
    import sys
    import time
    items = [str(x).strip() for x in (items or []) if str(x).strip()][:3]
    if not items:
        return []
    try:
        st = json.loads(ALERTS.read_text())
    except (OSError, ValueError):
        st = {}
    now = time.time()
    sent = {k: t for k, t in st.get("sent", {}).items() if now - t < 6 * 3600}
    fresh = [x for x in items if x[:120] not in sent]
    if not fresh or now - st.get("last", {}).get(role, 0) < min_gap:
        return []
    name = {"shop": "상점 에이전트", "buyer": "구매 에이전트", "curator": "큐레이터"}.get(role, role)
    subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "notify.py"),
                    f"[yms979] {name} 경보", "\n\n".join(fresh)], capture_output=True, text=True)
    for x in fresh:
        sent[x[:120]] = now
    st["sent"], st.setdefault("last", {})[role] = sent, now
    ALERTS.write_text(json.dumps(st, ensure_ascii=False))
    return fresh
