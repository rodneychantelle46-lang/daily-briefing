import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from src.publishers.feishu import send_feishu_card
from src.publishers.telegram import send_telegram_brief
from src.utils.logger import get_logger

logger = get_logger("notify_failure")
APP_TZ = ZoneInfo("Asia/Shanghai")


def _github_run_url() -> str:
    explicit = os.getenv("GITHUB_RUN_URL", "").strip()
    if explicit:
        return explicit

    server = os.getenv("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    run_id = os.getenv("GITHUB_RUN_ID", "").strip()
    if repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


def build_failure_card(label: str, reason: str, run_url: str = "", workflow: str = "") -> dict:
    now = datetime.now(APP_TZ).strftime("%Y-%m-%d %H:%M")
    workflow_text = workflow or os.getenv("GITHUB_WORKFLOW", "") or "GitHub Actions"
    run_text = f"[{os.getenv('GITHUB_RUN_ID', '查看运行')}]({run_url})" if run_url else "未获取到 run URL"

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "red",
            "title": {"tag": "plain_text", "content": f"⚠️ {label}失败告警"},
        },
        "elements": [
            {
                "tag": "markdown",
                "content": f"**结论**\n{label}自动链路失败，已阻断正式推送，避免把降级内容伪装成正式简报。",
            },
            {
                "tag": "markdown",
                "content": f"**原因**\n{reason or 'GitHub Actions 执行失败，详见运行日志和 artifact。'}",
            },
            {
                "tag": "markdown",
                "content": f"**排查入口**\nWorkflow：{workflow_text}\nRun：{run_text}\n时间：{now}（北京时间）",
            },
        ],
    }


def _feishu_config_from_env() -> dict:
    return {
        "mode": "app",
        "app_id": os.getenv("FEISHU_APP_ID", ""),
        "app_secret": os.getenv("FEISHU_APP_SECRET", ""),
        "receive_id_type": os.getenv("FEISHU_RECEIVE_ID_TYPE", "open_id"),
        "receive_id": os.getenv("FEISHU_RECEIVE_ID", ""),
    }


def _telegram_config_from_env() -> dict:
    return {
        "bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
        "thread_id": os.getenv("TELEGRAM_THREAD_ID", ""),
    }


def main() -> int:
    load_dotenv()
    label = os.getenv("BRIEFING_LABEL", "简报")
    reason = os.getenv("BRIEFING_FAILURE_REASON", "GitHub Actions 执行失败")
    run_url = _github_run_url()
    card = build_failure_card(label=label, reason=reason, run_url=run_url)

    telegram_config = _telegram_config_from_env()
    if telegram_config["bot_token"] and telegram_config["chat_id"]:
        sent = send_telegram_brief(card, telegram_config)
        if not sent:
            logger.error("失败告警发送失败")
            return 1
        logger.info("失败告警发送成功")
        return 0

    config = _feishu_config_from_env()

    if not all([config["app_id"], config["app_secret"], config["receive_id"]]):
        logger.warning("飞书告警配置不完整，跳过失败告警发送")
        return 0

    sent = send_feishu_card(card, config)
    if not sent:
        logger.error("失败告警发送失败")
        return 1
    logger.info("失败告警发送成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())
