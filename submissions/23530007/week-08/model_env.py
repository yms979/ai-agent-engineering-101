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
