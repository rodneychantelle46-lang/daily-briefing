import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from src.utils.logger import get_logger

logger = get_logger("send_guard")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PATH = PROJECT_ROOT / "data" / "send_history.json"
APP_TZ = ZoneInfo("Asia/Shanghai")
FORCE_ENV = "DAILY_BRIEFING_FORCE_SEND"


def today_key(now: datetime | None = None) -> str:
    current = now or datetime.now(APP_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=APP_TZ)
    return current.astimezone(APP_TZ).strftime("%Y-%m-%d")


def load_history(path: Path | None = None) -> dict:
    p = path or DEFAULT_PATH
    try:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"加载发送历史失败: {e}")
    return {}


def save_history(data: dict, path: Path | None = None) -> None:
    p = path or DEFAULT_PATH
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception as e:
        logger.warning(f"保存发送历史失败: {e}")


def already_sent(kind: str, date_key: str | None = None, path: Path | None = None) -> bool:
    if os.getenv(FORCE_ENV, "").strip() == "1":
        logger.warning(f"{FORCE_ENV}=1，跳过 {kind} 幂等检查")
        return False

    key = date_key or today_key()
    history = load_history(path)
    record = history.get(kind, {}).get(key)
    if isinstance(record, dict) and record.get("sent") is True:
        logger.info(f"{kind} {key} 已有成功发送记录，跳过重复推送")
        return True
    return False


def mark_sent(
    kind: str,
    date_key: str | None = None,
    metadata: dict | None = None,
    path: Path | None = None,
    retention_days: int = 30,
) -> None:
    key = date_key or today_key()
    history = load_history(path)
    by_kind = history.setdefault(kind, {})
    payload = {
        "sent": True,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }
    if metadata:
        payload.update(metadata)
    by_kind[key] = payload
    _cleanup(history, retention_days)
    save_history(history, path)
    logger.info(f"已记录 {kind} {key} 发送成功")


def _cleanup(history: dict, retention_days: int) -> None:
    cutoff = (datetime.now(APP_TZ) - timedelta(days=retention_days)).strftime("%Y-%m-%d")
    for kind, records in list(history.items()):
        if not isinstance(records, dict):
            history.pop(kind, None)
            continue
        for key in list(records.keys()):
            if key < cutoff:
                records.pop(key, None)
