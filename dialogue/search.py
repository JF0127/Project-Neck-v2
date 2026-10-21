"""DeepSeek Search API: https://api-docs.deepseek.com/api/search."""

import asyncio
import os
import time

import httpx

URL = "https://api.deepseek.com/v1/tools/search"


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
            return "搜索失败：缺少 API 密钥或有效的搜索词。"
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await asyncio.wait_for(client.post(
                URL, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"query": query, "count": count}), timeout=5.0)
            response.raise_for_status()
            results = response.json().get("results") or []
        entry["result_count"] = len(results)
        if not results:
            return "搜索未找到相关结果，暂时无法确认最新信息。"
        return "\n\n".join(
            f"标题：{item.get('title') or '无标题'}\n"
            f"发布日期：{item.get('date') or '未知'}\n"
            f"摘要：{item.get('summary') or item.get('snippet') or '无摘要'}"
            for item in results)
    except Exception as exc:
        return f"搜索失败（{type(exc).__name__}），暂时无法获取最新信息。"
    finally:
        entry["duration_s"] = time.monotonic() - started
