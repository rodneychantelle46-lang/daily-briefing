import json
import random
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import requests

from src.utils.logger import get_logger

logger = get_logger("newsnow_fetcher")

DEFAULT_API_URL = "https://newsnow.busiyi.world/api/s"
TIMEOUT = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 DailyBriefing/1.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
}


def fetch_hotlists(config: dict[str, Any] | None) -> list[dict]:
    """Fetch multi-platform hotlists via the public NewsNow-compatible API.

    This borrows TrendRadar's useful idea: use NewsNow as a broad hotlist
    aggregator. It deliberately avoids importing TrendRadar's database,
    scheduler, MCP, multi-channel notification, and heavy AI-filter stack.
    """
    if not config or not config.get("enabled", False):
        return []

    api_url = config.get("api_url", DEFAULT_API_URL) or DEFAULT_API_URL
    sources = [s for s in config.get("sources", []) if s.get("enabled", True)]
    max_per_source = int(config.get("max_per_source", 12))
    request_interval_ms = int(config.get("request_interval_ms", 300))
    max_retries = int(config.get("max_retries", 2))

    articles: list[dict] = []
    failed: list[str] = []

    for index, source in enumerate(sources):
        if index > 0:
            jitter = random.randint(-80, 120)
            time.sleep(max(80, request_interval_ms + jitter) / 1000)

        source_items = _fetch_source(api_url, source, max_per_source, max_retries)
        if source_items is None:
            failed.append(source.get("id", "unknown"))
            continue
        articles.extend(source_items)

    articles = _dedupe_articles(articles)
    logger.info(f"NewsNow 热榜抓取完成：{len(articles)} 条，失败源：{failed or '无'}")
    return articles


def _fetch_source(api_url: str, source: dict[str, Any], max_per_source: int, max_retries: int) -> list[dict] | None:
    source_id = source.get("id", "").strip()
    source_name = source.get("name", source_id).strip() or source_id
    category = source.get("category", "热榜")
    if not source_id:
        return []

    url = f"{api_url}?id={quote_plus(source_id)}&latest"
    last_error = ""

    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status", "")
            if status not in ("success", "cache"):
                raise ValueError(f"响应状态异常：{status}")

            items = []
            updated_at = _parse_updated_time(data.get("updatedTime"))
            for rank, item in enumerate(data.get("items", []), 1):
                if len(items) >= max_per_source:
                    break
                title = str(item.get("title") or item.get("id") or "").strip()
                if not title:
                    continue
                item_url = str(item.get("url") or item.get("mobileUrl") or "").strip()
                if not item_url:
                    continue
                items.append({
                    "title": title,
                    "url": item_url,
                    "source": source_name,
                    "category": category,
                    "source_type": "hotlist",
                    "rank": rank,
                    "hot_score": _extract_hot_score(item),
                    "published_at": updated_at,
                })

            logger.info(f"{source_name}: 获取 {len(items)} 条热榜（{status}）")
            return items
        except Exception as e:
            last_error = str(e)
            if attempt < max_retries:
                wait = random.uniform(1.0, 2.5) + attempt
                logger.warning(f"{source_name}: 抓取失败 ({e})，{wait:.1f}s 后重试")
                time.sleep(wait)

    logger.warning(f"{source_name}: 抓取失败，已跳过 ({last_error})")
    return None


def _parse_updated_time(value: Any) -> str:
    if isinstance(value, (int, float)) and value > 0:
        # NewsNow returns milliseconds.
        timestamp = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _extract_hot_score(item: dict[str, Any]) -> str:
    extra = item.get("extra") or {}
    for key in ("hot", "heat", "score", "num", "desc"):
        value = extra.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _dedupe_articles(articles: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for article in articles:
        key = article.get("url") or f"{article.get('source')}::{article.get('title')}"
        if key in seen:
            continue
        seen.add(key)
        result.append(article)
    return result
