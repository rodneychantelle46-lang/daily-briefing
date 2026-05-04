import re
import requests
from bs4 import BeautifulSoup
from src.utils.logger import get_logger

logger = get_logger("github_fetcher")

TRENDING_URL = "https://github.com/trending"
TIMEOUT = 10
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
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
        logger.info(f"GitHub Trending: 获取 {len(repos)} 个项目（候选 {len(articles[:candidate_count])} 个）")
        return repos
    except Exception as e:
        logger.warning(f"GitHub Trending 抓取失败: {e}")
        return []


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
