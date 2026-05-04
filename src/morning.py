import json
import os
import sys
import yaml
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.fetchers.rss_fetcher import fetch_rss
from src.fetchers.zhihu_fetcher import fetch_zhihu_hot
from src.fetchers.weather_fetcher import fetch_weather
from src.fetchers.quote_fetcher import fetch_quote
from src.fetchers.podcast_fetcher import fetch_podcast
from src.fetchers.bilibili_fetcher import fetch_bilibili_videos, fetch_popular_videos
from src.processors.llm_selector import select_articles
from src.publishers.feishu import build_morning_card, send_feishu_card
from src.utils.logger import get_logger
from src.utils.dedup import load_seen, save_seen, filter_unseen, mark_seen, cleanup_old

logger = get_logger("morning")


def _parse_iso_datetime(value: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def filter_recent_articles(articles: list[dict], max_age_days: int = 14) -> list[dict]:
    """Drop obviously stale RSS entries before the LLM sees them.

    Some feeds (notably product blogs) expose hundreds of old posts. Keeping all of
    them makes the model pick stale noise and burns tokens. Entries without a
    usable timestamp are kept, because several Chinese feeds omit dates.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    recent = []
    stale = 0
    for article in articles:
        dt = _parse_iso_datetime(article.get("published_at", ""))
        if dt is None or dt >= cutoff:
            recent.append(article)
        else:
            stale += 1
    if stale:
        logger.info(f"新鲜度过滤: 丢弃 {stale} 篇超过 {max_age_days} 天的旧内容")
    return recent


def write_card_artifact(card: dict, date_str: str) -> Path:
    artifacts_dir = PROJECT_ROOT / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    filename = f"morning-card-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path = artifacts_dir / filename
    payload = {
        "date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "card": card,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info(f"晨报卡片已保存到 artifact: {path}")
    return path


def load_config() -> dict:
    config_path = PROJECT_ROOT / "config" / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    # 替换环境变量占位符
    _resolve_env(config)
    return config


def load_rss_sources() -> dict:
    sources_path = PROJECT_ROOT / "config" / "rss_sources.yaml"
    with open(sources_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _resolve_env(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                env_var = v[2:-1]
                obj[k] = os.getenv(env_var, "")
            else:
                _resolve_env(v)
    elif isinstance(obj, list):
        for item in obj:
            _resolve_env(item)


def main():
    load_dotenv(PROJECT_ROOT / ".env")

    logger.info("========== 早报开始 ==========")
    config = load_config()
    rss_sources = load_rss_sources()

    # 1. 抓取全行业 RSS
    logger.info("--- 步骤 1: 抓取 RSS ---")
    general_sources = rss_sources.get("general", [])
    general_articles = fetch_rss(general_sources)

    # 2. 抓取知乎热榜
    logger.info("--- 步骤 2: 抓取知乎热榜 ---")
    zhihu_articles = fetch_zhihu_hot()
    all_articles = filter_recent_articles(general_articles + zhihu_articles, max_age_days=14)

    # 3. 抓取兴趣领域 RSS
    logger.info("--- 步骤 3: 抓取兴趣领域 RSS ---")
    interest_sources = {}
    for key in rss_sources:
        if key not in ("general", "podcast"):
            interest_sources[key] = rss_sources[key]
    interest_articles = {}
    for key, sources in interest_sources.items():
        interest_articles[key] = filter_recent_articles(fetch_rss(sources), max_age_days=30)

    # 4. 去重过滤
    logger.info("--- 步骤 4: 去重过滤 ---")
    seen_data = load_seen()
    all_articles = filter_unseen(all_articles, seen_data)
    for key in interest_articles:
        interest_articles[key] = filter_unseen(interest_articles[key], seen_data)

    # 5. GPT 选稿
    logger.info("--- 步骤 5: GPT 选稿 ---")
    llm_config = config.get("llm", {})
    model = llm_config.get("model", "gpt-5.5")
    api_key = llm_config.get("api_key", "")
    base_url = llm_config.get("base_url", "")

    general_top5 = select_articles(
        all_articles, category="全行业", count=5, model=model, api_key=api_key, base_url=base_url
    )

    # 排除已被全行业选中的 URL
    general_selected_urls = {a["url"] for a in general_top5}

    interests = config.get("interests", [])
    interest_top5 = {}
    for interest in interests:
        name = interest["name"]
        keywords = interest.get("keywords", [])
        # 合并：兴趣领域专属源 + 全部文章中关键词匹配的
        pool = []
        # 从兴趣领域专属源获取
        for key, arts in interest_articles.items():
            pool.extend(arts)
        # 从全部文章中按关键词粗筛
        for a in all_articles:
            title = a.get("title", "")
            if any(kw in title for kw in keywords):
                pool.append(a)
        # 去重 + 排除全行业已选
        seen_urls = set(general_selected_urls)
        unique_pool = []
        for a in pool:
            if a["url"] not in seen_urls:
                seen_urls.add(a["url"])
                unique_pool.append(a)

        # 如果粗筛后候选不足，把全部文章给 GPT（排除已选的）
        if len(unique_pool) < 10:
            for a in all_articles:
                if a["url"] not in seen_urls:
                    seen_urls.add(a["url"])
                    unique_pool.append(a)

        interest_top5[name] = select_articles(
            unique_pool, category=name, keywords=keywords, count=5,
            model=model, api_key=api_key, base_url=base_url
        )

    # 5b. B站热门视频
    bilibili_videos = []
    bili_config = config.get("bilibili", {})
    if bili_config:
        logger.info("--- 步骤 5b: B站热门视频 ---")
        bili_count = bili_config.get("count", 5)
        bili_mode = bili_config.get("mode", "popular")
        if bili_mode == "popular":
            bili_pool = fetch_popular_videos(count=bili_count * 4)
        else:
            bili_keywords = bili_config.get("search_keywords", [])
            bili_pool = fetch_bilibili_videos(bili_keywords, count=bili_count * 3)
        bili_pool = filter_unseen(bili_pool, seen_data)
        bilibili_videos = bili_pool[:bili_count]

    # 6. 先暂存待记录条目；飞书发送成功后再真正落去重，避免“没发出去却已标记已读”。
    logger.info("--- 步骤 6: 准备去重状态 ---")
    all_selected = list(general_top5)
    for news_list in interest_top5.values():
        all_selected.extend(news_list)
    all_selected.extend(bilibili_videos)

    # 7. 天气
    logger.info("--- 步骤 7: 获取天气 ---")
    city = config.get("user", {}).get("city", "鼓楼")
    weather_cfg = config.get("weather", {})
    weather = fetch_weather(
        city,
        api_key=weather_cfg.get("api_key", ""),
        api_host=weather_cfg.get("api_host", ""),
    )

    # 8. 每日一句
    logger.info("--- 步骤 8: 每日一句 ---")
    quote = fetch_quote()

    # 9. 播客推荐
    logger.info("--- 步骤 9: 播客推荐 ---")
    podcast_sources = rss_sources.get("podcast", [])
    podcast = fetch_podcast(podcast_sources)

    # 10. 组装飞书卡片
    logger.info("--- 步骤 10: 组装飞书卡片 ---")
    date_str = datetime.now().strftime("%Y年%m月%d日")
    card = build_morning_card(
        general_news=general_top5,
        interest_news=interest_top5,
        bilibili_videos=bilibili_videos,
        bili_section_name=bili_config.get("name", "求职就业") if bili_config else "",
        weather=weather,
        quote=quote,
        podcast=podcast,
        date_str=date_str,
    )

    write_card_artifact(card, date_str)

    # 11. 推送
    logger.info("--- 步骤 11: 飞书推送 ---")
    feishu_config = config.get("publisher", {}).get("feishu", {})
    sent = send_feishu_card(card, feishu_config)
    if not sent:
        logger.error("飞书推送失败，晨报任务按失败退出，防止 GitHub Actions 假成功")
        sys.exit(1)

    # 12. 记录已推送文章
    logger.info("--- 步骤 12: 记录已推送 ---")
    mark_seen(all_selected, seen_data)
    seen_data = cleanup_old(seen_data, config.get("dedup", {}).get("retention_days", 7))
    save_seen(seen_data)

    logger.info("========== 早报完成 ==========")


if __name__ == "__main__":
    main()
