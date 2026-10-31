"""Minimal OpenAI-compatible chat client (works with any gateway whose docs say 'OpenAI compatible').
Built to burn as few tokens as possible: disk cache, hard budget guard, usage log, calibrated estimates."""
import hashlib
import json
import os
import re
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

from .config import ROOT

CACHE = ROOT / "cache" / "llm"
USAGE = ROOT / "logs" / "llm_usage.jsonl"
CALIB = ROOT / "cache" / "calibration.json"
_run_tokens = 0


class LLMError(Exception):
    pass


def cfg():
    load_dotenv(ROOT / ".env")
    g = os.getenv
    c = {"base": g("LLM_BASE_URL", "").strip().rstrip("/"), "key": g("LLM_API_KEY", "").strip(), "model": g("LLM_MODEL", "").strip(),
         "pin": float(g("LLM_PRICE_IN_PER_M", "0") or 0), "pout": float(g("LLM_PRICE_OUT_PER_M", "0") or 0),
         "budget": float(g("LLM_BUDGET_USD", "0") or 0), "max_out": int(g("LLM_MAX_OUTPUT_TOKENS", "1500")),
         "run_cap": int(g("LLM_MAX_TOKENS_PER_RUN", "60000")), "timeout": int(g("LLM_TIMEOUT", "150"))}
    if not (c["base"] and c["key"] and c["model"]) or "xxxx" in c["key"]:
        raise LLMError("Fill LLM_BASE_URL, LLM_API_KEY and LLM_MODEL in .env (see .env.example).")
    c["base"] = re.sub(r"/chat/completions$", "", c["base"])
    return c


def reset_run():
    global _run_tokens
    _run_tokens = 0


def ratio():
    try:
        return float(json.loads(CALIB.read_text())["chars_per_token"])
    except Exception:
        return 2.0  # conservative for Persian until the first real call calibrates it


def est_tokens(chars):
    return int(chars / ratio()) + 1


def cost_of(c, tin, tout):
    return tin * c["pin"] / 1e6 + tout * c["pout"] / 1e6


def spent_this_month():
    if not USAGE.exists():
        return 0.0
    m, tot = datetime.now().strftime("%Y-%m"), 0.0
    for ln in USAGE.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(ln)
            if r["time"].startswith(m):
                tot += r.get("cost", 0)
        except Exception:
            pass
    return tot


def chat(messages, max_out=None, task="x", use_cache=True):
    """-> (text, info). Raises LLMError on any problem. Never retries blindly (every retry costs money)."""
    global _run_tokens
    c = cfg()
    max_out = min(max_out or c["max_out"], c["max_out"])
    key = hashlib.sha256(json.dumps([c["model"], messages, max_out], ensure_ascii=False).encode()).hexdigest()
    cf = CACHE / f"{key}.json"
    if use_cache and cf.exists():
        d = json.loads(cf.read_text(encoding="utf-8"))
        return d["text"], {"cached": True, "tin": 0, "tout": 0, "cost": 0.0}
    chars = sum(len(m["content"]) for m in messages)
    tin = est_tokens(chars)
    if c["pin"] or c["pout"]:
        worst = cost_of(c, tin, max_out)
        if c["budget"] and spent_this_month() + worst > c["budget"]:
            raise LLMError(f"Budget guard: spent ${spent_this_month():.4f} + this call (worst case ${worst:.4f}) > LLM_BUDGET_USD ${c['budget']}")
    if _run_tokens + tin + max_out > c["run_cap"]:
        raise LLMError(f"Per-run token cap reached ({c['run_cap']}). Raise LLM_MAX_TOKENS_PER_RUN if intended.")
    body = {"model": c["model"], "messages": messages, "temperature": 0, "max_tokens": max_out}
    t0, r = time.time(), None
    for attempt in range(2):
        try:
            r = requests.post(c["base"] + "/chat/completions", json=body, timeout=c["timeout"],
                              headers={"Authorization": f"Bearer {c['key']}", "Content-Type": "application/json"})
        except requests.Timeout:
            raise LLMError(f"The AI provider did not answer within {c['timeout']}s (LLM_TIMEOUT). The call may still have been billed - "
                           "check your balance. Raise LLM_TIMEOUT in .env or try a smaller/faster model.")
        except requests.RequestException as e:
            raise LLMError(f"Network error: {type(e).__name__}: {e}")
        if r.status_code == 400 and "max_tokens" in r.text and "max_completion_tokens" in r.text and attempt == 0:
            body["max_completion_tokens"] = body.pop("max_tokens")  # some newer models want this name
            continue
        break
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message") or r.text[:200]
        except ValueError:
            msg = r.text[:200]
        hint = " (invalid or missing API key?)" if r.status_code == 401 else " (no credit left?)" if r.status_code in (402, 403) else ""
        raise LLMError(f"HTTP {r.status_code}{hint}: {msg}")
    d = r.json()
    try:
        text = d["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"Unexpected reply: {str(d)[:200]}")
    u = d.get("usage") or {}
    ptok, ctok = int(u.get("prompt_tokens", tin)), int(u.get("completion_tokens", est_tokens(len(text))))
    _run_tokens += ptok + ctok
    cost = cost_of(c, ptok, ctok)
    USAGE.parent.mkdir(exist_ok=True)
    with open(USAGE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), "task": task, "model": c["model"],
                            "tin": ptok, "tout": ctok, "cost": round(cost, 6)}) + "\n")
    if u.get("prompt_tokens") and chars > 200:  # calibrate the estimator (chars per token) from reality
        CALIB.parent.mkdir(parents=True, exist_ok=True)
        CALIB.write_text(json.dumps({"chars_per_token": round(chars / ptok, 3)}))
    CACHE.mkdir(parents=True, exist_ok=True)
    cf.write_text(json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8")
    return text, {"cached": False, "tin": ptok, "tout": ctok, "cost": cost, "sec": round(time.time() - t0, 1)}


def parse_json_list(text):
    t = re.sub(r"```(?:json)?", "", text).strip()
    i, j = t.find("["), t.rfind("]")
    if i == -1 or j == -1:
        raise LLMError("reply has no JSON list: " + text[:120])
    try:
        return json.loads(t[i:j + 1])
    except ValueError as e:
        raise LLMError(f"reply is not valid JSON ({e}): " + text[:120])


def ping():
    c = cfg()
    text, info = chat([{"role": "user", "content": "Reply with the single word: OK"}], max_out=8, task="ping", use_cache=False)
    print(f"Connected. model={c['model']} reply={text.strip()[:20]!r} tokens in/out={info['tin']}/{info['tout']} "
          f"cost=${info['cost']:.6f} time={info['sec']}s")
    print(f"Spent this month (by this program): ${spent_this_month():.4f}" + (f" of ${c['budget']}" if c["budget"] else ""))
    if not (c["pin"] or c["pout"]):
        print("Note: LLM_PRICE_IN_PER_M / LLM_PRICE_OUT_PER_M are not set, so costs show as $0 and only the token cap protects you.")
