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


def chat(system: str, user: str, temperature: float = None,
         model: str = None, num_predict: int = None,
         keep_alive: str = None) -> str:
    """调用 Ollama /api/chat，返回文本内容

    model / num_predict / keep_alive 可按请求覆盖，用于模型路由与性能调优。
    """
    payload = {
        "model": model or config.MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {
            "temperature": config.TEMPERATURE if temperature is None else temperature,
            "num_predict": num_predict or config.NUM_PREDICT,
        },
        "keep_alive": keep_alive or config.KEEP_ALIVE,
    }
    res = _post(f"{config.OLLAMA_URL}/api/chat", payload, config.LLM_TIMEOUT)
    msg = res.get("message") or {}
    content = (msg.get("content") or "").strip()
    if not content:
        raise LLMError("模型返回空内容")
    return content


def chat_stream(system: str, user: str, model: str = None):
    """流式调用，逐块 yield 文本，用于 SSE"""
    payload = {
        "model": model or config.MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": True,
        "options": {"temperature": config.TEMPERATURE,
                    "num_predict": config.NUM_PREDICT},
        "keep_alive": config.KEEP_ALIVE,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{config.OLLAMA_URL}/api/chat", data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=config.LLM_TIMEOUT) as resp:
        for line in resp:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            piece = (obj.get("message") or {}).get("content") or ""
            if piece:
                yield piece
            if obj.get("done"):
                break


def embed(text: str, model: str = None):
    """文本向量化，用于语义缓存

    强制 num_gpu=0：embedding 模型也有 1GB+，和 7b 抢显存会导致主模型被换出，
    下次生成要重新加载 55 秒。放 CPU 上跑多花 100ms，比这个代价小得多。
    """
    payload = {
        "model": model or config.EMBED_MODEL,
        "prompt": text,
        "options": {"num_gpu": 0},
        "keep_alive": config.KEEP_ALIVE,
    }
    res = _post(f"{config.OLLAMA_URL}/api/embeddings", payload, 60)
    vec = res.get("embedding")
    if not vec:
        raise LLMError("embedding 返回为空")
    return vec


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
