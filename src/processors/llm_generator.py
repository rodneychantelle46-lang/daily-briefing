import json
import os
from pathlib import Path
from src.utils.llm_client import chat_completion
from src.utils.logger import get_logger
from src.utils.web_searcher import search_zhihu, search_bilibili

logger = get_logger("llm_generator")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
HISTORY_PATH = PROJECT_ROOT / "data" / "generated_topics.json"
GENERATION_STATUS_OK = "ok"
GENERATION_STATUS_DEGRADED = "degraded"

TOPIC_CONFIGS = {
    "cs_ai_learning": {
        "name": "计网 × AI 知识学习",
        "prompt": """生成一张适合纯文科生的“计算机网络 + AI”知识学习卡片，保持午报风格。这个栏目一共两个知识点：1 个计算机网络知识点 + 1 个 AI 基础知识点。

整体知识规划按这张地图推进，避免每天东一榔头西一棒：
1. 网络基础：IP、端口、DNS、HTTP/HTTPS、TCP/UDP、Cookie/Session
2. Web 与 API：请求/响应、JSON、鉴权、Webhook、浏览器与服务器
3. 数据与系统：数据库、缓存、队列、日志、云服务、部署
4. AI 基础：Token、Embedding、向量数据库、RAG、Function Calling、Agent
5. AI 工程：提示词、工具调用、评估、权限、安全、成本

要求：
- points 必须刚好 2 条
- 第 1 条必须来自网络基础 / Web 与 API / 数据与系统，偏计算机网络和互联网工作原理
- 第 2 条必须来自 AI 基础 / AI 工程，偏 AI 底层概念和 Agent 实践
- 每条只讲一个小概念，每条 content 80-130 字
- 必须深入浅出：先用生活类比解释，再给准确说法
- 要说明它和 AI Agent / OpenClaw / 日常上网有什么关系
- 不要幼稚化，不要百科腔，不要堆术语
- 每条都给一个今天可以动手的小观察/小实验

避免重复这些已生成过的主题：{history}

请严格返回 JSON 格式：
{{"title": "栏目总标题，10字以内", "topic": "两个主题关键词，用 + 连接", "points": [{{"pillar": "网络基础/Web 与 API/数据与系统", "title": "10字以内标题", "content": "知识点正文", "try_this": "今天可以试的一步", "topic": "主题关键词"}}, {{"pillar": "AI 基础/AI 工程", "title": "10字以内标题", "content": "知识点正文", "try_this": "今天可以试的一步", "topic": "主题关键词"}}]}}""",
    },
    "psychology": {
        "name": "心理学/经济学技巧",
        "prompt": """生成一条实用的心理学或经济学知识卡片，保持午报风格，120-180 字。
要求：
- 只讲一个具体效应/原理
- 先解释“它是什么”，再说“今天怎么用”
- 必须带一个工作/学习/消费决策里的例子
- 不要鸡汤，不要百科口吻

避免重复这些已生成过的主题：{history}

请严格返回 JSON 格式：
{{"title": "10字以内标题", "content": "知识卡片正文", "try_this": "今天可以试的一步", "topic": "主题关键词"}}""",
    },
    "brand_insight": {
        "name": "品牌洞察",
        "prompt": """生成一条品牌或商业洞察，保持午报风格，120-180 字。
要求：
- 选择一个真实品牌/产品/商业现象
- 讲清楚它用了什么策略，以及为什么有效
- 提炼一条可迁移的方法论
- 不要写成品牌公关稿

避免重复这些已生成过的主题：{history}

请严格返回 JSON 格式：
{{"title": "10字以内标题", "content": "洞察正文", "try_this": "可迁移的一步", "topic": "主题关键词"}}""",
    },
}


def generate_tip(
    topic_type: str,
    model: str = "gpt-5.5",
    api_key: str = None,
    base_url: str = None,
) -> dict:
    key = os.getenv("CODEX_API_KEY", "") or api_key or os.getenv("OPENAI_API_KEY", "")
    model = os.getenv("CODEX_MODEL", "") or model or os.getenv("OPENAI_MODEL", "gpt-5.5")
    config = TOPIC_CONFIGS.get(topic_type)
    if not config:
        logger.warning(f"未知的主题类型: {topic_type}")
        return _fallback_tip(topic_type or "未知栏目", "unknown_topic_type")

    if not key:
        logger.warning("CODEX_API_KEY/OPENAI_API_KEY 未设置，无法生成内容")
        return _fallback_tip(config["name"], "missing_api_key")

    history = _load_history(topic_type)
    history_str = ", ".join(history[-30:]) if history else "无"
    prompt = config["prompt"].format(history=history_str)

    try:
        content = chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            api_key=key,
            base_url=base_url,
            temperature=0.65,
            max_tokens=700,
        )
        content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        result = _normalize_tip_result(json.loads(content), config["name"])
        if _is_low_quality_tip(result):
            logger.warning(f"午报内容质量偏低 ({topic_type})，使用降级文案")
            return _fallback_tip(config["name"], "low_quality_tip")

        topic_kw = result.get("topic", "")
        logger.info(f"午报内容生成成功 ({topic_type}): {topic_kw}")
        _save_history(topic_type, topic_kw)

        result["links"] = _collect_related_links(result, topic_type)
        result["generation_status"] = GENERATION_STATUS_OK
        result["generation_error"] = ""
        return result
    except json.JSONDecodeError as e:
        logger.warning(f"GPT 返回 JSON 解析失败 ({e})")
        return _fallback_tip(config["name"], f"json_decode_error: {e}")
    except Exception as e:
        logger.warning(f"GPT 调用失败 ({e})")
        return _fallback_tip(config["name"], f"llm_call_failed: {type(e).__name__}: {e}")


def summarize_github_repos(
    repos: list[dict],
    model: str = "gpt-5.5",
    api_key: str = None,
    base_url: str = None,
) -> list[dict]:
    if not repos:
        return []

    # Deep-read metadata is part of the quality gate. Missing/degraded metadata is
    # treated as degraded instead of silently falling back to trending descriptions.
    if any(r.get("generation_status") == GENERATION_STATUS_DEGRADED for r in repos):
        return _mark_github_degraded(repos, "github_trending_degraded")
    if any(r.get("deep_read_status") != "ok" for r in repos):
        return _mark_github_degraded(repos, "github_deep_read_missing_or_degraded")

    key = os.getenv("CODEX_API_KEY", "") or api_key or os.getenv("OPENAI_API_KEY", "")
    model = os.getenv("CODEX_MODEL", "") or model or os.getenv("OPENAI_MODEL", "gpt-5.5")
    if not key:
        logger.warning("CODEX_API_KEY/OPENAI_API_KEY 未设置，无法生成 GitHub 摘要")
        return _mark_github_degraded(repos, "missing_api_key")

    repo_list = "\n".join(_format_repo_for_prompt(i, r) for i, r in enumerate(repos, start=1))

    prompt = f"""你是一位有品味但保守负责的技术博主，擅长用简洁有趣的语言介绍开源项目。请基于“Trending 信息 + GitHub API 元数据 + README 摘要”评估以下项目，各写一段中文摘要（40-80字）。

硬性要求：
- 必须使用 README 摘要、license、topics、最近更新时间、open issues 等字段作为判断依据
- 先用一句话说清楚项目是什么、解决什么问题
- 再加一句点评：为什么值得关注（技术亮点 / 应用场景 / 行业趋势）
- use_case 要具体，说它适合怎么用或能借鉴什么
- risk 要保守指出一个采用风险或不确定性；不要把仅凭 trending 描述的内容包装成深读结论
- 如果字段不足，不要编造；直接在 risk 里说信息不足
- 语气自然，像在跟朋友推荐，不要太官方

项目列表：
{repo_list}

请严格返回 JSON 数组，格式如下，不要返回任何其他内容：
[{{"index": 1, "summary": "摘要正文", "why": "为什么值得看", "use_case": "适合怎么用/借鉴", "risk": "风险或不确定性"}}]

返回 {len(repos)} 条，不多不少。"""

    try:
        content = chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            api_key=key,
            base_url=base_url,
            temperature=0.3,
            max_tokens=1400,
        )
        content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        summaries = json.loads(content)
        if not isinstance(summaries, list) or len(summaries) != len(repos):
            logger.warning("GitHub 摘要返回数量不匹配，按降级处理")
            return _mark_github_degraded(repos, "github_summary_count_mismatch")

        if any(_is_low_quality_repo_summary(item) for item in summaries):
            logger.warning("GitHub 摘要质量偏低，按降级处理")
            return _mark_github_degraded(repos, "github_summary_low_quality")

        for item in summaries:
            idx = item.get("index", 0) - 1
            if 0 <= idx < len(repos):
                repos[idx]["summary"] = item.get("summary", "").strip()
                repos[idx]["why"] = item.get("why", "").strip()
                repos[idx]["use_case"] = item.get("use_case", "").strip()
                repos[idx]["risk"] = item.get("risk", "").strip()
                repos[idx]["generation_status"] = GENERATION_STATUS_OK
                repos[idx]["generation_error"] = ""
        logger.info(f"GitHub 项目摘要生成成功: {len(summaries)} 条")
        return repos
    except Exception as e:
        logger.warning(f"GitHub 摘要生成失败 ({e})，按降级处理")
        return _mark_github_degraded(repos, f"github_summary_failed: {type(e).__name__}: {e}")


def _normalize_tip_result(result: dict, section_name: str) -> dict:
    title = str(result.get("title", "")).strip()[:16]
    topic = str(result.get("topic", title or section_name)).strip()
    points = _normalize_learning_points(result.get("points", []))
    content = str(result.get("content", "")).strip()
    try_this = str(result.get("try_this", "")).strip()

    if points:
        content = "\n".join(point["content"] for point in points)
        if not topic:
            topic = " + ".join(point.get("topic", "") for point in points if point.get("topic"))

    return {
        "title": title or section_name,
        "content": content,
        "try_this": try_this,
        "topic": topic,
        "pillar": str(result.get("pillar", "")).strip(),
        "points": points,
    }


def _normalize_learning_points(points: list[dict]) -> list[dict]:
    if not isinstance(points, list):
        return []
    normalized = []
    for point in points[:2]:
        if not isinstance(point, dict):
            continue
        normalized.append({
            "pillar": str(point.get("pillar", "")).strip(),
            "title": str(point.get("title", "")).strip()[:16],
            "content": str(point.get("content", "")).strip(),
            "try_this": str(point.get("try_this", "")).strip(),
            "topic": str(point.get("topic", "")).strip(),
        })
    return normalized


def _is_low_quality_tip(result: dict) -> bool:
    banned = ["提升效率", "非常重要", "值得关注", "在当今", "随着技术发展", "可以帮助你"]
    points = result.get("points", [])
    if points:
        if len(points) != 2:
            return True
        for point in points:
            content = point.get("content", "")
            if len(content) < 40 or len(content) > 220:
                return True
            if sum(1 for word in banned if word in content) >= 2:
                return True
            if not point.get("try_this"):
                return True
        return False

    content = result.get("content", "")
    if len(content) < 60:
        return True
    if len(content) > 360:
        return True
    if sum(1 for word in banned if word in content) >= 2:
        return True
    if not result.get("try_this"):
        return True
    return False


def _fallback_tip(section_name: str, error: str) -> dict:
    return {
        "title": section_name,
        "content": f"今日{section_name}生成失败，先跳过这块，不硬凑废话。",
        "try_this": "等下一次自动生成；如果连续失败，再查模型或搜索链路。",
        "topic": "生成失败",
        "pillar": "",
        "points": [],
        "links": [],
        "generation_status": GENERATION_STATUS_DEGRADED,
        "generation_error": error,
    }


def _collect_related_links(result: dict, topic_type: str) -> list[dict]:
    links: list[dict] = []
    seen_urls: set[str] = set()
    for keyword, domain in _related_link_keywords(result, topic_type):
        for platform, search_func in (("知乎", search_zhihu), ("B站", search_bilibili)):
            for item in search_func(keyword, count=1, domain=domain):
                url = item.get("url", "")
                if not url or url in seen_urls:
                    continue
                links.append({"platform": platform, **item})
                seen_urls.add(url)
        if len(links) >= 4:
            break
    return links


def _related_link_keywords(result: dict, topic_type: str) -> list[tuple[str, str]]:
    if topic_type == "cs_ai_learning" and result.get("points"):
        keywords = []
        for point in result.get("points", []):
            keyword = point.get("topic") or point.get("title")
            domain = _domain_for_learning_point(point)
            if keyword:
                keywords.append((keyword, domain))
        return keywords

    keyword = result.get("topic") or result.get("title")
    domain = "brand" if topic_type == "brand_insight" else topic_type
    return [(keyword, domain)] if keyword else []


def _domain_for_learning_point(point: dict) -> str:
    text = f"{point.get('pillar', '')} {point.get('topic', '')} {point.get('title', '')}"
    if any(word in text for word in ["网络", "Web", "API", "数据与系统", "IP", "DNS", "HTTP", "TCP", "UDP"]):
        return "network"
    return "ai"


def _format_repo_for_prompt(index: int, repo: dict) -> str:
    topics = ", ".join(repo.get("topics") or []) or "无"
    readme = (repo.get("readme_excerpt") or "无 README 摘要").replace("\n", " ")[:900]
    return (
        f"{index}. {repo.get('name', '')} ({repo.get('language', '')})\n"
        f"   URL: {repo.get('url', '')}\n"
        f"   Trending 描述: {repo.get('description', '无描述')}\n"
        f"   今日新增星标: {repo.get('stars_today', '?')}；总星标: {repo.get('stars', '?')}；forks: {repo.get('forks', '?')}\n"
        f"   license: {repo.get('license') or '未知'}；topics: {topics}\n"
        f"   最近更新: {repo.get('updated_at') or '未知'}；open issues: {repo.get('open_issues_count', '未知')}\n"
        f"   README 摘要: {readme}"
    )


def _is_low_quality_repo_summary(item: dict) -> bool:
    if not isinstance(item, dict):
        return True
    summary = str(item.get("summary", "")).strip()
    why = str(item.get("why", "")).strip()
    use_case = str(item.get("use_case", "")).strip()
    risk = str(item.get("risk", "")).strip()
    if len(summary) < 20 or len(summary) > 180:
        return True
    if len(why) < 8 or len(use_case) < 8 or len(risk) < 4:
        return True
    return False


def _mark_github_degraded(repos: list[dict], error: str) -> list[dict]:
    for repo in repos:
        specific_error = repo.get("generation_error") or repo.get("deep_read_error") or error
        repo["generation_status"] = GENERATION_STATUS_DEGRADED
        repo["generation_error"] = specific_error
        repo.setdefault("summary", repo.get("description") or "GitHub 项目摘要生成失败，已按降级处理。")
        repo.setdefault("why", "摘要生成或深读链路不可用，本期不应作为正式判断发送。")
        repo.setdefault("use_case", "等待下一次自动生成，或先修复 GitHub/API/LLM 链路。")
    return repos


def _load_history(topic_type: str) -> list[str]:
    try:
        if HISTORY_PATH.exists():
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get(topic_type, [])
    except Exception:
        pass
    return []


def _save_history(topic_type: str, topic: str):
    if not topic:
        return
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if HISTORY_PATH.exists():
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        topics = data.get(topic_type, [])
        topics.append(topic)
        topics = topics[-30:]  # 只保留最近 30 条
        data[topic_type] = topics
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存主题历史失败: {e}")
