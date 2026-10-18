# -*- coding: utf-8 -*-
"""请求指标与结构化日志

内存里保留最近 N 条请求明细用于算分位，同时按行追加到 logs/requests.jsonl。
设计目标：不引入 Prometheus 这类依赖，也能回答"现在多快、命中多少、错在哪"。
"""

import json
import os
import threading
import time
from collections import deque, Counter

from . import config

LOG_DIR = os.path.join(config.BASE_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "requests.jsonl")
MAX_KEEP = 2000


def _pct(sorted_vals, p):
    if not sorted_vals:
        return 0
    idx = min(int(round(p / 100 * (len(sorted_vals) - 1))), len(sorted_vals) - 1)
    return sorted_vals[idx]


class Metrics:
    def __init__(self):
        self._buf = deque(maxlen=MAX_KEEP)
        self._lock = threading.Lock()
        self._start = time.time()
        self._total = 0
        self._errors = 0
        self._cache_hits = 0
        os.makedirs(LOG_DIR, exist_ok=True)

    def record(self, entry: dict):
        entry.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
        with self._lock:
            self._buf.append(entry)
            self._total += 1
            if entry.get("status") not in ("ok", "clarify", "reject"):
                self._errors += 1
            if entry.get("cache") in ("exact",) or str(entry.get("cache", "")).startswith("semantic"):
                self._cache_hits += 1
            try:
                with open(LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError:
                pass

    def snapshot(self) -> dict:
        with self._lock:
            rows = list(self._buf)
            total = self._total
            errors = self._errors
            hits = self._cache_hits

        if not rows:
            return {"total": total, "window": 0, "uptime_s": int(time.time() - self._start)}

        lat = sorted(r.get("total_ms", 0) for r in rows)
        gen = sorted(r.get("generate_ms", 0) for r in rows if r.get("generate_ms"))
        sql_ms = sorted(r.get("execute_ms", 0) for r in rows if r.get("execute_ms"))

        # 近 60 秒的 QPS
        now = time.time()
        recent = [r for r in rows if now - r.get("_epoch", 0) <= 60]
        qps = round(len(recent) / 60, 2)

        models = Counter(r.get("model") for r in rows if r.get("model"))
        status = Counter(r.get("status") for r in rows)

        return {
            "total_requests": total,
            "window": len(rows),
            "uptime_s": int(now - self._start),
            "qps_60s": qps,
            "latency_ms": {
                "p50": _pct(lat, 50), "p90": _pct(lat, 90),
                "p95": _pct(lat, 95), "p99": _pct(lat, 99),
                "max": lat[-1] if lat else 0,
                "avg": int(sum(lat) / len(lat)) if lat else 0,
            },
            "stage_avg_ms": {
                "generate": int(sum(gen) / len(gen)) if gen else 0,
                "execute": int(sum(sql_ms) / len(sql_ms)) if sql_ms else 0,
            },
            "cache_hit_rate": round(hits / total, 4) if total else 0.0,
            "error_rate": round(errors / total, 4) if total else 0.0,
            "status_dist": dict(status),
            "model_dist": dict(models),
        }

    def recent(self, n: int = 20):
        with self._lock:
            return list(self._buf)[-n:]


METRICS = Metrics()
