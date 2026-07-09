# -*- coding: utf-8 -*-
"""Semantic de-dup guard for afternoon briefing topics.

Reads previously sent afternoon topics, injects them into LLM prompts, and
records topics from outbound afternoon payloads. This is intentionally generic:
not a single-word blacklist.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

_INSTALLED = False
_CAPTURED: list[str] = []

ALIASES = {
    "宜家效应": "宜家效应 / IKEA effect",
    "ikea effect": "宜家效应 / IKEA effect",
    "ikea-effect": "宜家效应 / IKEA effect",
}

PATTERNS = [
    re.compile(r"(?:主题|标题|topic|title|concept|概念)[:：]\s*([^\n\r|｜]{2,80})", re.I),
    re.compile(r"^#{1,4}\s*([^\n\r]{2,80})", re.M),
    re.compile(r"([\u4e00-\u9fa5A-Za-z0-9·\- ]{2,48}(?:效应|定律|法则|模型|偏差|心理|思维|原则|理论|框架|悖论))"),
]

NOISE = {"午报", "晨报", "结论", "摘要", "建议", "上海", "浦东", "天气", "新闻", "OpenClaw"}


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in [here.parent, *here.parents]:
        if (p / "config").exists() or (p / ".git").exists():
            return p
    return Path.cwd()


def history_file() -> Path:
    path = repo_root() / "data" / "afternoon_topic_history.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def is_afternoon() -> bool:
    hay = " ".join([*sys.argv, os.environ.get("GITHUB_WORKFLOW", ""), os.environ.get("RUN_MODE", ""), os.environ.get("BRIEFING_MODE", "")])
    return "afternoon" in hay.lower() or "午报" in hay


def norm(text: str) -> str:
    s = str(text or "").strip()
    s = re.sub(r"[\s\t\r\n]+", " ", s)
    s = s.strip(" -*#[]()（）【】《》:：|｜,.，。!！?？;；")
    low = s.lower()
    for key, value in ALIASES.items():
        if key in low or key in s:
            return value
    return s


def extract_topics(text: str, limit: int = 80) -> list[str]:
    topics: list[str] = []
    blob = str(text or "")
    for pattern in PATTERNS:
        for match in pattern.finditer(blob):
            item = norm(match.group(1))
            if not item or item in NOISE or len(item) > 80:
                continue
            if item not in topics:
                topics.append(item)
            if len(topics) >= limit:
                return topics
    return topics


def flatten(obj: Any) -> str:
    if isinstance(obj, dict):
        return "\n".join(flatten(v) for v in obj.values())
    if isinstance(obj, list):
        return "\n".join(flatten(v) for v in obj)
    return str(obj)


def history_texts() -> list[str]:
    root = repo_root()
    paths: list[Path] = []
    for rel in [
        "data/afternoon_topic_history.json",
        "send_history.json",
        "sent_history.json",
        "data/send_history.json",
        "data/sent_history.json",
        "logs/send_history.json",
        "config/config.yaml",
    ]:
        p = root / rel
        if p.exists() and p.is_file():
            paths.append(p)
    for base in [root / "data", root / "logs", root / "outputs", root / "reports"]:
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file() and p.suffix.lower() in {".json", ".jsonl", ".md", ".txt"} and p.stat().st_size <= 2_000_000:
                paths.append(p)
    out: list[str] = []
    seen: set[str] = set()
    for p in paths[:100]:
        key = str(p.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
            try:
                text = flatten(json.loads(text))
            except Exception:
                pass
            out.append(text)
        except Exception:
            pass
    return out


def recent_topics(limit: int = 60) -> list[str]:
    topics: list[str] = []
    for text in history_texts():
        if not ("午报" in text or "afternoon" in text.lower() or any(k in text for k in ALIASES)):
            continue
        for topic in extract_topics(text):
            if topic not in topics:
                topics.append(topic)
            if len(topics) >= limit:
                return topics
    seed = "宜家效应 / IKEA effect"
    if seed not in topics:
        topics.append(seed)
    return topics[:limit]


def build_guard_prompt() -> str:
    topics = recent_topics()
    lines = [
        "午报选题语义去重硬规则（P7-20260709）：",
        "下列主题近期已经讲过或作为历史种子；同义词、英文名、近似概念、换标题复述都算重复，必须换新主题。",
        "已讲主题样本：",
    ]
    lines.extend(f"- {topic}" for topic in topics[:60])
    return "\n".join(lines)


def add_guard_to_messages(messages: Any) -> Any:
    if isinstance(messages, list):
        return [*messages, {"role": "user", "content": build_guard_prompt()}]
    return messages


def add_guard_to_input(value: Any) -> Any:
    if isinstance(value, str):
        return value + "\n\n" + build_guard_prompt()
    if isinstance(value, list):
        return [*value, {"role": "user", "content": build_guard_prompt()}]
    return value


def capture_payload(payload: Any) -> None:
    try:
        text = json.dumps(payload, ensure_ascii=False)
    except Exception:
        text = str(payload)
    if "午报" in text or "afternoon" in text.lower() or any(k in text for k in ALIASES):
        _CAPTURED.append(text)


def flush_history() -> None:
    if not _CAPTURED:
        return
    path = history_file()
    try:
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if not isinstance(old, list):
            old = []
    except Exception:
        old = []
    seen = {item.get("topic") for item in old if isinstance(item, dict)}
    for text in _CAPTURED:
        for topic in extract_topics(text):
            if topic not in seen:
                old.append({"topic": topic, "source": "outbound_afternoon", "version": "P7-20260709"})
                seen.add(topic)
    path.write_text(json.dumps(old[-300:], ensure_ascii=False, indent=2), encoding="utf-8")


def install() -> None:
    global _INSTALLED
    if _INSTALLED or not is_afternoon():
        return
    _INSTALLED = True
    os.environ.setdefault("AFTERNOON_TOPIC_DEDUP_GUARD", "1")
    atexit.register(flush_history)

    try:
        from openai.resources.chat.completions import Completions  # type: ignore
        original = Completions.create
        def create(self, *args, **kwargs):
            if "messages" in kwargs:
                kwargs["messages"] = add_guard_to_messages(kwargs.get("messages"))
            return original(self, *args, **kwargs)
        Completions.create = create  # type: ignore
    except Exception:
        pass

    try:
        from openai.resources.responses import Responses  # type: ignore
        original_resp = Responses.create
        def resp_create(self, *args, **kwargs):
            if "input" in kwargs:
                kwargs["input"] = add_guard_to_input(kwargs.get("input"))
            return original_resp(self, *args, **kwargs)
        Responses.create = resp_create  # type: ignore
    except Exception:
        pass

    try:
        import requests  # type: ignore
        original_post = requests.post
        def post(url, *args, **kwargs):
            body = kwargs.get("json")
            if isinstance(url, str) and isinstance(body, dict):
                low = url.lower()
                if "openai" in low or "/v1/" in low:
                    body = dict(body)
                    if "messages" in body:
                        body["messages"] = add_guard_to_messages(body.get("messages"))
                    if "input" in body:
                        body["input"] = add_guard_to_input(body.get("input"))
                    kwargs["json"] = body
                else:
                    capture_payload(body)
            return original_post(url, *args, **kwargs)
        requests.post = post  # type: ignore
    except Exception:
        pass


install()
