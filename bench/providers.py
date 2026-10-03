"""Dostawcy modeli ze wspólnym interfejsem `generate(...) -> dict`.

* ollama      – lokalny serwer (OLLAMA_HOST, domyślnie http://127.0.0.1:11434) lub chmura https://ollama.com z kluczem OLLAMA_API_KEY
* openrouter  – https://openrouter.ai/api/v1 z kluczem OPENROUTER_API_KEY (modele ":free" bez opłat, z limitami)
* claude_cli  – Claude Code z linii poleceń (`claude -p`), modele haiku / sonnet / opus; rozliczane w ramach abonamentu
Każda odpowiedź: {text, parsed(json|None), usage{wej,wyj}, koszt_usd, czas_ms, surowe, blad}.
Błędy 429/5xx: ponawianie z rosnącym odstępem (do 6 prób), potem błąd zapisywany przy odpowiedzi.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request

RETRY_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}


def _post_json(url: str, payload: dict, headers: dict, timeout: int = 300) -> tuple[int, dict | str]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, body
    except (urllib.error.URLError, TimeoutError) as e:
        return 0, str(e)


def _with_retry(fn, max_tries: int = 6, base: float = 5.0):
    last = None
    for i in range(max_tries):
        status, data = fn()
        if status == 200:
            return status, data
        last = (status, data)
        if status in RETRY_STATUSES or status == 0:
            time.sleep(base * (2 ** i))
            continue
        break
    return last


def _parse_json_text(text: str):
    if text is None:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
    a, b = s.find("{"), s.rfind("}")
    if a == -1 or b == -1:
        return None
    try:
        return json.loads(s[a:b + 1])
    except json.JSONDecodeError:
        return None


class Ollama:
    name = "ollama"

    def __init__(self, host: str | None = None, api_key: str | None = None):
        self.host = (host or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
        self.key = api_key or os.environ.get("OLLAMA_API_KEY")

    def list_models(self) -> list[dict]:
        req = urllib.request.Request(self.host + "/api/tags", headers={"Authorization": f"Bearer {self.key}"} if self.key else {})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
        return [{"nazwa": m["name"], "parametry": (m.get("details") or {}).get("parameter_size"), "rozmiar": m.get("size")} for m in d.get("models", [])]

    def generate(self, model: str, system: str, user: str, schema: dict | None = None, temperature: float = 0.0, max_tokens: int = 300, seed: int = 7) -> dict:
        payload = {"model": model, "stream": False, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "options": {"temperature": temperature, "num_predict": max_tokens, "seed": seed}}
        if schema:
            payload["format"] = schema
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        t = time.time()
        status, data = _with_retry(lambda: _post_json(self.host + "/api/chat", payload, headers))
        ms = int((time.time() - t) * 1000)
        if status != 200:
            return {"text": None, "parsed": None, "usage": {}, "koszt_usd": 0.0, "czas_ms": ms, "surowe": data, "blad": f"HTTP {status}: {str(data)[:300]}"}
        text = (data.get("message") or {}).get("content", "")
        return {"text": text, "parsed": _parse_json_text(text), "usage": {"wej": data.get("prompt_eval_count", 0), "wyj": data.get("eval_count", 0)},
                "koszt_usd": 0.0, "czas_ms": ms, "surowe": data, "blad": None}


class OpenRouter:
    name = "openrouter"
    URL = "https://openrouter.ai/api/v1"

    def __init__(self, api_key: str | None = None):
        self.key = api_key or os.environ.get("OPENROUTER_API_KEY")

    def list_models(self, only_free: bool = True) -> list[dict]:
        with urllib.request.urlopen(self.URL + "/models", timeout=30) as r:
            d = json.load(r)["data"]
        out = []
        for m in d:
            pr = m.get("pricing") or {}
            free = float(pr.get("prompt") or 0) == 0 and float(pr.get("completion") or 0) == 0
            if only_free and not free:
                continue
            out.append({"nazwa": m["id"], "darmowy": free, "kontekst": m.get("context_length"), "cena_wej": float(pr.get("prompt") or 0),
                        "cena_wyj": float(pr.get("completion") or 0), "modalnosc": (m.get("architecture") or {}).get("modality"), "etykieta": m.get("name")})
        return out

    def generate(self, model: str, system: str, user: str, schema: dict | None = None, temperature: float = 0.0, max_tokens: int = 300, seed: int = 7) -> dict:
        if not self.key:
            return {"text": None, "parsed": None, "usage": {}, "koszt_usd": 0.0, "czas_ms": 0, "surowe": None, "blad": "brak OPENROUTER_API_KEY"}
        payload = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "temperature": temperature, "max_tokens": max_tokens, "seed": seed, "usage": {"include": True}}
        if schema:
            payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "odpowiedz", "strict": True, "schema": schema}}
        headers = {"Authorization": f"Bearer {self.key}", "HTTP-Referer": "https://github.com/marcinkleczek/cross-research", "X-Title": "krzyzowki-benchmark"}
        t = time.time()
        status, data = _with_retry(lambda: _post_json(self.URL + "/chat/completions", payload, headers))
        ms = int((time.time() - t) * 1000)
        if status != 200 or not isinstance(data, dict) or "choices" not in data:
            # część darmowych modeli nie obsługuje response_format: ponawiamy bez schematu
            if schema and status == 400:
                payload.pop("response_format", None)
                status, data = _with_retry(lambda: _post_json(self.URL + "/chat/completions", payload, headers))
                ms = int((time.time() - t) * 1000)
            if status != 200 or not isinstance(data, dict) or "choices" not in data:
                return {"text": None, "parsed": None, "usage": {}, "koszt_usd": 0.0, "czas_ms": ms, "surowe": data, "blad": f"HTTP {status}: {str(data)[:300]}"}
        text = (data["choices"][0].get("message") or {}).get("content", "") or ""
        us = data.get("usage") or {}
        return {"text": text, "parsed": _parse_json_text(text), "usage": {"wej": us.get("prompt_tokens", 0), "wyj": us.get("completion_tokens", 0)},
                "koszt_usd": float(us.get("cost") or 0.0), "czas_ms": ms, "surowe": data, "blad": None}


class ClaudeCLI:
    """Claude Code z linii poleceń. Bez narzędzi, jedna tura, odpowiedź w formacie wymuszonym `--json-schema`."""
    name = "claude_cli"
    MODELS = ["haiku", "sonnet", "opus"]

    def list_models(self) -> list[dict]:
        return [{"nazwa": m} for m in self.MODELS]

    def generate(self, model: str, system: str, user: str, schema: dict | None = None, temperature: float = 0.0, max_tokens: int = 300, seed: int = 7) -> dict:
        prompt = system + "\n\n" + user
        cmd = ["claude", "-p", prompt, "--model", model, "--output-format", "json", "--max-turns", "3", "--tools", "", "--permission-mode", "dontAsk"]
        if schema:
            cmd += ["--json-schema", json.dumps(schema, ensure_ascii=False)]
        t = time.time()
        for i in range(4):
            try:
                pr = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=os.environ.get("BENCH_CLAUDE_CWD", "/tmp"))
            except subprocess.TimeoutExpired:
                continue
            if pr.stdout.strip():
                break
            time.sleep(10 * (2 ** i))
        ms = int((time.time() - t) * 1000)
        try:
            d = json.loads(pr.stdout)
        except Exception:  # noqa: BLE001
            return {"text": None, "parsed": None, "usage": {}, "koszt_usd": 0.0, "czas_ms": ms, "surowe": {"stdout": pr.stdout[-1000:], "stderr": pr.stderr[-1000:]}, "blad": "brak JSON z claude"}
        if d.get("is_error") or d.get("subtype", "").startswith("error"):
            return {"text": None, "parsed": None, "usage": {}, "koszt_usd": float(d.get("total_cost_usd") or 0), "czas_ms": ms, "surowe": d, "blad": str(d.get("result") or d.get("subtype"))[:300]}
        so = d.get("structured_output")
        text = json.dumps(so, ensure_ascii=False) if isinstance(so, dict) else (d.get("result") or "")
        parsed = so if isinstance(so, dict) else _parse_json_text(text)
        us = d.get("usage") or {}
        return {"text": text, "parsed": parsed, "usage": {"wej": us.get("input_tokens", 0) + us.get("cache_read_input_tokens", 0) + us.get("cache_creation_input_tokens", 0), "wyj": us.get("output_tokens", 0)},
                "koszt_usd": float(d.get("total_cost_usd") or 0), "czas_ms": ms, "surowe": {k: d.get(k) for k in ("modelUsage", "usage", "duration_api_ms", "stop_reason", "num_turns")}, "blad": None}


PROVIDERS = {"ollama": Ollama, "openrouter": OpenRouter, "claude_cli": ClaudeCLI}


def get(name: str):
    return PROVIDERS[name]()
