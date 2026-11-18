#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Клиент LLM: подбор URL и распространённых форм ответа, только stdlib."""

import json
import os
import operator
import random
import threading
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "https://api.inceptionlabs.ai/v1"
DEFAULT_MODEL = "mercury-3"

# Это тариф Mercury 3, для других моделей стоимость является лишь оценкой.
PRICE_IN = 0.10 / 1e6
PRICE_OUT = 0.35 / 1e6


class LLMError(Exception):
    def __init__(self, msg, status=None, retryable=False):
        super().__init__(msg)
        self.status = status
        self.retryable = retryable


def cost(in_tok, out_tok):
    return operator.mul(in_tok, PRICE_IN) + operator.mul(out_tok, PRICE_OUT)


def _backoff(n, retry_after=None):
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except (TypeError, ValueError):
            pass
    delay = 1.5
    for _ in range(n):
        delay += delay
    return min(30.0, delay) + random.uniform(0, 0.6)


def _get(obj, path):
    for key in path:
        if isinstance(key, int):
            if not isinstance(obj, list) or len(obj) <= key:
                return None
            obj = obj[key]
        else:
            if not isinstance(obj, dict):
                return None
            obj = obj.get(key)
    return obj


_TEXT_PATHS = (
    ("choices", 0, "delta", "content"),
    ("choices", 0, "message", "content"),
    ("delta", "text"),
    ("content_block", "delta", "text"),
    ("candidates", 0, "content", "parts", 0, "text"),
    ("message", "content"),
    ("content",), ("text",), ("output",), ("response",),
    ("result",), ("generated_text",),
)


def _count(value):
    try:
        return max(0, int(value)) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


class Client:
    def __init__(self, api_key=None, base_url=None, model=None, timeout=180, retries=5, stop_event=None):
        self.api_key = api_key or os.environ.get("INCEPTION_API_KEY", "")
        self.base_url = base_url or os.environ.get("MERCURY_BASE_URL") or DEFAULT_BASE
        self.model = model or os.environ.get("MERCURY_MODEL") or DEFAULT_MODEL
        self.timeout = timeout
        self.retries = retries
        self.stop_event = stop_event
        self.cooldown_lock = threading.Lock()
        self.cooldown_until = 0.0
        self._working_url = None

    def _wait(self, seconds):
        if self.stop_event:
            if self.stop_event.wait(seconds):
                raise LLMError("запрос отменён пользователем")
        else:
            time.sleep(seconds)

    def _wait_cooldown(self):
        while True:
            if self.stop_event and self.stop_event.is_set():
                raise LLMError("запрос отменён пользователем")
            with self.cooldown_lock:
                remaining = self.cooldown_until - time.monotonic()
            if remaining <= 0:
                return
            self._wait(remaining + random.uniform(0.05, 1.5))

    @property
    def ready(self):
        return bool(self.api_key)

    def _payload(self, messages, effort, temperature, max_tokens, stream, json_mode):
        p = {
            "model": self.model,
            "messages": messages,
            "reasoning_effort": effort or "medium",
            "temperature": max(0.5, min(1.0, float(temperature or 0.75))),
            "max_completion_tokens": int(max_tokens or 1200),
            "stream": bool(stream),
        }
        if stream:
            p["stream_options"] = {"include_usage": True}
        if json_mode:
            p["response_format"] = {"type": "json_object"}
        return p

    def _redact(self, text):
        text = str(text or "")
        return text.replace(self.api_key, "[скрыто]") if self.api_key else text

    def _open(self, url, body):
        codes = []
        for auth in ("bearer", "key"):
            headers = {"Content-Type": "application/json",
                       "Accept": "text/event-stream, application/json, application/x-ndjson",
                       "User-Agent": "MercuryStudio/1.0"}
            if auth == "bearer":
                headers["Authorization"] = "Bearer " + self.api_key
            else:
                headers["x-api-key"] = self.api_key
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                return urllib.request.urlopen(req, timeout=self.timeout)
            except urllib.error.HTTPError as exc:
                if exc.code not in (401, 403):
                    raise
                codes.append(exc.code)
                exc.close()
        raise LLMError("ошибка авторизации: Bearer HTTP %s, x-api-key HTTP %s"
                       % (codes[0], codes[1]), codes[-1])

    def _read_response(self, resp, messages, on_token, on_thought, t0):
        parts = []
        usage = {"in": 0, "out": 0}
        model = self.model
        finish = ""
        chosen = None
        preview = ""
        length = 0
        event_type = ""
        saw_encrypted = False
        saw_chunk = False

        def process(chunk):
            nonlocal chosen, model, finish, length, saw_encrypted, saw_chunk
            if not isinstance(chunk, dict):
                return
            saw_chunk = True
            if isinstance(chunk.get("model"), str) and chunk["model"]:
                model = chunk["model"]
            for dest, paths in (
                ("in", (("usage", "prompt_tokens"), ("usage", "input_tokens"),
                        ("usageMetadata", "promptTokenCount"))),
                ("out", (("usage", "completion_tokens"), ("usage", "output_tokens"),
                         ("usageMetadata", "candidatesTokenCount"))),
            ):
                for path in paths:
                    val = _count(_get(chunk, path))
                    if val is not None:
                        usage[dest] = val
                        break
            for path in (("choices", 0, "finish_reason"), ("finish_reason",),
                         ("stop_reason",), ("delta", "stop_reason"),
                         ("candidates", 0, "finishReason"), ("finishReason",)):
                val = _get(chunk, path)
                if isinstance(val, str) and val:
                    finish = val
                    break
            for container in (_get(chunk, ("choices", 0, "delta")),
                              _get(chunk, ("choices", 0, "message"))):
                if not isinstance(container, dict):
                    continue
                thought = container.get("reasoning_content") or container.get("reasoning")
                if isinstance(thought, str) and thought and on_thought:
                    on_thought(thought)
                details = container.get("reasoning_details") or []
                if isinstance(details, list):
                    for detail in details:
                        if not isinstance(detail, dict):
                            continue
                        if detail.get("type") == "reasoning.encrypted":
                            saw_encrypted = True
                        elif detail.get("type") == "reasoning.text":
                            value = detail.get("text")
                            if isinstance(value, str) and value and on_thought:
                                on_thought(value)
            paths = (chosen,) if chosen is not None else _TEXT_PATHS
            for path in paths:
                value = _get(chunk, path)
                if isinstance(value, str) and value:
                    if chosen is None:
                        chosen = path
                    parts.append(value)
                    length += len(value)
                    if on_token:
                        on_token(value, length)
                    break

        def consume(raw):
            nonlocal preview, event_type
            line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
            if len(preview) < 300:
                preview = (preview + line)[:300]
            line = line.strip()
            if line.startswith("event:"):
                event_type = line[6:].strip()
                return True
            if not line or line.startswith(":"):
                return True
            if line.startswith("data:"):
                line = line[5:].strip()
            if line == "[DONE]":
                return False
            try:
                chunk = json.loads(line)
            except (ValueError, TypeError):
                return True
            process(chunk)
            return True

        headers = getattr(resp, "headers", None)
        ctype = (headers.get("Content-Type", "") if headers else "").lower()
        if not ctype or "text/event-stream" in ctype or "ndjson" in ctype or "jsonl" in ctype:
            for raw in resp:
                if not consume(raw):
                    break
        else:
            raw = resp.read()
            text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
            preview = text[:300]
            try:
                chunk = json.loads(text)
            except (ValueError, TypeError):
                for line in text.splitlines():
                    if not consume(line):
                        break
            else:
                process(chunk)
        text = "".join(parts).strip()
        if not text:
            if finish in ("length", "max_tokens", "MAX_TOKENS"):
                raise LLMError("модель исчерпала бюджет токенов до текстового ответа; увеличьте max_completion_tokens или уменьшите Effort")
            if saw_encrypted:
                raise LLMError("модель вернула только зашифрованное рассуждение без текстового ответа")
            if saw_chunk:
                raise LLMError("не удалось распознать формат ответа; первые 300 символов: " + self._redact(preview[:300]))
            raise LLMError("не удалось распознать формат ответа провайдера")
        if not usage["out"]:
            usage["out"] = max(1, len(text) // 4)
        if not usage["in"]:
            usage["in"] = max(1, sum(len(str(m.get("content") or "")) for m in messages) // 4)
        return {"text": text, "usage": usage, "latency": time.time() - t0,
                "model": model, "finish": finish,
                "truncated": finish in ("length", "max_tokens", "MAX_TOKENS")}

    def _request(self, messages, effort, temperature, max_tokens, json_mode,
                 on_token, on_reason, on_thought, stream):
        if not self.ready:
            raise LLMError("нет API-ключа — включите демо-режим или вставьте ключ в настройках")
        payload = self._payload(messages, effort, temperature, max_tokens, stream, json_mode)
        body = json.dumps(payload).encode("utf-8")
        base = self.base_url
        urls = [self._working_url] if self._working_url else [base]
        if not self._working_url and not base.rstrip("/").endswith("/chat/completions"):
            urls.append(base.rstrip("/") + "/chat/completions")
        missing = []
        t0 = time.time()

        def with_attempts(exc, url, status=None):
            if not missing:
                return exc
            prior = "; ".join("%s — HTTP %s" % (self._redact(item), code)
                              for item, code in missing)
            current = "HTTP %s" % status if status is not None else "без HTTP-ответа"
            message = "%s; %s — %s: %s" % (prior, self._redact(url), current, self._redact(exc))
            return LLMError(message, exc.status, exc.retryable)

        for url in urls:
            last = None
            for attempt in range(self.retries + 1):
                self._wait_cooldown()
                partial = [False]

                def token(piece, total):
                    partial[0] = True
                    if on_token:
                        on_token(piece, total)

                response_status = None
                try:
                    with self._open(url, body) as resp:
                        response_status = getattr(resp, "status", 200)
                        result = self._read_response(resp, messages, token, on_thought, t0)
                    self._working_url = url
                    return result
                except urllib.error.HTTPError as exc:
                    code = exc.code
                    detail = ""
                    try:
                        detail = exc.read().decode("utf-8", "replace")[:300]
                    except Exception:
                        pass
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    exc.close()
                    if code in (404, 405, 410, 501):
                        missing.append((url, code))
                        break
                    retryable = code in (408, 409, 425, 429, 500, 502, 503, 504)
                    last = LLMError("HTTP %s: %s" % (code, self._redact(detail or exc.reason)),
                                    code, retryable)
                    if not retryable or attempt == self.retries or partial[0]:
                        raise with_attempts(last, url, code)
                    wait = _backoff(attempt, retry_after)
                    if code == 429:
                        if not retry_after:
                            wait = max(wait, 60.0)
                        with self.cooldown_lock:
                            self.cooldown_until = max(self.cooldown_until, time.monotonic() + wait)
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    if partial[0]:
                        error = LLMError("поток прерван после начала ответа: %s" % self._redact(exc))
                        raise with_attempts(error, url, response_status)
                    last = LLMError("сеть: %s" % self._redact(exc), retryable=True)
                    if attempt == self.retries:
                        raise with_attempts(last, url)
                    wait = _backoff(attempt)
                except LLMError as exc:
                    raise with_attempts(exc, url, exc.status or response_status)
                except (ValueError, TypeError) as exc:
                    error = LLMError("некорректный адрес или запрос: %s" % self._redact(exc))
                    raise with_attempts(error, url, response_status)
                if on_reason:
                    on_reason("повтор %d: %s" % (attempt + 1, last))
                if not (last and last.status == 429):
                    self._wait(wait)
        if missing:
            detail = "; ".join("%s — HTTP %s" % (self._redact(url), code) for url, code in missing)
            raise LLMError("не найден адрес провайдера: " + detail, missing[-1][1])
        raise LLMError("не удалось выполнить запрос")

    def chat(self, messages, effort="medium", temperature=0.75, max_tokens=1200,
             json_mode=False, on_token=None, on_reason=None, on_thought=None):
        return self._request(messages, effort, temperature, max_tokens, json_mode,
                             on_token, on_reason, on_thought, True)

    def probe(self, messages, effort="low", max_tokens=10):
        """Короткая непотоковая проверка без изменения интерфейса chat()."""
        return self._request(messages, effort, 0.75, max_tokens, False,
                             None, None, None, False)
