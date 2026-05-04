import json
import os
from src.utils.llm_client import chat_completion
from src.utils.logger import get_logger

logger = get_logger("llm_selector")


def select_articles(
    articles: list[dict],
    category: str,
    keywords: list[str] = None,
    count: int = 5,
    model: str = "gpt-5.5",
    api_key: str = None,
    base_url: str = None,
) -> list[dict]:
    if not articles:
        logger.warning("没有文章可供选择")
        return []

    key = api_key or os.getenv("OPENAI_API_KEY", "")
    if not key:
        logger.warning("OPENAI_API_KEY 未设置，使用降级策略（按时间倒序）")
        return _fallback_select(articles, count)

    # 限制候选池，避免把几百上千条旧博客塞给模型。
    candidate_articles = articles[:80]
    article_list = "\n".join(
        f"{i+1}. [{a['title']}]({a['url']}) — {a.get('source', '')} — {a.get('published_at', '')[:10]}"
        for i, a in enumerate(candidate_articles)
    )

    if keywords:
        keyword_hint = f"\n关注领域关键词：{', '.join(keywords)}"
    else:
        keyword_hint = ""

    prompt = f"""你是一位资深新闻编辑。从以下文章列表中选出最有价值的 {count} 篇。

选择标准：
- 影响力：对行业或社会有重大影响
- 时效性：最新最热的动态优先
- 信息增量：能带来新知识或新视角{keyword_hint}

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
            "reason": f"来自{source}，可快速扫一眼" if source else "可快速扫一眼",
            "takeaway": "模型不可用时的保底选稿，优先看标题判断",
        })
    return result
