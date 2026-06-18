import json
import os
from src.utils.llm_client import chat_completion
from src.utils.logger import get_logger
from src.utils.source_quality import source_scores
from src.utils.topic_cluster import cluster_related_articles

logger = get_logger("llm_selector")

SELECTION_STATUS_LLM = "llm"
SELECTION_STATUS_DEGRADED = "degraded"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


LLM_SELECTOR_CANDIDATE_LIMIT = _env_int("LLM_SELECTOR_CANDIDATE_LIMIT", 45)
LLM_SELECTOR_PER_SOURCE_FIRST_PASS = _env_int("LLM_SELECTOR_PER_SOURCE_FIRST_PASS", 4)
LLM_SELECTOR_TITLE_LIMIT = _env_int("LLM_SELECTOR_TITLE_LIMIT", 96)


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

    key = os.getenv("CODEX_API_KEY", "") or api_key or os.getenv("OPENAI_API_KEY", "")
    model = os.getenv("CODEX_MODEL", "") or model or os.getenv("OPENAI_MODEL", "gpt-5.5")
    # 先做跨平台同话题合并，再限制候选池，避免把重复热搜和上千条旧博客塞给模型。
    clustered_articles = cluster_related_articles(articles)
    candidate_limit = max(count * 6, min(LLM_SELECTOR_CANDIDATE_LIMIT, 80))
    per_source_first_pass = max(1, min(LLM_SELECTOR_PER_SOURCE_FIRST_PASS, 6))
    candidate_articles = _prepare_candidates(
        clustered_articles,
        limit=candidate_limit,
        per_source_first_pass=per_source_first_pass,
        source_quality=source_quality,
    )

    if not key:
        logger.warning("CODEX_API_KEY/OPENAI_API_KEY 未设置，使用降级策略（来源均衡候选）")
        return _fallback_select(
            candidate_articles,
            count,
            error_reason="CODEX_API_KEY/OPENAI_API_KEY 未设置",
            source_quality=source_quality,
        )

    # 同时做来源均衡：TrendRadar/NewsNow 热榜源更广，但不能让某一个平台刷屏。
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
- 只能按下面的 index 选择，不要自己编造或改写链接
- 每条只返回 index、reason、takeaway
- reason 控制在 35 字以内：说明为什么值得看
- takeaway 控制在 45 字以内：给出明确判断，别官腔

文章列表：
{article_list}

请严格返回 JSON 数组，格式如下，不要返回任何其他内容：
[{{"index": 1, "reason": "短看点", "takeaway": "短判断"}}]

只返回 {count} 篇，不多不少。"""

    try:
        content = chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=0.3,
            max_tokens=min(900, 220 + count * 120),
        )
        content = _strip_json_fence(content)
        selected = json.loads(content)
        if isinstance(selected, list) and len(selected) > 0:
            logger.info(f"GPT 选稿完成（{category}）：{len(selected)} 篇")
            hydrated = _hydrate_selection(selected[:count], candidate_articles)
            if len(hydrated) >= count:
                return hydrated[:count]
            logger.warning("GPT 返回有效候选不足，使用来源均衡补齐")
            return _fill_missing_selections(
                hydrated,
                candidate_articles,
                count,
                error_reason="GPT 返回有效候选不足",
                source_quality=source_quality,
            )
        else:
            logger.warning("GPT 返回格式异常，使用来源均衡降级策略")
            return _fallback_select(
                candidate_articles,
                count,
                error_reason="GPT 返回格式异常",
                source_quality=source_quality,
            )
    except json.JSONDecodeError as e:
        logger.warning(f"GPT 返回 JSON 解析失败 ({e})，使用来源均衡降级策略")
        return _fallback_select(
            candidate_articles,
            count,
            error_reason=f"JSON 解析失败: {e}",
            source_quality=source_quality,
        )
    except Exception as e:
        logger.warning(f"GPT 调用失败 ({e})，使用来源均衡降级策略")
        return _fallback_select(
            candidate_articles,
            count,
            error_reason=f"{type(e).__name__}: {e}",
            source_quality=source_quality,
        )


def _strip_json_fence(content: str) -> str:
    content = content.strip()
    if content.startswith("```json"):
        content = content[len("```json"):]
    elif content.startswith("```"):
        content = content[len("```"):]
    if content.endswith("```"):
        content = content[:-len("```")]
    return content.strip()


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
    title = str(article.get("title", "")).strip().replace("\n", " ")[:LLM_SELECTOR_TITLE_LIMIT]
    meta = " — ".join(part for part in [source, source_type, rank_text, related_text, date] if part)
    return f"{index}. {title} — {meta}"


def _hydrate_selection(selected: list[dict], articles: list[dict]) -> list[dict]:
    by_url = {a.get("url", ""): a for a in articles}
    hydrated = []
    seen_urls: set[str] = set()
    for item in selected:
        if not isinstance(item, dict):
            continue
        original = {}
        index = _parse_selection_index(item.get("index"), len(articles))
        if index is not None:
            original = articles[index - 1]
        else:
            url = str(item.get("url", "")).strip()
            original = by_url.get(url, {}) if url else {}

        url = original.get("url", "") or str(item.get("url", "")).strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        hydrated.append({
            "title": original.get("title", item.get("title", "")),
            "url": url,
            "source": original.get("source", item.get("source", "")),
            "published_at": original.get("published_at", ""),
            "rank": original.get("rank"),
            "source_type": original.get("source_type", ""),
            "related_sources": original.get("related_sources", []),
            "related_urls": original.get("related_urls", []),
            "topic_cluster_size": original.get("topic_cluster_size", 1),
            "multi_source_label": original.get("multi_source_label", ""),
            "reason": str(item.get("reason", "")).strip(),
            "takeaway": str(item.get("takeaway", "")).strip(),
            "selection_status": SELECTION_STATUS_LLM,
            "selection_error": "",
        })
    return hydrated


def _fallback_select(
    articles: list[dict],
    count: int,
    error_reason: str = "模型选稿异常",
    source_quality: dict = None,
) -> list[dict]:
    logger.info(f"降级策略：来源均衡取前 {count} 篇")
    # 关键修复：降级时每个来源先拿 1 条，再补齐，避免今日头条/量子位这类单源霸屏。
    candidates = _prepare_candidates(
        articles,
        limit=max(count * 4, count),
        per_source_first_pass=1,
        source_quality=source_quality,
    )
    return _build_degraded_selection(candidates, count, error_reason)


def _fallback_reason(article: dict) -> str:
    source = article.get("source", "")
    rank = article.get("rank")
    related_sources = article.get("related_sources") or []
    if len(related_sources) > 1:
        return "多源同热，降级候选"
    if article.get("source_type") == "hotlist" and source and rank:
        return f"{source}热榜#{rank}，降级候选"
    if source:
        return f"{source}新近内容，降级候选"
    return "来源均衡降级候选"


def _parse_selection_index(value, articles_len: int) -> int | None:
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    if 1 <= index <= articles_len:
        return index
    return None


def _build_degraded_selection(candidates: list[dict], count: int, error_reason: str) -> list[dict]:
    result = []
    for a in candidates:
        source = a.get("source", "")
        result.append({
            "title": a.get("title", ""),
            "url": a.get("url", ""),
            "source": source,
            "published_at": a.get("published_at", ""),
            "rank": a.get("rank"),
            "source_type": a.get("source_type", ""),
            "related_sources": a.get("related_sources", []),
            "related_urls": a.get("related_urls", []),
            "topic_cluster_size": a.get("topic_cluster_size", 1),
            "multi_source_label": a.get("multi_source_label", ""),
            "reason": _fallback_reason(a),
            "takeaway": "",
            "selection_status": SELECTION_STATUS_DEGRADED,
            "selection_error": error_reason,
        })
        if len(result) >= count:
            break
    return result


def _fill_missing_selections(
    hydrated: list[dict],
    articles: list[dict],
    count: int,
    error_reason: str,
    source_quality: dict = None,
) -> list[dict]:
    seen_urls = {item.get("url", "") for item in hydrated if item.get("url")}
    remaining = [article for article in articles if article.get("url", "") not in seen_urls]
    if not remaining:
        return hydrated[:count]
    filler = _fallback_select(remaining, count - len(hydrated), error_reason=error_reason, source_quality=source_quality)
    combined = list(hydrated)
    combined.extend(filler)
    return combined[:count]
