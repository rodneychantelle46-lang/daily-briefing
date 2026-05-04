import json
import os
from pathlib import Path
from src.utils.llm_client import chat_completion
from src.utils.logger import get_logger
from src.utils.web_searcher import search_zhihu, search_bilibili

logger = get_logger("llm_generator")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
HISTORY_PATH = PROJECT_ROOT / "data" / "generated_topics.json"

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
    key = api_key or os.getenv("OPENAI_API_KEY", "")
    config = TOPIC_CONFIGS.get(topic_type)
    if not config:
        logger.warning(f"未知的主题类型: {topic_type}")
        return {"content": "", "link": "", "topic": ""}

    if not key:
        logger.warning("OPENAI_API_KEY 未设置，无法生成内容")
        return _fallback_tip(config["name"])

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
            return _fallback_tip(config["name"])

        topic_kw = result.get("topic", "")
        logger.info(f"午报内容生成成功 ({topic_type}): {topic_kw}")
        _save_history(topic_type, topic_kw)

        # 搜索真实的知乎和 B 站内容作为延伸阅读。
        links = []
        if topic_kw:
            zhihu = search_zhihu(topic_kw, count=1)
            if zhihu:
                links.append({"platform": "知乎", **zhihu[0]})
            bili = search_bilibili(topic_kw, count=1)
            if bili:
                links.append({"platform": "B站", **bili[0]})
        result["links"] = links
        return result
    except json.JSONDecodeError as e:
        logger.warning(f"GPT 返回 JSON 解析失败 ({e})")
        return _fallback_tip(config["name"])
    except Exception as e:
        logger.warning(f"GPT 调用失败 ({e})")
        return _fallback_tip(config["name"])


def summarize_github_repos(
    repos: list[dict],
    model: str = "gpt-5.5",
    api_key: str = None,
    base_url: str = None,
) -> list[dict]:
    if not repos:
        return []

    key = api_key or os.getenv("OPENAI_API_KEY", "")
    if not key:
        logger.warning("OPENAI_API_KEY 未设置，无法生成 GitHub 摘要")
        return repos

    repo_list = "\n".join(
        f"{i+1}. {r['name']} ({r.get('language','')}) — {r.get('description','无描述')} | 今日 {r.get('stars_today','?')}"
        for i, r in enumerate(repos)
    )

    prompt = f"""你是一位有品味的技术博主，擅长用简洁有趣的语言介绍开源项目。请为以下 GitHub 热门项目各写一段中文摘要（40-80字）。

要求：
- 先用一句话说清楚项目是什么、解决什么问题
- 再加一句点评：为什么值得关注（技术亮点 / 应用场景 / 行业趋势）
- use_case 要具体，说它适合怎么用或能借鉴什么
- 语气自然，像在跟朋友推荐，不要太官方

项目列表：
{repo_list}

请严格返回 JSON 数组，格式如下，不要返回任何其他内容：
[{{"index": 1, "summary": "摘要正文", "why": "为什么值得看", "use_case": "适合怎么用/借鉴"}}]

返回 {len(repos)} 条，不多不少。"""

    try:
        content = chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            api_key=key,
            base_url=base_url,
            temperature=0.3,
            max_tokens=1200,
        )
        content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        summaries = json.loads(content)
        if isinstance(summaries, list):
            for item in summaries:
                idx = item.get("index", 0) - 1
                if 0 <= idx < len(repos):
                    repos[idx]["summary"] = item.get("summary", "")
                    repos[idx]["why"] = item.get("why", "")
                    repos[idx]["use_case"] = item.get("use_case", "")
            logger.info(f"GitHub 项目摘要生成成功: {len(summaries)} 条")
        return repos
    except Exception as e:
        logger.warning(f"GitHub 摘要生成失败 ({e})，使用原始描述")
        for r in repos:
            r.setdefault("summary", r.get("description", ""))
        return repos


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


def _fallback_tip(section_name: str) -> dict:
    return {
        "title": section_name,
        "content": f"今日{section_name}生成失败，先跳过这块，不硬凑废话。",
        "try_this": "等下一次自动生成；如果连续失败，再查模型或搜索链路。",
        "topic": "生成失败",
        "pillar": "",
        "points": [],
        "links": [],
    }


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
