import json
from pathlib import Path
from typing import Any

from src.utils.logger import get_logger

logger = get_logger("last30days_fetcher")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_PATH = PROJECT_ROOT / "data" / "last30days_findings.json"


def load_last30days_findings(
    path: str | Path | None = None,
    max_items: int | None = None,
) -> list[dict]:
    """Load cached last30days findings without making briefing delivery depend on it."""
    cache_path = Path(path) if path else DEFAULT_CACHE_PATH
    if not cache_path.exists():
        return []

    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"last30days 缓存读取失败，已跳过旁路情报: {exc}")
        return []

    return normalize_last30days_payload(payload, max_items=max_items)


def normalize_last30days_payload(
    payload: Any,
    *,
    topic: str = "",
    label: str = "",
    max_items: int | None = None,
) -> list[dict]:
    if not isinstance(payload, dict):
        return []

    items: list[dict] = []
    if isinstance(payload.get("items"), list):
        items.extend(_normalize_items(payload["items"], topic=topic, label=label))

    if isinstance(payload.get("topics"), list):
        for topic_payload in payload["topics"]:
            if not isinstance(topic_payload, dict):
                continue
            topic_name = topic_payload.get("name") or topic_payload.get("topic") or topic
            topic_label = topic_payload.get("label") or label
            if isinstance(topic_payload.get("items"), list):
                items.extend(
                    _normalize_items(
                        topic_payload["items"],
                        topic=topic_name,
                        label=topic_label,
                    )
                )
            raw_report = topic_payload.get("raw_report")
            if isinstance(raw_report, dict):
                items.extend(
                    normalize_last30days_payload(
                        raw_report,
                        topic=topic_name,
                        label=topic_label,
                    )
                )

    items.extend(_normalize_ranked_candidates(payload, topic=topic, label=label))
    if not items:
        items.extend(_normalize_items_by_source(payload, topic=topic, label=label))

    deduped = _dedupe_items(items)
    if max_items is not None:
        return deduped[:max_items]
    return deduped


def _normalize_ranked_candidates(payload: dict, *, topic: str, label: str) -> list[dict]:
    candidates = payload.get("ranked_candidates")
    if not isinstance(candidates, list):
        return []

    items = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        source_items = candidate.get("source_items") if isinstance(candidate.get("source_items"), list) else []
        primary = source_items[0] if source_items and isinstance(source_items[0], dict) else {}
        source = candidate.get("source") or primary.get("source") or ""
        sources = candidate.get("sources")
        source_label = ", ".join(sources) if isinstance(sources, list) and sources else source
        items.append(
            _clean_item(
                {
                    "title": candidate.get("title"),
                    "url": candidate.get("url") or primary.get("url"),
                    "source": source_label,
                    "topic": topic,
                    "label": label,
                    "summary": candidate.get("snippet")
                    or candidate.get("explanation")
                    or primary.get("snippet")
                    or primary.get("why_relevant")
                    or primary.get("body"),
                    "engagement": candidate.get("engagement") or primary.get("engagement"),
                    "published_at": primary.get("published_at"),
                    "score": candidate.get("final_score") or candidate.get("rerank_score"),
                }
            )
        )
    return [item for item in items if item]


def _normalize_items_by_source(payload: dict, *, topic: str, label: str) -> list[dict]:
    items_by_source = payload.get("items_by_source")
    if not isinstance(items_by_source, dict):
        return []

    items = []
    for source, source_items in items_by_source.items():
        if not isinstance(source_items, list):
            continue
        for source_item in source_items:
            if not isinstance(source_item, dict):
                continue
            item = dict(source_item)
            item.setdefault("source", source)
            items.append(_clean_item({**item, "topic": topic, "label": label}))
    return [item for item in items if item]


def _normalize_items(items: list, *, topic: str, label: str) -> list[dict]:
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized.append(
            _clean_item(
                {
                    **item,
                    "topic": item.get("topic") or topic,
                    "label": item.get("label") or label,
                }
            )
        )
    return [item for item in normalized if item]


def _clean_item(item: dict) -> dict:
    title = _text(item.get("title"))
    if not title:
        return {}
    return {
        "title": title,
        "url": _text(item.get("url")),
        "source": _text(item.get("source")),
        "topic": _text(item.get("topic")),
        "label": _text(item.get("label")),
        "summary": _trim(
            _text(
                item.get("summary")
                or item.get("snippet")
                or item.get("why_relevant")
                or item.get("body")
                or item.get("description")
                or item.get("explanation")
            ),
            180,
        ),
        "engagement": item.get("engagement") if isinstance(item.get("engagement"), dict) else {},
        "published_at": _text(item.get("published_at")),
        "score": item.get("score"),
    }


def _dedupe_items(items: list[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for item in items:
        key = item.get("url") or f"{item.get('source')}::{item.get('title')}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _trim(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "…"
