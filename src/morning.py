import json
import os
import sys
import yaml
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.fetchers.rss_fetcher import fetch_rss
from src.fetchers.zhihu_fetcher import fetch_zhihu_hot
from src.fetchers.newsnow_fetcher import fetch_hotlists
from src.fetchers.weather_fetcher import fetch_weather
from src.fetchers.quote_fetcher import fetch_quote
from src.fetchers.podcast_fetcher import fetch_podcast
from src.fetchers.bilibili_fetcher import fetch_bilibili_videos, fetch_popular_videos
from src.processors.llm_selector import select_articles
from src.publishers.feishu import build_morning_card, send_feishu_card
from src.utils.logger import get_logger
from src.utils.dedup import load_seen, save_seen, filter_unseen, mark_seen, cleanup_old
from src.utils.send_guard import already_sent, mark_sent
from src.utils.source_quality import load_source_quality, update_source_quality, summarize_source_counts
from src.utils.link_validator import validate_article_links

logger = get_logger("morning")
APP_TZ = ZoneInfo("Asia/Shanghai")


def local_now() -> datetime:
    return datetime.now(APP_TZ)


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


def _summarize_link_reports(link_reports: list[dict] | None) -> dict:
    reports = link_reports or []
    return {
        "total": len(reports),
        "kept": sum(1 for r in reports if r.get("status") == "keep"),
        "rejected": sum(1 for r in reports if r.get("status") == "reject"),
        "fetch_errors_kept": sum(1 for r in reports if "抓取失败" in r.get("reason", "")),
    }


def _summarize_card(card: dict) -> dict:
    elements = card.get("elements", []) if isinstance(card, dict) else []
    return {
        "section_count": len(elements),
        "markdown_blocks": sum(1 for e in elements if e.get("tag") == "markdown"),
        "header": card.get("header", {}).get("title", {}).get("content", "") if isinstance(card, dict) else "",
    }


def _is_llm_degraded(article: dict) -> bool:
    return article.get("selection_status") == "degraded" or "模型不可用" in article.get("takeaway", "")


def _selection_error_summary(selected_articles: list[dict]) -> dict:
    summary: dict[str, int] = {}
    for article in selected_articles:
        if not _is_llm_degraded(article):
            continue
        reason = article.get("selection_error") or "unknown"
        summary[reason] = summary.get(reason, 0) + 1
    return summary


def write_audit_artifact(
    date_str: str,
    fetched_articles: list[dict],
    selected_articles: list[dict],
    source_quality: dict,
    link_reports: list[dict] = None,
    card: dict | None = None,
    llm_degraded: bool = False,
    aborted_reason: str = "",
) -> Path:
    artifacts_dir = PROJECT_ROOT / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    filename = f"morning-audit-{local_now().strftime('%Y%m%d-%H%M%S')}.json"
    path = artifacts_dir / filename
    payload = {
        "date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "quality_summary": {
            "fetched_total": len(fetched_articles),
            "selected_total": len(selected_articles),
            "source_count": len(summarize_source_counts(fetched_articles)),
            "llm_degraded": llm_degraded,
            "aborted_reason": aborted_reason,
            "selection_errors": _selection_error_summary(selected_articles),
            "link_validation": _summarize_link_reports(link_reports),
            "card": _summarize_card(card or {}),
        },
        "fetched_by_source": summarize_source_counts(fetched_articles),
        "selected_by_source": summarize_source_counts(selected_articles),
        "selected": [
            {
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "source": a.get("source", ""),
                "rank": a.get("rank"),
                "related_sources": a.get("related_sources", []),
                "reason": a.get("reason", ""),
                "takeaway": a.get("takeaway", ""),
                "selection_status": a.get("selection_status", ""),
                "selection_error": a.get("selection_error", ""),
            }
            for a in selected_articles
        ],
        "source_quality": source_quality,
        "link_validation": link_reports or [],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info(f"晨报候选审计已保存到 artifact: {path}")
    return path


def write_card_artifact(card: dict, date_str: str) -> Path:
    artifacts_dir = PROJECT_ROOT / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    filename = f"morning-card-{local_now().strftime('%Y%m%d-%H%M%S')}.json"
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
    send_date = local_now().strftime("%Y-%m-%d")
    if already_sent("morning", send_date):
        return

    rss_sources = load_rss_sources()
    source_quality = load_source_quality()

    # 1. 抓取全行业 RSS
    logger.info("--- 步骤 1: 抓取 RSS ---")
    general_sources = rss_sources.get("general", [])
    general_articles = fetch_rss(general_sources)

    # 2. 抓取热榜平台：借鉴 TrendRadar 的 NewsNow 聚合源，只取轻量抓取能力
    logger.info("--- 步骤 2: 抓取多平台热榜 ---")
    hotlist_articles = fetch_hotlists(config.get("hotlists", {}))

    # 3. 抓取知乎热榜（保留原生兜底；NewsNow 热榜中默认不重复配置知乎）
    logger.info("--- 步骤 3: 抓取知乎热榜 ---")
    zhihu_articles = fetch_zhihu_hot()

    # 热榜是高时效信号，放在候选池前面，避免被长 RSS 列表淹没。
    all_articles = filter_recent_articles(hotlist_articles + zhihu_articles + general_articles, max_age_days=14)

    # 4. 抓取兴趣领域 RSS
    logger.info("--- 步骤 4: 抓取兴趣领域 RSS ---")
    interest_sources = {}
    for key in rss_sources:
        if key not in ("general", "podcast"):
            interest_sources[key] = rss_sources[key]
    interest_articles = {}
    for key, sources in interest_sources.items():
        interest_articles[key] = filter_recent_articles(fetch_rss(sources), max_age_days=30)

    # 5. 去重过滤
    logger.info("--- 步骤 5: 去重过滤 ---")
    seen_data = load_seen()
    all_articles = filter_unseen(all_articles, seen_data)
    for key in interest_articles:
        interest_articles[key] = filter_unseen(interest_articles[key], seen_data)

    # 6. GPT 选稿
    logger.info("--- 步骤 6: GPT 选稿 ---")
    llm_config = config.get("llm", {})
    model = llm_config.get("model", "gpt-5.5")
    api_key = llm_config.get("api_key", "")
    base_url = llm_config.get("base_url", "")

    general_top5 = select_articles(
        all_articles, category="全行业", count=5, model=model, api_key=api_key, base_url=base_url,
        source_quality=source_quality,
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
            model=model, api_key=api_key, base_url=base_url,
            source_quality=source_quality,
        )

    # 5b. B站热门视频
    bilibili_videos = []
    bili_config = config.get("bilibili", {})
    if bili_config:
        logger.info("--- 步骤 6b: B站热门视频 ---")
        bili_count = bili_config.get("count", 5)
        bili_mode = bili_config.get("mode", "popular")
        if bili_mode == "popular":
            bili_pool = fetch_popular_videos(count=bili_count * 4)
        else:
            bili_keywords = bili_config.get("search_keywords", [])
            bili_pool = fetch_bilibili_videos(bili_keywords, count=bili_count * 3)
        bili_pool = filter_unseen(bili_pool, seen_data)
        bilibili_videos = bili_pool[:bili_count]

    # 6c. 链接与标题校验：剔除明确不一致的落地页，避免搜索页/错链混进正式卡片。
    logger.info("--- 步骤 7: 校验标题与链接 ---")
    link_reports = []
    general_top5, reports = validate_article_links(general_top5)
    link_reports.extend(reports)
    for interest_name in list(interest_top5.keys()):
        interest_top5[interest_name], reports = validate_article_links(interest_top5[interest_name])
        link_reports.extend(reports)

    # 7. 先暂存待记录条目；飞书发送成功后再真正落去重，避免“没发出去却已标记已读”。
    logger.info("--- 步骤 8: 准备去重状态 ---")
    all_selected = list(general_top5)
    for news_list in interest_top5.values():
        all_selected.extend(news_list)
    all_selected.extend(bilibili_videos)

    fetched_for_quality = list(all_articles)
    for news_list in interest_articles.values():
        fetched_for_quality.extend(news_list)
    fetched_for_quality.extend(bilibili_videos)
    source_quality = update_source_quality(fetched_for_quality, all_selected, source_quality)

    llm_degraded = any(_is_llm_degraded(a) for a in all_selected)
    allow_degraded_morning = bool(llm_config.get("allow_degraded_morning", False))
    date_str = local_now().strftime("%Y年%m月%d日")
    if llm_degraded and not allow_degraded_morning:
        aborted_reason = "选稿模型异常，已停止发送正式晨报，避免把降级候选伪装成编辑判断。"
        write_audit_artifact(
            date_str,
            fetched_for_quality,
            all_selected,
            source_quality,
            link_reports,
            llm_degraded=True,
            aborted_reason=aborted_reason,
        )
        logger.error(aborted_reason)
        sys.exit(2)

    # 8. 天气
    logger.info("--- 步骤 9: 获取天气 ---")
    city = config.get("user", {}).get("city", "鼓楼")
    weather_cfg = config.get("weather", {})
    weather = fetch_weather(
        city,
        api_key=weather_cfg.get("api_key", ""),
        api_host=weather_cfg.get("api_host", ""),
    )

    # 9. 每日一句
    logger.info("--- 步骤 10: 每日一句 ---")
    quote = fetch_quote()

    # 10. 播客推荐
    logger.info("--- 步骤 11: 播客推荐 ---")
    podcast_sources = rss_sources.get("podcast", [])
    podcast = fetch_podcast(podcast_sources)

    # 11. 组装飞书卡片
    logger.info("--- 步骤 12: 组装飞书卡片 ---")
    card = build_morning_card(
        general_news=general_top5,
        interest_news=interest_top5,
        bilibili_videos=bilibili_videos,
        bili_section_name=bili_config.get("name", "求职就业") if bili_config else "",
        weather=weather,
        quote=quote,
        podcast=podcast,
        date_str=date_str,
        llm_degraded=llm_degraded,
    )

    write_card_artifact(card, date_str)
    write_audit_artifact(
        date_str,
        fetched_for_quality,
        all_selected,
        source_quality,
        link_reports,
        card=card,
        llm_degraded=llm_degraded,
    )

    # 12. 推送
    logger.info("--- 步骤 13: 飞书推送 ---")
    feishu_config = config.get("publisher", {}).get("feishu", {})
    sent = send_feishu_card(card, feishu_config)
    if not sent:
        logger.error("飞书推送失败，晨报任务按失败退出，防止 GitHub Actions 假成功")
        sys.exit(1)
    mark_sent("morning", send_date, metadata={"date": date_str})

    # 13. 记录已推送文章
    logger.info("--- 步骤 14: 记录已推送 ---")
    mark_seen(all_selected, seen_data)
    seen_data = cleanup_old(seen_data, config.get("dedup", {}).get("retention_days", 7))
    save_seen(seen_data)

    logger.info("========== 早报完成 ==========")


if __name__ == "__main__":
    main()
