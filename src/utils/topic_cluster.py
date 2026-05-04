import re
from collections import Counter
from typing import Iterable

from src.utils.logger import get_logger

logger = get_logger("topic_cluster")

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
WORD_RE = re.compile(r"[a-z0-9]+")
PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)

STOP_WORDS = {
    "的", "了", "和", "与", "及", "在", "是", "被", "将", "对", "等", "中",
    "回应", "官方", "最新", "突然", "宣布", "网友", "热", "热搜",
}


def cluster_related_articles(articles: list[dict], threshold: float = 0.48) -> list[dict]:
    """Merge near-duplicate stories across platforms.

    NewsNow expands the source surface, but the same event often appears on
    Weibo/Baidu/Toutiao at once. This lightweight heuristic keeps one canonical
    item and annotates it with related sources instead of making the LLM choose
    between duplicated headlines.
    """
    groups: list[dict] = []

    for article in _sort_articles(articles):
        title = article.get("title", "")
        tokens = _title_tokens(title)
        if not tokens:
            groups.append({"tokens": set(), "items": [article]})
            continue

        best_group = None
        best_score = 0.0
        for group in groups:
            score = _similarity(tokens, group["tokens"])
            if score > best_score:
                best_score = score
                best_group = group

        if best_group is not None and best_score >= threshold:
            best_group["items"].append(article)
            best_group["tokens"] |= tokens
        else:
            groups.append({"tokens": set(tokens), "items": [article]})

    merged = [_merge_group(group["items"]) for group in groups]
    removed = len(articles) - len(merged)
    if removed > 0:
        logger.info(f"同话题合并: {len(articles)} 条 → {len(merged)} 条，合并 {removed} 条跨平台重复")
    return merged


def _sort_articles(articles: Iterable[dict]) -> list[dict]:
    def key(article: dict) -> tuple:
        source_type_rank = 0 if article.get("source_type") == "hotlist" else 1
        rank = article.get("rank")
        rank_value = rank if isinstance(rank, int) else 999
        return (source_type_rank, rank_value, article.get("source", ""))

    return sorted(articles, key=key)


def _merge_group(items: list[dict]) -> dict:
    best = _sort_articles(items)[0].copy()
    related_sources = []
    related_urls = []
    for item in items:
        source = item.get("source", "")
        if source and source not in related_sources:
            related_sources.append(source)
        url = item.get("url", "")
        if url and url not in related_urls:
            related_urls.append(url)

    best["related_sources"] = related_sources
    best["related_urls"] = related_urls
    best["topic_cluster_size"] = len(items)
    if len(related_sources) > 1:
        best["multi_source_label"] = f"{related_sources[0]}等{len(related_sources)}源"
    return best


def _title_tokens(title: str) -> set[str]:
    normalized = title.lower()
    normalized = re.sub(r"https?://\S+", "", normalized)
    normalized = PUNCT_RE.sub(" ", normalized)

    tokens = set(WORD_RE.findall(normalized))
    cjk_chars = [ch for ch in normalized if CJK_RE.match(ch) and ch not in STOP_WORDS]
    tokens.update(cjk_chars)
    tokens.update("".join(cjk_chars[i:i + 2]) for i in range(max(0, len(cjk_chars) - 1)))
    return {t for t in tokens if t and t not in STOP_WORDS}


def _similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    jaccard = intersection / union if union else 0.0
    containment = intersection / min(len(a), len(b))
    return max(jaccard, containment * 0.82)
