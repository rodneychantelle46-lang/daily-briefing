import json
import os
import sys
import yaml
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.processors.llm_generator import generate_tip, summarize_github_repos
from src.fetchers.github_fetcher import fetch_trending_repos
from src.publishers.feishu import build_afternoon_card
from src.publishers.telegram import send_telegram_brief
from src.utils.logger import get_logger
from src.utils.send_guard import already_sent, mark_sent

logger = get_logger("afternoon")
APP_TZ = ZoneInfo("Asia/Shanghai")
GENERATION_STATUS_DEGRADED = "degraded"


def local_now() -> datetime:
    return datetime.now(APP_TZ)


def load_config() -> dict:
    config_path = PROJECT_ROOT / "config" / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    _resolve_env(config)
    return config


def write_afternoon_artifact(
    card: dict,
    tips: list[dict],
    github_repos: list[dict],
    date_str: str,
    send_blocked: bool = False,
    aborted_reason: str = "",
) -> Path:
    artifacts_dir = PROJECT_ROOT / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    filename = f"afternoon-card-{local_now().strftime('%Y%m%d-%H%M%S')}.json"
    path = artifacts_dir / filename
    payload = {
        "date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "quality_summary": build_afternoon_quality_summary(
            card,
            tips,
            github_repos,
            send_blocked=send_blocked,
            aborted_reason=aborted_reason,
        ),
        "card": card,
        "tips": tips,
        "github_repos": github_repos,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info(f"午报卡片已保存到 artifact: {path}")
    return path


def build_afternoon_quality_summary(
    card: dict,
    tips: list[dict],
    github_repos: list[dict],
    send_blocked: bool = False,
    aborted_reason: str = "",
) -> dict:
    tip_degraded_count = sum(1 for tip in tips if _item_degraded(tip))
    github_degraded_count = sum(1 for repo in github_repos if _item_degraded(repo) or repo.get("deep_read_status") == GENERATION_STATUS_DEGRADED)
    tip_link_count = sum(len(tip.get("links") or []) for tip in tips)
    github_link_count = sum(1 for repo in github_repos if repo.get("url"))
    return {
        "tip_count": len(tips),
        "tip_degraded_count": tip_degraded_count,
        "github_repo_count": len(github_repos),
        "github_degraded_count": github_degraded_count,
        "fallback_degraded_count": tip_degraded_count + github_degraded_count,
        "link_count": tip_link_count + github_link_count,
        "tip_link_count": tip_link_count,
        "github_link_count": github_link_count,
        "send_blocked": send_blocked,
        "aborted_reason": aborted_reason,
        "generation_errors": _generation_error_summary(tips, github_repos),
        "card": _summarize_card(card),
    }


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


def _summarize_card(card: dict) -> dict:
    elements = card.get("elements", []) if isinstance(card, dict) else []
    return {
        "header": card.get("header", {}).get("title", {}).get("content", "") if isinstance(card, dict) else "",
        "section_count": len(elements),
        "markdown_blocks": sum(1 for e in elements if e.get("tag") == "markdown"),
        "hr_blocks": sum(1 for e in elements if e.get("tag") == "hr"),
        "markdown_chars": sum(len(e.get("content", "")) for e in elements if e.get("tag") == "markdown"),
    }


def _item_degraded(item: dict) -> bool:
    return item.get("generation_status") == GENERATION_STATUS_DEGRADED


def _afternoon_degraded(tips: list[dict], github_repos: list[dict]) -> bool:
    return any(_item_degraded(tip) for tip in tips) or any(
        _item_degraded(repo) or repo.get("deep_read_status") == GENERATION_STATUS_DEGRADED
        for repo in github_repos
    )


def _generation_error_summary(tips: list[dict], github_repos: list[dict]) -> dict:
    summary: dict[str, int] = {}
    for item in [*tips, *github_repos]:
        reason = item.get("generation_error") or item.get("deep_read_error")
        if not reason:
            continue
        summary[reason] = summary.get(reason, 0) + 1
    return summary


def _allow_degraded_afternoon(config: dict) -> bool:
    for env_name in ("ALLOW_DEGRADED_AFTERNOON", "AFTERNOON_ALLOW_DEGRADED"):
        value = os.getenv(env_name)
        if value is not None:
            return _truthy(value)

    root_config = config if isinstance(config, dict) else {}
    llm_config = root_config.get("llm", {})
    value = llm_config.get("allow_degraded_afternoon", root_config.get("allow_degraded_afternoon", False))
    return _truthy(value)


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "允许", "是"}


def _github_empty_degraded_repo() -> dict:
    return {
        "name": "GitHub Trending",
        "url": "",
        "description": "GitHub Trending 未获取到候选项目，已按降级处理。",
        "summary": "GitHub Trending 未获取到候选项目，已阻断正式午报发送。",
        "generation_status": GENERATION_STATUS_DEGRADED,
        "generation_error": "github_trending_empty",
        "deep_read_status": GENERATION_STATUS_DEGRADED,
        "deep_read_error": "github_trending_empty",
        "quality_score": 0,
    }


def main():
    load_dotenv(PROJECT_ROOT / ".env")

    logger.info("========== 午报开始 ==========")
    config = load_config()
    send_date = local_now().strftime("%Y-%m-%d")
    if already_sent("afternoon", send_date):
        return

    llm_config = config.get("llm", {})
    model = llm_config.get("model", "gpt-5.5")
    api_key = llm_config.get("api_key", "")
    base_url = llm_config.get("base_url", "")

    # 1. 生成三条内容
    logger.info("--- 步骤 1: 生成计网 × AI 知识学习 ---")
    learning = generate_tip("cs_ai_learning", model=model, api_key=api_key, base_url=base_url)

    logger.info("--- 步骤 2: 生成心理学/经济学技巧 ---")
    psychology = generate_tip("psychology", model=model, api_key=api_key, base_url=base_url)

    logger.info("--- 步骤 3: 生成品牌洞察 ---")
    brand = generate_tip("brand_insight", model=model, api_key=api_key, base_url=base_url)

    tips = [learning, psychology, brand]

    # 2. GitHub 热门项目
    logger.info("--- 步骤 4: GitHub 热门项目 ---")
    repos = fetch_trending_repos(count=5)
    if repos:
        repos = summarize_github_repos(repos, model=model, api_key=api_key, base_url=base_url)
    else:
        repos = [_github_empty_degraded_repo()]

    # 3. 组装飞书卡片
    logger.info("--- 步骤 5: 组装飞书卡片 ---")
    date_str = local_now().strftime("%Y年%m月%d日")
    card = build_afternoon_card(tips=tips, date_str=date_str, github_repos=repos)

    llm_degraded = _afternoon_degraded(tips, repos)
    allow_degraded = _allow_degraded_afternoon(config)
    aborted_reason = ""
    send_blocked = False
    if llm_degraded and not allow_degraded:
        send_blocked = True
        aborted_reason = "午报生成链路出现降级，已停止发送正式午报，避免把 fallback 包装成正式判断。"
    elif llm_degraded and allow_degraded:
        logger.warning("午报存在降级内容，但配置显式允许降级发送")

    write_afternoon_artifact(
        card,
        tips,
        repos or [],
        date_str,
        send_blocked=send_blocked,
        aborted_reason=aborted_reason,
    )

    if send_blocked:
        logger.error(aborted_reason)
        sys.exit(2)

    # 4. 推送
    logger.info("--- 步骤 6: Telegram 推送 ---")
    telegram_config = config.get("publisher", {}).get("telegram", {})
    sent = send_telegram_brief(card, telegram_config)
    if not sent:
        logger.error("Telegram 推送失败，午报任务按失败退出，防止 GitHub Actions 假成功")
        sys.exit(1)
    mark_sent("afternoon", send_date, metadata={"date": date_str})

    logger.info("========== 午报完成 ==========")


if __name__ == "__main__":
    main()
