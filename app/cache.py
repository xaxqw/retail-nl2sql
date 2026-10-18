# -*- coding: utf-8 -*-
"""两级缓存：精确匹配 + 语义相似匹配

L1 精确匹配：问题字符串完全相同，0 开销
L2 语义匹配：问题向量余弦相似度超过阈值即复用

语义缓存有个坑：语义相近但时间范围不同的问题会误命中
（"上个月销售额" 和 "这个月销售额" 相似度常在 0.95 以上）。
所以命中前还要校验时间签名，不一致就直接放弃这次命中。
"""

import threading
from collections import OrderedDict

from . import config, llm

# 时间口径归一化：不同说法指向同一区间时，必须归到同一个标记
# 否则"上月"和"上个月"会被判成不同口径，语义缓存白白失效
TIME_GROUPS = [
    ("上个月", ["上个月", "上月", "上月度"]),
    ("本月", ["本月", "这个月", "当月"]),
    ("下个月", ["下个月"]),
    ("去年", ["去年", "上一年"]),
    ("今年", ["今年", "本年"]),
    ("最近三个月", ["最近三个月", "最近3个月", "近三个月", "近3个月"]),
    ("最近30天", ["最近30天", "近30天", "最近一个月", "近一个月"]),
    ("最近七天", ["最近七天", "最近7天", "近七天", "近7天", "本周", "这周"]),
    ("双11", ["双11", "双十一"]),
    ("今年上半年", ["上半年"]),
    ("今年下半年", ["下半年"]),
    ("2024", ["2024"]),
    ("2025", ["2025"]),
    ("2026", ["2026"]),
]


def _norm(vec):
    s = sum(x * x for x in vec) ** 0.5
    return [x / s for x in vec] if s else vec


def _cos(a, b):
    return sum(x * y for x, y in zip(a, b))


def _time_sig(q: str) -> frozenset:
    return frozenset(
        canon for canon, words in TIME_GROUPS if any(w in q for w in words)
    )


class SemanticCache:
    def __init__(self, size: int = None, threshold: float = None):
        self.size = size or config.CACHE_SIZE
        self.threshold = threshold or config.CACHE_SIM_THRESHOLD
        self._data = OrderedDict()   # question -> {"result", "vec", "sig"}
        self._lock = threading.Lock()
        self.hits_exact = 0
        self.hits_semantic = 0
        self.misses = 0

    # ---------- 查询 ----------
    def get(self, question: str, allow_semantic: bool = True):
        """返回 (结果 or None, 命中方式)"""
        q = question.strip()

        with self._lock:
            if q in self._data:
                self._data.move_to_end(q)
                self.hits_exact += 1
                return dict(self._data[q]["result"]), "exact"

        if not allow_semantic or not self._data:
            with self._lock:
                self.misses += 1
            return None, "miss"

        try:
            vec = _norm(llm.embed(q))
        except Exception:
            with self._lock:
                self.misses += 1
            return None, "miss"

        sig = _time_sig(q)
        best_key, best_sim = None, 0.0

        with self._lock:
            for key, item in self._data.items():
                if item["sig"] != sig:      # 时间口径不同，直接跳过
                    continue
                sim = _cos(vec, item["vec"])
                if sim > best_sim:
                    best_key, best_sim = key, sim

            if best_key and best_sim >= self.threshold:
                self._data.move_to_end(best_key)
                self.hits_semantic += 1
                res = dict(self._data[best_key]["result"])
                res["cache"] = f"semantic({best_sim:.3f})"
                res["cached_question"] = best_key
                return res, "semantic"

            self.misses += 1
        return None, "miss"

    # ---------- 写入 ----------
    def put(self, question: str, result: dict):
        if not config.CACHE_ENABLED:
            return
        q = question.strip()
        try:
            vec = _norm(llm.embed(q))
        except Exception:
            return   # embedding 不可用时降级为不缓存，不影响主流程

        with self._lock:
            self._data[q] = {"result": dict(result), "vec": vec, "sig": _time_sig(q)}
            self._data.move_to_end(q)
            while len(self._data) > self.size:
                self._data.popitem(last=False)

    def clear(self):
        with self._lock:
            self._data.clear()
            self.hits_exact = self.hits_semantic = self.misses = 0

    def stats(self) -> dict:
        with self._lock:
            total = self.hits_exact + self.hits_semantic + self.misses
            return {
                "size": len(self._data),
                "capacity": self.size,
                "hits_exact": self.hits_exact,
                "hits_semantic": self.hits_semantic,
                "misses": self.misses,
                "hit_rate": round((total - self.misses) / total, 4) if total else 0.0,
            }


CACHE = SemanticCache()
