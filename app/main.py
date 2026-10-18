# -*- coding: utf-8 -*-
"""FastAPI 服务：对外提供问数接口

启动:
    uvicorn app.main:app --reload --port 8000
接口文档:
    http://127.0.0.1:8000/docs
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from app import db, llm, nl2sql  # noqa: E402

app = FastAPI(
    title="零售经营数据智能问数系统",
    description="自然语言转 SQL 的经营数据查询服务",
    version="0.1.0",
)


class AskRequest(BaseModel):
    question: str
    explain: bool = True


@app.get("/health")
def health():
    h = llm.health()
    try:
        stats = db.table_stats()
    except Exception as e:
        stats = {"error": str(e)}
    return {"llm": h, "tables": stats}


@app.get("/schema")
def schema():
    return {"schema": db.load_schema()}


@app.post("/ask")
def ask(req: AskRequest):
    q = (req.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="question 不能为空")
    if len(q) > 200:
        raise HTTPException(status_code=400, detail="问题过长，请控制在 200 字以内")
    try:
        return nl2sql.ask(q, explain=req.explain)
    except llm.LLMError as e:
        raise HTTPException(status_code=503, detail=str(e))
