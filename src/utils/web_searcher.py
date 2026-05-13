import re
import requests
from bs4 import BeautifulSoup
from src.utils.logger import get_logger

logger = get_logger("web_searcher")

TIMEOUT = 10
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}
BILI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com",
    "Cookie": "buvid3=daily-briefing",
}

NETWORK_IP_RE = re.compile(r"(^|[^A-Za-z])IP([^A-Za-z]|$)", re.IGNORECASE)
DOMAIN_ALIASES = {
    "cs_ai_learning": "cs_ai_learning",
    "network": "network",
    "computer_network": "network",
    "ai": "ai",
    "psychology": "psychology",
    "brand": "brand",
    "brand_insight": "brand",
}
DOMAIN_TERMS = {
    "network": ["计算机网络", "网络", "协议", "IP地址", "TCP", "HTTP", "DNS", "服务器", "浏览器", "数据包"],
    "ai": ["AI", "人工智能", "大模型", "Agent", "LLM", "Token", "Embedding", "RAG", "提示词"],
    "psychology": ["心理学", "经济学", "行为", "决策", "效应", "认知", "实验", "消费"],
    "brand": ["品牌", "营销", "商业", "增长", "案例", "产品", "定位", "用户"],
}
EXCLUDE_TERMS = {
    "network": ["知识产权", "专利", "商标", "著作权", "版权", "律所", "法务", "侵权", "IP授权"],
    "ai": ["知识产权", "专利申请", "商标注册"],
    "psychology": [],
    "brand": [],
}
TOKEN_STOPWORDS = {
    "ai", "ip", "http", "https", "tcp", "udp", "dns", "json", "api", "rag", "llm",
    "和", "与", "及", "的", "是", "怎么", "什么", "基础", "入门", "教程",
}


def construct_related_search_query(keyword: str, domain: str | None = None, platform: str = "zhihu") -> str:
    """Build a domain-aware related-reading query.

    The most important guardrail is IP disambiguation: in this project, IP inside
    the CS/AI learning section means Internet Protocol, not intellectual property.
    """
    raw = _clean_keyword(keyword)
    domain_key = _infer_domain(raw, domain)
    terms = DOMAIN_TERMS.get(domain_key, [])
    excludes = EXCLUDE_TERMS.get(domain_key, [])

    if domain_key == "network" and NETWORK_IP_RE.search(raw):
        query_terms = [raw, "计算机网络", "IP地址", "TCP/IP", "数据包"]
    elif domain_key == "cs_ai_learning":
        query_terms = [raw, "计算机网络", "AI Agent", "通俗讲解"]
    elif domain_key in DOMAIN_TERMS:
        query_terms = [raw, *terms[:3], "案例" if domain_key in {"psychology", "brand"} else "教程"]
    else:
        query_terms = [raw]

    query = " ".join(dict.fromkeys(t for t in query_terms if t)).strip()
    if platform == "zhihu":
        for term in excludes:
            query += f" -{term}"
    return query


def is_relevant_search_result(keyword: str, title: str, domain: str | None = None) -> bool:
    raw = _clean_keyword(keyword)
    clean_title = _clean_keyword(title)
    if not raw or not clean_title:
        return False

    domain_key = _infer_domain(raw, domain)
    combined = f"{raw} {clean_title}"
    if any(term in combined for term in EXCLUDE_TERMS.get(domain_key, [])):
        return False

    # "IP" in the network learning section is explicitly Internet Protocol.
    if domain_key == "network" and NETWORK_IP_RE.search(raw):
        if any(term in clean_title for term in ["知识产权", "商标", "专利", "著作权", "版权", "IP授权"]):
            return False
        return any(term in clean_title for term in ["IP", "IP地址", "TCP/IP", "计算机网络", "网络协议", "数据包"])

    keyword_tokens = _keyword_tokens(raw)
    if keyword_tokens and any(token.lower() in clean_title.lower() for token in keyword_tokens):
        return True

    return any(term.lower() in clean_title.lower() for term in DOMAIN_TERMS.get(domain_key, []))


def search_zhihu(keyword: str, count: int = 1, domain: str | None = None) -> list[dict]:
    query = construct_related_search_query(keyword, domain=domain, platform="zhihu")
    try:
        resp = requests.get(
            "https://cn.bing.com/search",
            params={"q": f"site:zhihu.com {query}"},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for a in soup.select("h2 a"):
            href = a.get("href", "")
            title = a.get_text(strip=True)
            if href and title and "zhihu.com" in href and is_relevant_search_result(keyword, title, domain):
                results.append({"title": title, "url": href, "query": query})
                if len(results) >= count:
                    break
        return results
    except Exception as e:
        logger.warning(f"知乎搜索失败 ({keyword}): {e}")
        return []


def search_bilibili(keyword: str, count: int = 1, domain: str | None = None) -> list[dict]:
    query = construct_related_search_query(keyword, domain=domain, platform="bilibili")
    try:
        resp = requests.get(
            "https://api.bilibili.com/x/web-interface/wbi/search/type",
            params={"search_type": "video", "keyword": query, "page": 1},
            headers=BILI_HEADERS,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        items = resp.json().get("data", {}).get("result", [])
        results = []
        for item in items:
            bvid = item.get("bvid", "")
            title = _strip_bili_highlight(item.get("title", ""))
            if bvid and title and is_relevant_search_result(keyword, title, domain):
                results.append({
                    "title": title,
                    "url": f"https://www.bilibili.com/video/{bvid}",
                    "query": query,
                })
                if len(results) >= count:
                    break
        return results
    except Exception as e:
        logger.warning(f"B站搜索失败 ({keyword}): {e}")
        return []


def _infer_domain(keyword: str, domain: str | None = None) -> str:
    domain_key = DOMAIN_ALIASES.get((domain or "").strip(), (domain or "").strip())
    text = f"{keyword} {domain_key}".upper()
    if domain_key == "cs_ai_learning":
        if NETWORK_IP_RE.search(keyword) or any(term in text for term in ["DNS", "HTTP", "TCP", "UDP", "网络", "端口", "COOKIE", "SESSION"]):
            return "network"
        if any(term in text for term in ["AI", "LLM", "TOKEN", "EMBEDDING", "RAG", "AGENT", "大模型", "提示词"]):
            return "ai"
    return domain_key or "general"


def _clean_keyword(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("+", " ")).strip()


def _strip_bili_highlight(value: str) -> str:
    return (
        str(value or "")
        .replace('<em class="keyword">', "")
        .replace("</em>", "")
        .strip()
    )


def _keyword_tokens(value: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]*|[\u4e00-\u9fff]{2,}", value)
    return [t for t in tokens if t.lower() not in TOKEN_STOPWORDS]
