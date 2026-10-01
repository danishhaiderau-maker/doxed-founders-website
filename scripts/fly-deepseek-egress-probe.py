"""Read-only DeepSeek egress probe (run inside the Fly machine).

Distinguishes DNS, TCP, auth, balance, model-id and slow-completion failures
from the exact network namespace the bot uses.  The API key is read from the
environment (or the bot process environment) and only a short SHA-256
fingerprint is printed; the key itself is never emitted.  Spends well under
one cent of DeepSeek credit (two short completions).
"""
import hashlib
import json
import os
import socket
import time
from pathlib import Path

import requests

BASE = "https://api.deepseek.com"
CHAT = BASE + "/v1/chat/completions"


def _key():
    key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if key:
        return key, "exec_env"
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cmd = (proc / "cmdline").read_bytes()
            if b"bot.py" not in cmd and b"gunicorn" not in cmd and b"python" not in cmd:
                continue
            for item in (proc / "environ").read_bytes().split(b"\0"):
                if item.startswith(b"DEEPSEEK_API_KEY="):
                    value = item.split(b"=", 1)[1].decode("utf-8", "replace").strip()
                    if value:
                        return value, f"proc_env:{proc.name}"
        except Exception:
            continue
    return "", "missing"


def _timed(label, fn):
    t0 = time.time()
    try:
        out = fn()
        out["ms"] = int((time.time() - t0) * 1000)
    except Exception as exc:
        out = {"error": f"{type(exc).__name__}:{str(exc)[:200]}", "ms": int((time.time() - t0) * 1000)}
    print(json.dumps({"probe": label, **out}, sort_keys=True), flush=True)
    return out


def main():
    key, source = _key()
    print(json.dumps({
        "probe": "config",
        "key_present": bool(key),
        "key_len": len(key),
        "key_fp": hashlib.sha256(key.encode()).hexdigest()[:10] if key else None,
        "key_source": source,
        "deepseek_model_env": os.getenv("DEEPSEEK_MODEL"),
        "deepseek_thinking_env": os.getenv("DEEPSEEK_THINKING_MODE"),
        "proxy_env": sorted(k for k in os.environ if "proxy" in k.lower()),
        "requests_version": requests.__version__,
    }, sort_keys=True), flush=True)
    _timed("dns", lambda: {"addrs": sorted({a[4][0] for a in socket.getaddrinfo("api.deepseek.com", 443)})})

    def tcp():
        s = socket.create_connection(("api.deepseek.com", 443), timeout=5)
        s.close()
        return {"ok": True}

    _timed("tcp", tcp)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def balance():
        r = requests.get(BASE + "/user/balance", headers=headers, timeout=8)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        return {"http": r.status_code, "is_available": body.get("is_available"),
                "totals": [(b.get("currency"), b.get("total_balance")) for b in body.get("balance_infos") or []]}

    _timed("balance", balance)

    def models():
        r = requests.get(BASE + "/models", headers=headers, timeout=8)
        ids = [m.get("id") for m in (r.json().get("data") or [])] if r.ok else []
        return {"http": r.status_code, "ids": ids, "body": "" if r.ok else r.text[:200]}

    _timed("models", models)

    def chat(model, content, timeout):
        def run():
            r = requests.post(CHAT, headers=headers, timeout=timeout, json={
                "model": model, "temperature": 0.0, "thinking": {"type": "disabled"},
                "messages": [{"role": "user", "content": content}]})
            out = {"http": r.status_code}
            if r.ok:
                body = r.json()
                usage = body.get("usage") or {}
                out.update(model_echo=body.get("model"), prompt_tokens=usage.get("prompt_tokens"),
                           completion_tokens=usage.get("completion_tokens"))
            else:
                out["body"] = r.text[:200]
            return out
        return run

    _timed("chat_tiny_v4_flash", chat("deepseek-v4-flash", "Reply with the single word OK", 12))
    filler = " ".join(f"bar{i}:o=84650 h=84700 l=84600 c=84680 v=12.5" for i in range(400))
    _timed("chat_5k_v4_flash", chat("deepseek-v4-flash", "Summarise in one word: " + filler, 15))


if __name__ == "__main__":
    main()
