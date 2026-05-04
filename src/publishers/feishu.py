import json
import time
import requests
from src.utils.logger import get_logger

logger = get_logger("feishu")

MAX_RETRIES = 2
RETRY_DELAY = 5

# 飞书 API
TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
MESSAGE_URL = "https://open.feishu.cn/open-apis/im/v1/messages"


def _get_tenant_access_token(app_id: str, app_secret: str) -> str:
    resp = requests.post(
        TOKEN_URL,
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"获取 token 失败: {data.get('msg', '')}")
    token = data.get("tenant_access_token", "")
    logger.info("飞书 tenant_access_token 获取成功")
    return token


def send_feishu_card(card: dict, feishu_config: dict) -> bool:
    mode = feishu_config.get("mode", "app")
    if mode == "webhook":
        return _send_via_webhook(feishu_config.get("webhook_url", ""), card)
    else:
        return _send_via_app(card, feishu_config)


def _send_via_app(card: dict, feishu_config: dict) -> bool:
    app_id = feishu_config.get("app_id", "")
    app_secret = feishu_config.get("app_secret", "")
    receive_id = feishu_config.get("receive_id", "")
    receive_id_type = feishu_config.get("receive_id_type", "open_id")

    if not all([app_id, app_secret, receive_id]):
        logger.error("飞书应用配置不完整（需要 app_id, app_secret, receive_id）")
        return False

    for attempt in range(MAX_RETRIES + 1):
        try:
            token = _get_tenant_access_token(app_id, app_secret)
            payload = {
                "receive_id": receive_id,
                "msg_type": "interactive",
                "content": json.dumps(card),
            }
            resp = requests.post(
                MESSAGE_URL,
                params={"receive_id_type": receive_id_type},
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                timeout=10,
            )
            resp.raise_for_status()
            result = resp.json()
            if result.get("code") == 0:
                logger.info("飞书应用推送成功")
                return True
            else:
                logger.warning(f"飞书返回异常: code={result.get('code')}, msg={result.get('msg', '')}")
        except Exception as e:
            logger.warning(f"飞书推送失败 (第 {attempt+1} 次): {e}")
        if attempt < MAX_RETRIES:
            logger.info(f"等待 {RETRY_DELAY}s 后重试...")
            time.sleep(RETRY_DELAY)
    logger.error("飞书推送最终失败，已达最大重试次数")
    return False


def _send_via_webhook(webhook_url: str, card: dict) -> bool:
    if not webhook_url:
        logger.error("飞书 Webhook URL 未配置")
        return False
    payload = {
        "msg_type": "interactive",
        "card": card,
    }
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            resp.raise_for_status()
            result = resp.json()
            if result.get("code") == 0 or result.get("StatusCode") == 0:
                logger.info("飞书 Webhook 推送成功")
                return True
            else:
                logger.warning(f"飞书返回异常: {result}")
        except Exception as e:
            logger.warning(f"飞书推送失败 (第 {attempt+1} 次): {e}")
        if attempt < MAX_RETRIES:
            logger.info(f"等待 {RETRY_DELAY}s 后重试...")
            time.sleep(RETRY_DELAY)
    logger.error("飞书推送最终失败，已达最大重试次数")
    return False


def _format_article_item(index: int, article: dict) -> str:
    title = article.get("title", "")
    url = article.get("url", "")
    source = article.get("source", "")
    rank = article.get("rank")
    reason = article.get("reason", "")
    takeaway = article.get("takeaway", "")
    related_sources = article.get("related_sources") or []
    multi_source_label = article.get("multi_source_label", "")

    rank_text = f" · #{rank}" if rank else ""
    if multi_source_label:
        source_text = f"（{multi_source_label}{rank_text}）"
    else:
        source_text = f"（{source}{rank_text}）" if source else ""
    line = f"{index}. [{title}]({url}){source_text}\n" if url else f"{index}. {title}{source_text}\n"
    if len(related_sources) > 1:
        line += f"   - 同热：{' / '.join(related_sources[:5])}\n"
    if reason:
        line += f"   - 看点：{reason}\n"
    if takeaway:
        line += f"   - 判断：{takeaway}\n"
    elif source:
        line += f"   - 来源：{source}\n"
    return line


def build_morning_card(
    general_news: list[dict],
    interest_news: dict[str, list[dict]],
    bilibili_videos: list[dict] = None,
    bili_section_name: str = "",
    weather: dict = None,
    quote: dict = None,
    podcast: dict = None,
    date_str: str = "",
) -> dict:
    elements = []

    # 头部：早报 + 日期
    header_text = f"☀️ 早报 · {date_str}"

    # 天气信息独立一行
    if weather:
        city = weather.get("city", "")
        temp_min = weather.get("temp_min", "--")
        temp_max = weather.get("temp_max", "--")
        condition_day = weather.get("condition_day", "") or weather.get("condition", "")
        wind_dir = weather.get("wind_dir", "")
        wind_scale = weather.get("wind_scale", "")
        wind_text = f" | {wind_dir}{wind_scale}级" if wind_dir else ""
        weather_md = f"📍 {city} · {condition_day} {temp_min}~{temp_max}°C{wind_text}"
        elements.append({"tag": "markdown", "content": weather_md})
        elements.append({"tag": "hr"})

    # 全行业资讯
    general_md = "**━━ 全行业资讯 ━━**\n"
    for i, a in enumerate(general_news, 1):
        general_md += _format_article_item(i, a)
    elements.append({"tag": "markdown", "content": general_md})
    elements.append({"tag": "hr"})

    # 兴趣领域
    for interest_name, news_list in interest_news.items():
        interest_md = f"**━━ {interest_name} · 兴趣领域 ━━**\n"
        for i, a in enumerate(news_list, 1):
            interest_md += _format_article_item(i, a)
        elements.append({"tag": "markdown", "content": interest_md})
        elements.append({"tag": "hr"})

    # B站视频推荐
    if bilibili_videos:
        section_name = bili_section_name or "求职就业"
        bili_md = f"**━━ 🎬 {section_name} · B站视频 ━━**\n"
        for i, v in enumerate(bilibili_videos, 1):
            bili_md += f"{i}. [{v['title']}]({v['url']})\n"
        elements.append({"tag": "markdown", "content": bili_md})
        elements.append({"tag": "hr"})

    # 播客推荐
    if podcast:
        podcast_md = "**━━ 播客推荐 ━━**\n"
        podcast_md += f"🎙️ {podcast.get('name', '')} · 《{podcast.get('episode_title', '')}》\n"
        podcast_md += f"[收听链接]({podcast.get('url', '')})"
        elements.append({"tag": "markdown", "content": podcast_md})
        elements.append({"tag": "hr"})

    # 每日一句
    if quote:
        quote_md = f"**━━ 每日一句 ━━**\n"
        quote_md += f"💬 \"{quote.get('text', '')}\" —— {quote.get('author', '')}"
        elements.append({"tag": "markdown", "content": quote_md})

    card = {
        "header": {
            "title": {"tag": "plain_text", "content": header_text},
            "template": "blue",
        },
        "elements": elements,
    }
    return card


def build_afternoon_card(
    tips: list[dict],
    date_str: str = "",
    github_repos: list[dict] = None,
) -> dict:
    elements = []

    section_icons = ["🤖", "🧠", "💡"]
    section_names = ["AI 技巧", "心理学/经济学", "品牌洞察"]

    for i, tip in enumerate(tips):
        name = section_names[i] if i < len(section_names) else "知识卡片"
        icon = section_icons[i] if i < len(section_icons) else "📌"
        title = tip.get("title", "")
        try_this = tip.get("try_this", "")
        tip_md = f"**━━ {icon} {name} ━━**\n"
        if title and title != name:
            tip_md += f"**{title}**\n"
        tip_md += tip.get("content", "")
        if try_this:
            tip_md += f"\n\n**试一下**：{try_this}"
        links = tip.get("links", [])
        if links:
            tip_md += "\n\n📖 延伸阅读："
            for lk in links:
                platform = lk.get("platform", "")
                title = lk.get("title", "链接")[:30]
                url = lk.get("url", "")
                if url:
                    tip_md += f"\n- [{platform} · {title}]({url})"
        elements.append({"tag": "markdown", "content": tip_md})
        elements.append({"tag": "hr"})

    # GitHub 热门项目
    if github_repos:
        gh_md = "**━━ 🔥 GitHub 热门项目 ━━**\n"
        for r in github_repos:
            name = r.get("name", "")
            url = r.get("url", "")
            summary = r.get("summary", r.get("description", ""))
            why = r.get("why", "")
            use_case = r.get("use_case", "")
            lang = r.get("language", "")
            stars = r.get("stars_today", "")
            score = r.get("quality_score")
            lang_tag = f"`{lang}` " if lang else ""
            stars_tag = f" ⭐ {stars}" if stars else ""
            score_tag = f" · 质量 {score}" if score is not None else ""
            gh_md += f"\n**[{name}]({url})**{stars_tag}{score_tag}\n"
            gh_md += f"{lang_tag}{summary}\n"
            if why:
                gh_md += f"- 看点：{why}\n"
            if use_case:
                gh_md += f"- 可用：{use_case}\n"
        elements.append({"tag": "markdown", "content": gh_md})

    card = {
        "header": {
            "title": {"tag": "plain_text", "content": f"📬 午报 · {date_str}"},
            "template": "purple",
        },
        "elements": elements,
    }
    return card
