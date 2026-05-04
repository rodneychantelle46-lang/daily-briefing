import json
import os
from src.utils.llm_client import chat_completion
from src.utils.logger import get_logger
from src.utils.source_quality import source_scores
from src.utils.topic_cluster import cluster_related_articles

logger = get_logger("llm_selector")


def select_articles(
    articles: list[dict],
    category: str,
    keywords: list[str] = None,
    count: int = 5,
    model: str = "gpt-5.5",
    api_key: str = None,
    base_url: str = None,
    source_quality: dict = None,
) -> list[dict]:
    if not articles:
        logger.warning("没有文章可供选择")
        return []

    key = api_key or os.getenv("OPENAI_API_KEY", "")
    # 先做跨平台同话题合并，再限制候选池，避免把重复热搜和上千条旧博客塞给模型。
    clustered_articles = cluster_related_articles(articles)

    if not key:
        logger.warning("OPENAI_API_KEY 未设置，使用降级策略（按时间倒序）")
        return _fallback_select(clustered_articles, count)

    # 同时做来源均衡：TrendRadar/NewsNow 热榜源更广，但不能让某一个平台刷屏。
    candidate_articles = _prepare_candidates(clustered_articles, limit=80, per_source_first_pass=6, source_quality=source_quality)
    article_list = "\n".join(
        _format_candidate_line(i + 1, a)
        for i, a in enumerate(candidate_articles)
    )

    if keywords:
        keyword_hint = f"\n关注领域关键词：{', '.join(keywords)}"
    else:
        keyword_hint = ""

    prompt = f"""你是一位资深新闻编辑。从以下文章列表中选出最有价值的 {count} 篇。

选择标准：
- 影响力：对行业或社会有重大影响
- 时效性：最新最热的动态优先，热榜高排名可加权但不要被娱乐噪音带跑
- 信息增量：能带来新知识或新视角
- 来源多样：尽量避免同一平台/同一话题刷屏{keyword_hint}

输出要求：
- 不要只复制标题，要补一条短看点和一条短判断
- reason 控制在 35 字以内：说明为什么值得看
- takeaway 控制在 45 字以内：给出明确判断，别官腔

文章列表：
{article_list}

请严格返回 JSON 数组，格式如下，不要返回任何其他内容：
[{{"title": "文章标题", "url": "文章链接", "reason": "短看点", "takeaway": "短判断"}}]

只返回 {count} 篇，不多不少。"""

    try:
        content = chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=0.3,
            max_tokens=1400,
        )
        content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        selected = json.loads(content)
        if isinstance(selected, list) and len(selected) > 0:
            logger.info(f"GPT 选稿完成（{category}）：{len(selected)} 篇")
            return _hydrate_selection(selected[:count], candidate_articles)
        else:
            logger.warning("GPT 返回格式异常，使用降级策略")
            return _fallback_select(articles, count)
    except json.JSONDecodeError as e:
        logger.warning(f"GPT 返回 JSON 解析失败 ({e})，使用降级策略")
        return _fallback_select(articles, count)
    except Exception as e:
        logger.warning(f"GPT 调用失败 ({e})，使用降级策略")
        return _fallback_select(articles, count)


def _prepare_candidates(
    articles: list[dict],
    limit: int = 80,
    per_source_first_pass: int = 6,
    source_quality: dict = None,
) -> list[dict]:
    scores = source_scores(source_quality or {})

    def priority(article: dict) -> tuple:
        source_type_rank = 0 if article.get("source_type") == "hotlist" else 1
        rank = article.get("rank")
        rank_value = rank if isinstance(rank, int) else 999
        quality = scores.get(article.get("source", ""), 0.85)
        cluster_bonus = min(3, int(article.get("topic_cluster_size", 1)))
        return (source_type_rank, -cluster_bonus, -quality, rank_value)

    sorted_articles = sorted(articles, key=priority)
    buckets: dict[str, list[dict]] = {}
    for article in sorted_articles:
        buckets.setdefault(article.get("source", "未知来源"), []).append(article)

    selected: list[dict] = []
    seen_urls: set[str] = set()

    for source_articles in buckets.values():
        for article in source_articles[:per_source_first_pass]:
            url = article.get("url", "")
            if url and url not in seen_urls:
                selected.append(article)
                seen_urls.add(url)
                if len(selected) >= limit:
                    return selected

    for article in sorted_articles:
        url = article.get("url", "")
        if url and url not in seen_urls:
            selected.append(article)
            seen_urls.add(url)
            if len(selected) >= limit:
                break
    return selected


def _format_candidate_line(index: int, article: dict) -> str:
    source = article.get("source", "")
    date = article.get("published_at", "")[:10]
    rank = article.get("rank")
    rank_text = f"排行#{rank}" if rank else ""
    source_type = "热榜" if article.get("source_type") == "hotlist" else "RSS"
    related_sources = article.get("related_sources") or []
    related_text = f"多平台同热:{'/'.join(related_sources[:4])}" if len(related_sources) > 1 else ""
    meta = " — ".join(part for part in [source, source_type, rank_text, related_text, date] if part)
    return f"{index}. [{article['title']}]({article['url']}) — {meta}"


def _hydrate_selection(selected: list[dict], articles: list[dict]) -> list[dict]:
    by_url = {a.get("url", ""): a for a in articles}
    hydrated = []
    for item in selected:
        url = item.get("url", "")
        original = by_url.get(url, {})
        hydrated.append({
            "title": item.get("title") or original.get("title", ""),
            "url": url or original.get("url", ""),
            "source": original.get("source", item.get("source", "")),
            "published_at": original.get("published_at", ""),
            "rank": original.get("rank"),
            "source_type": original.get("source_type", ""),
            "related_sources": original.get("related_sources", []),
            "related_urls": original.get("related_urls", []),
            "topic_cluster_size": original.get("topic_cluster_size", 1),
            "multi_source_label": original.get("multi_source_label", ""),
            "reason": item.get("reason", "").strip(),
            "takeaway": item.get("takeaway", "").strip(),
        })
    return hydrated


def _fallback_select(articles: list[dict], count: int) -> list[dict]:
    logger.info(f"降级策略：取前 {count} 篇")
    result = []
    for a in articles[:count]:
        source = a.get("source", "")
        result.append({
            "title": a["title"],
            "url": a["url"],
            "source": source,
            "published_at": a.get("published_at", ""),
            "rank": a.get("rank"),
            "source_type": a.get("source_type", ""),
            "related_sources": a.get("related_sources", []),
            "related_urls": a.get("related_urls", []),
            "topic_cluster_size": a.get("topic_cluster_size", 1),
            "multi_source_label": a.get("multi_source_label", ""),
            "reason": f"来自{source}，可快速扫一眼" if source else "可快速扫一眼",
            "takeaway": "模型不可用时的保底选稿，优先看标题判断",
        })
    return result
