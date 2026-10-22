"""DeepSeek Search API: https://api-docs.deepseek.com/api/search."""

import asyncio
import json
import os
from pathlib import Path
import sys
import time

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "third_party/probe"))
from probe import probe

URL = "https://api.deepseek.com/v1/tools/search"


def _probe_search_summary(text):
    probe("search_result_summary", text)
    return text


async def web_search(query, count=4, log=None):
    query = query.strip()[:128] if isinstance(query, str) else ""
    started = time.monotonic()
    entry = {"query": query, "request_at": started, "duration_s": 0.0, "result_count": 0}
    if log is not None:
        log["search_used"] = True
        log.setdefault("searches", []).append(entry)
    try:
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not key or not query:
            return _probe_search_summary("搜索失败：缺少 API 密钥或有效的搜索词。")
        async with httpx.AsyncClient(timeout=5.0) as client:
            probe("search_query_forwarded", json.dumps({"query": query, "count": count}, ensure_ascii=False))
            response = await asyncio.wait_for(client.post(
                URL, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"query": query, "count": count}), timeout=5.0)
            response.raise_for_status()
            results = response.json().get("results") or []
        probe("search_result_entries", json.dumps(results, ensure_ascii=False))
        entry["result_count"] = len(results)
        if not results:
            return _probe_search_summary("搜索未找到相关结果，暂时无法确认最新信息。")
        return _probe_search_summary("\n\n".join(
            f"标题：{item.get('title') or '无标题'}\n"
            f"发布日期：{item.get('date') or '未知'}\n"
            f"摘要：{item.get('summary') or item.get('snippet') or '无摘要'}"
            for item in results))
    except Exception as exc:
        return _probe_search_summary(f"搜索失败（{type(exc).__name__}），暂时无法获取最新信息。")
    finally:
        entry["duration_s"] = time.monotonic() - started
