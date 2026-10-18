# -*- coding: utf-8 -*-
"""LLM 调用封装：兼容 Ollama 本地模型与 OpenAI 兼容接口"""

import json
import time
import urllib.error
import urllib.request

from . import config


class LLMError(RuntimeError):
    pass


def _post(url: str, payload: dict, timeout: int) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise LLMError(f"模型服务不可用: {e}") from e
    except json.JSONDecodeError as e:
        raise LLMError(f"模型返回非 JSON: {e}") from e


def chat(system: str, user: str, temperature: float = None) -> str:
    """调用 Ollama /api/chat，返回文本内容"""
    payload = {
        "model": config.MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {
            "temperature": config.TEMPERATURE if temperature is None else temperature,
            "num_predict": 512,
        },
    }
    res = _post(f"{config.OLLAMA_URL}/api/chat", payload, config.LLM_TIMEOUT)
    msg = res.get("message") or {}
    content = (msg.get("content") or "").strip()
    if not content:
        raise LLMError("模型返回空内容")
    return content


def health() -> dict:
    """检查模型服务与模型是否就绪"""
    try:
        req = urllib.request.Request(f"{config.OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
        names = [m.get("name", "") for m in tags.get("models", [])]
        return {
            "ok": config.MODEL in names,
            "url": config.OLLAMA_URL,
            "model": config.MODEL,
            "available": names,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "model": config.MODEL}
