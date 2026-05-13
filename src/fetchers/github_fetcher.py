import os
import re
import requests
from bs4 import BeautifulSoup
from src.utils.logger import get_logger

logger = get_logger("github_fetcher")

TRENDING_URL = "https://github.com/trending"
GITHUB_API_URL = "https://api.github.com/repos"
TIMEOUT = 10
README_EXCERPT_LIMIT = 1200
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}
API_HEADERS = {
    **HEADERS,
    "Accept": "application/vnd.github+json",
}

PREFERRED_LANGS = {
    "Python", "TypeScript", "JavaScript", "Go", "Rust", "Swift", "Kotlin",
    "Jupyter Notebook", "Shell",
}

SIGNAL_KEYWORDS = {
    "ai", "agent", "llm", "mcp", "automation", "workflow", "browser",
    "data", "tool", "cli", "developer", "openai", "claude", "rag",
}


def fetch_trending_repos(count: int = 5, candidate_count: int = 15) -> list[dict]:
    try:
        logger.info("GitHub Trending: 正在抓取")
        resp = requests.get(TRENDING_URL, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        articles = soup.select("article.Box-row")
        repos = []
        for art in articles[:candidate_count]:
            h2 = art.select_one("h2 a")
            if not h2:
                continue
            repo_path = h2.get("href", "").strip().lstrip("/")
            desc_p = art.select_one("p")
            desc = desc_p.get_text(" ", strip=True) if desc_p else ""
            lang_span = art.select_one("span[itemprop='programmingLanguage']")
            lang = lang_span.get_text(strip=True) if lang_span else ""
            stars_span = art.select("span.d-inline-block.float-sm-right")
            stars_today = stars_span[0].get_text(strip=True) if stars_span else ""
            repo = {
                "name": repo_path,
                "url": f"https://github.com/{repo_path}",
                "description": desc,
                "language": lang,
                "stars_today": stars_today,
            }
            repo["quality_score"] = _score_repo(repo)
            repos.append(repo)

        repos = sorted(repos, key=lambda r: r.get("quality_score", 0), reverse=True)[:count]
        repos = [enrich_repo_metadata(repo) for repo in repos]
        if not repos:
            logger.warning("GitHub Trending: 未获取到候选项目")
            return [_degraded_repo("github_trending_empty")]
        logger.info(f"GitHub Trending: 获取 {len(repos)} 个项目（候选 {len(articles[:candidate_count])} 个）")
        return repos
    except Exception as e:
        logger.warning(f"GitHub Trending 抓取失败: {e}")
        return [_degraded_repo(f"github_trending_fetch_failed: {type(e).__name__}: {e}")]


def enrich_repo_metadata(repo: dict) -> dict:
    """Add lightweight deep-read metadata for LLM evaluation.

    We only use public GitHub endpoints and keep failures explicit. A repo with a
    degraded deep read must not be presented as if README/API context was checked.
    """
    enriched = dict(repo)
    repo_name = enriched.get("name", "")
    if not _looks_like_repo_path(repo_name):
        return _mark_deep_read_degraded(enriched, "invalid_repo_path")

    try:
        meta = _fetch_repo_api(repo_name)
        enriched.update({
            "description": meta.get("description") or enriched.get("description", ""),
            "license": (meta.get("license") or {}).get("spdx_id") or "",
            "topics": meta.get("topics") or [],
            "updated_at": meta.get("updated_at", ""),
            "open_issues_count": meta.get("open_issues_count"),
            "stars": meta.get("stargazers_count"),
            "forks": meta.get("forks_count"),
        })
        readme_excerpt, readme_error = _fetch_readme_excerpt(repo_name)
        enriched["readme_excerpt"] = readme_excerpt
        if readme_error:
            enriched["readme_error"] = readme_error
        enriched["deep_read_status"] = "ok"
        enriched["deep_read_error"] = ""
        return enriched
    except Exception as e:
        logger.warning(f"GitHub 深读失败 ({repo_name}): {e}")
        return _mark_deep_read_degraded(enriched, f"github_deep_read_failed: {type(e).__name__}: {e}")


def _fetch_repo_api(repo_name: str) -> dict:
    headers = _github_api_headers()
    resp = requests.get(f"{GITHUB_API_URL}/{repo_name}", headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _fetch_readme_excerpt(repo_name: str) -> tuple[str, str]:
    headers = {
        **_github_api_headers(),
        "Accept": "application/vnd.github.raw+json",
    }
    try:
        resp = requests.get(f"{GITHUB_API_URL}/{repo_name}/readme", headers=headers, timeout=TIMEOUT)
        if resp.status_code == 404:
            return "", "readme_not_found"
        resp.raise_for_status()
        return resp.text[:README_EXCERPT_LIMIT], ""
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"readme_fetch_failed: {type(e).__name__}: {e}") from e


def _github_api_headers() -> dict:
    headers = dict(API_HEADERS)
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _degraded_repo(error: str) -> dict:
    return {
        "name": "GitHub Trending",
        "url": "",
        "description": "GitHub Trending 获取失败，已按降级处理。",
        "language": "",
        "stars_today": "",
        "summary": "GitHub Trending 获取失败，已阻断正式午报发送，避免伪装成项目深读。",
        "generation_status": "degraded",
        "generation_error": error,
        "deep_read_status": "degraded",
        "deep_read_error": error,
        "quality_score": 0,
    }


def _mark_deep_read_degraded(repo: dict, error: str) -> dict:
    repo["deep_read_status"] = "degraded"
    repo["deep_read_error"] = error
    repo.setdefault("readme_excerpt", "")
    repo.setdefault("license", "")
    repo.setdefault("topics", [])
    return repo


def _score_repo(repo: dict) -> float:
    text = f"{repo.get('name', '')} {repo.get('description', '')}".lower()
    score = 0.0

    if repo.get("description"):
        score += 2.0
    if repo.get("language") in PREFERRED_LANGS:
        score += 1.2

    for keyword in SIGNAL_KEYWORDS:
        if keyword in text:
            score += 0.8

    stars_today = _parse_stars_today(repo.get("stars_today", ""))
    if stars_today:
        score += min(2.0, stars_today / 250)

    # 太像纯资源合集，午报里通常信息密度偏低，轻微降权，不一刀切。
    if any(word in text for word in ["awesome", "list", "collection"]):
        score -= 0.6

    return round(score, 3)


def _parse_stars_today(value: str) -> int:
    match = re.search(r"([\d,]+)", value or "")
    if not match:
        return 0
    return int(match.group(1).replace(",", ""))


def _looks_like_repo_path(value: str) -> bool:
    return bool(re.match(r"^[^/\s]+/[^/\s]+$", value or ""))
