"""DeepSeek Responses streaming, adapted from Project-Neck/runtime/dialogue.py."""

from collections import deque
import json
import os
from pathlib import Path

from openai import AsyncOpenAI

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-flash"
EXPRESSIONS = Path(__file__).resolve().parents[1] / "speech_motion/expressions.json"
_history = deque(maxlen=10)


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _completed_text(response):
    text = _field(response, "output_text")
    if isinstance(text, str) and text:
        return text
    parts = []
    for item in _field(response, "output", []) or []:
        text = _field(item, "text")
        if isinstance(text, str):
            parts.append(text)
        for content in _field(item, "content", []) or []:
            text = _field(content, "text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _segment(line, names):
    try:
        value = json.loads(line)
        text, expr, intensity = value["text"], value["expr"], value["intensity"]
        if (not isinstance(text, str) or not 0 < len(text.strip()) <= 20
                or expr not in names or type(intensity) not in (int, float)
                or not 0 <= intensity <= 1):
            return None
        return {"text": text.strip(), "expr": expr, "intensity": intensity,
                "emphasis": value.get("emphasis", False) is True}
    except (ValueError, TypeError, KeyError):
        return None


async def stream_segments(user_text):
    """Yield complete JSON-line segments; retain the last ten completed turns."""
    names = list(json.loads(EXPRESSIONS.read_text())["expressions"]) + ["none"]
    prompt = (
        '你是一个机器人，用自然、简洁的中文口语对话。'
        '输出格式：每行一个 JSON 对象 {"text": 文本, "expr": 情绪, "intensity": 0到1的数}，'
        '不输出任何其他内容。expr 只能取以下名称：'
        + json.dumps(names, ensure_ascii=False)
        + '。每段 text 是一个自然的短语，不超过20个字；整个回复不超过60个字。'
        '大多数段落的 expr 应为 "none"；只在语义明确需要表达情绪时才标注。'
        'neutral：平静陈述。attentive：认真倾听或回应对方。'
        'thinking：需要斟酌、回忆或不确定时。puzzled：反问、质疑、觉得奇怪时。'
        'shy：被夸奖或不好意思时。agree：赞同对方观点时。disagree：不同意、否定对方说法时。'
        'surprised：听到意外的事情时。happy：开心、轻松、分享好消息时。sad：遗憾、难过的话题时。'
        '每次回复中非 "none" 的 expr 最多3个；相邻两段尽量不使用相同的 expr；'
        'intensity 一般在0.3到0.8之间。'
        '每次回复可以给最关键的一段添加 "emphasis": true，最多1段；其余段落省略此字段或设为 false。'
    )
    messages = [{"role": "system", "content": prompt}]
    for user, assistant in _history:
        messages.extend([{"role": "user", "content": user},
                         {"role": "assistant", "content": assistant}])
    messages.append({"role": "user", "content": user_text})
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise ValueError("DEEPSEEK_API_KEY is required")

    buffer, accepted, length = "", [], 0
    received_delta = completed = False
    async with AsyncOpenAI(api_key=key, base_url=DEEPSEEK_BASE_URL,
                           timeout=30.0, max_retries=0) as client:
        events = await client.responses.create(
            model=DEEPSEEK_MODEL, input=messages, max_output_tokens=4096,
            temperature=0.7, reasoning={"effort": "none"}, stream=True,
        )
        async with events:
            async for event in events:
                kind = _field(event, "type")
                if kind == "response.output_text.delta":
                    delta = _field(event, "delta", "")
                    if not isinstance(delta, str):
                        continue
                    buffer += delta
                    received_delta |= bool(delta)
                elif kind == "response.completed":
                    completed = True
                    if not received_delta:
                        buffer += _completed_text(_field(event, "response"))
                    buffer += "\n"  # The last JSON object need not have a trailing newline.
                elif kind in ("response.failed", "response.incomplete", "error"):
                    response = _field(event, "response")
                    error = _field(response, "error") or _field(event, "error")
                    details = _field(response, "incomplete_details")
                    reason = _field(error, "message") or _field(details, "reason")
                    raise RuntimeError(f"DeepSeek {kind}: {reason or 'reason unavailable'}")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    segment = _segment(line, names)
                    if segment is not None and length + len(segment["text"]) <= 60:
                        length += len(segment["text"])
                        accepted.append(json.dumps(segment, ensure_ascii=False))
                        yield segment
    if not completed:
        raise RuntimeError("DeepSeek stream ended without response.completed")
    if accepted:
        _history.append((user_text, "\n".join(accepted)))
