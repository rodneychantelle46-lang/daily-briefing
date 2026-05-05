import html
import re
from difflib import SequenceMatcher
from typing import Iterable
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from src.utils.logger import get_logger

logger = get_logger("link_validator")

TIMEOUT = 12
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 DailyBriefing/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

GENERIC_TITLES = {
    "华尔街见闻",
    "wallstreetcn",
    "哔哩哔哩_bilibili",
    "bilibili",
}

SEARCH_HOSTS = {
    "search.bilibili.com",
}


def validate_article_links(articles: Iterable[dict], *, min_similarity: float = 0.45) -> tuple[list[dict], list[dict]]:
    """Keep articles whose URL looks like a real content page matching the title.

    The validator is intentionally conservative:
    - confirmed title mismatches and known search/result pages are rejected;
    - fetch errors and JS-only generic titles are kept but annotated, because a flaky
      publisher page should not silently empty a briefing.
    """
    valid: list[dict] = []
    reports: list[dict] = []

    for article in articles:
        checked = dict(article)
        report = validate_article_link(checked, min_similarity=min_similarity)
        checked["link_validation"] = report
        reports.append(report)

        if report["status"] == "reject":
            logger.warning(
                "链接校验剔除: %s | %s | %s",
                article.get("title", ""),
                article.get("url", ""),
                report.get("reason", ""),
            )
            continue
        valid.append(checked)

    return valid, reports


def validate_article_link(article: dict, *, min_similarity: float = 0.45) -> dict:
    title = str(article.get("title") or "").strip()
    url = str(article.get("url") or "").strip()
    report = {
        "title": title,
        "url": url,
        "status": "keep",
        "reason": "",
        "page_title": "",
        "final_url": url,
        "similarity": None,
    }

    if not title or not url:
        report.update(status="reject", reason="标题或链接为空")
        return report

    parsed = urlparse(url)
    if parsed.netloc.lower() in SEARCH_HOSTS:
        report.update(status="reject", reason="搜索页不是内容详情页")
        return report

    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        report["final_url"] = resp.url
        report["http_status"] = resp.status_code
        resp.raise_for_status()
    except Exception as exc:
        report.update(status="keep", reason=f"抓取失败，保守保留: {exc}")
        return report

    content_type = resp.headers.get("content-type", "").lower()
    if content_type and "html" not in content_type and "text" not in content_type:
        report.update(status="keep", reason=f"非 HTML 内容，保守保留: {content_type}")
        return report

    page_title = _extract_page_title(resp.text)
    report["page_title"] = page_title
    if not page_title:
        report.update(status="keep", reason="未提取到页面标题，保守保留")
        return report

    if _is_generic_title(page_title):
        report.update(status="keep", reason="页面只返回站点级标题，保守保留")
        return report

    similarity = _title_similarity(title, page_title)
    report["similarity"] = round(similarity, 3)
    if similarity < min_similarity:
        report.update(status="reject", reason="标题与落地页标题不一致")
    else:
        report.update(status="keep", reason="标题与落地页标题一致")
    return report


def _extract_page_title(text: str) -> str:
    soup = BeautifulSoup(text or "", "html.parser")
    for selector in (
        {"property": "og:title"},
        {"name": "twitter:title"},
        {"name": "title"},
    ):
        tag = soup.find("meta", attrs=selector)
        if tag and tag.get("content"):
            return _clean_title(tag.get("content", ""))
    if soup.title and soup.title.string:
        return _clean_title(soup.title.string)
    return ""


def _clean_title(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _normalize_title(value: str) -> str:
    value = html.unescape(value or "").lower()
    # Keep CJK, letters and digits; drop punctuation and common site suffix noise.
    value = re.sub(r"[_\-—|丨].*$", "", value)
    return "".join(ch for ch in value if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _is_generic_title(page_title: str) -> bool:
    normalized = _normalize_title(page_title)
    return normalized in {_normalize_title(t) for t in GENERIC_TITLES} or len(normalized) <= 4


def _title_similarity(expected: str, actual: str) -> float:
    expected_norm = _normalize_title(expected)
    actual_norm = _normalize_title(actual)
    if not expected_norm or not actual_norm:
        return 0.0
    if expected_norm in actual_norm or actual_norm in expected_norm:
        return 1.0
    sequence_score = SequenceMatcher(None, expected_norm, actual_norm).ratio()
    overlap_score = sum(1 for ch in expected_norm if ch in actual_norm) / max(len(expected_norm), 1)
    return max(sequence_score, overlap_score)
