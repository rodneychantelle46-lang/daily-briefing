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
from src.publishers.feishu import build_afternoon_card, send_feishu_card
from src.utils.logger import get_logger

logger = get_logger("afternoon")
APP_TZ = ZoneInfo("Asia/Shanghai")


def local_now() -> datetime:
    return datetime.now(APP_TZ)


def load_config() -> dict:
    config_path = PROJECT_ROOT / "config" / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    _resolve_env(config)
    return config


def write_afternoon_artifact(card: dict, tips: list[dict], github_repos: list[dict], date_str: str) -> Path:
    artifacts_dir = PROJECT_ROOT / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    filename = f"afternoon-card-{local_now().strftime('%Y%m%d-%H%M%S')}.json"
    path = artifacts_dir / filename
    payload = {
        "date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "card": card,
        "tips": tips,
        "github_repos": github_repos,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info(f"午报卡片已保存到 artifact: {path}")
    return path


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

    logger.info("========== 午报开始 ==========")
    config = load_config()

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

    # 3. 组装飞书卡片
    logger.info("--- 步骤 5: 组装飞书卡片 ---")
    date_str = local_now().strftime("%Y年%m月%d日")
    card = build_afternoon_card(tips=tips, date_str=date_str, github_repos=repos)
    write_afternoon_artifact(card, tips, repos or [], date_str)

    # 4. 推送
    logger.info("--- 步骤 6: 飞书推送 ---")
    feishu_config = config.get("publisher", {}).get("feishu", {})
    sent = send_feishu_card(card, feishu_config)
    if not sent:
        logger.error("飞书推送失败，午报任务按失败退出，防止 GitHub Actions 假成功")
        sys.exit(1)

    logger.info("========== 午报完成 ==========")


if __name__ == "__main__":
    main()
