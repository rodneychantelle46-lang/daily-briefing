import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from src.utils.logger import get_logger

logger = get_logger("quote_fetcher")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
APP_TZ = ZoneInfo("Asia/Shanghai")
HISTORY_DAYS = 14


def fetch_quote(quotes_path: str = None, history_path: str = None, today: str = None) -> dict:
    path = Path(quotes_path) if quotes_path else PROJECT_ROOT / "config" / "quotes.json"
    history_file = Path(history_path) if history_path else PROJECT_ROOT / "data" / "quote_history.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            quotes = json.load(f)
        if not quotes:
            logger.warning("名言库为空")
            return _fallback_quote()

        today_str = today or datetime.now(APP_TZ).strftime("%Y-%m-%d")
        history = _load_history(history_file)

        # 同一天重跑保持同一句，避免手动补发导致当天文案跳变。
        existing = _find_today_quote(history, today_str, quotes)
        if existing:
            logger.info(f"每日一句复用当天记录: \"{existing['text']}\" —— {existing['author']}")
            return existing

        index = _select_quote_index(today_str, quotes, history)
        quote = dict(quotes[index])
        _save_history(history_file, history, today_str, index, quote)
        logger.info(f"每日一句: \"{quote['text']}\" —— {quote['author']}")
        return quote
    except Exception as e:
        logger.warning(f"名言获取失败: {e}")
        return _fallback_quote()


def _fallback_quote() -> dict:
    return {"text": "今天也要加油哦！", "author": "daily-briefing"}


def _load_history(path: Path) -> dict:
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                return data
    except Exception as e:
        logger.warning(f"每日一句历史读取失败，将重建: {e}")
    return {"items": []}


def _find_today_quote(history: dict, today: str, quotes: list[dict]) -> dict | None:
    for item in reversed(history.get("items", [])):
        if item.get("date") != today:
            continue
        index = item.get("index")
        if isinstance(index, int) and 0 <= index < len(quotes):
            return dict(quotes[index])
        text = item.get("text")
        author = item.get("author")
        if text and author:
            return {"text": text, "author": author}
    return None


def _select_quote_index(today: str, quotes: list[dict], history: dict) -> int:
    recent_indices = {
        item.get("index")
        for item in history.get("items", [])[-HISTORY_DAYS:]
        if isinstance(item.get("index"), int)
    }
    start = int(hashlib.md5(today.encode("utf-8")).hexdigest(), 16) % len(quotes)
    for offset in range(len(quotes)):
        candidate = (start + offset) % len(quotes)
        if candidate not in recent_indices:
            return candidate
    return start


def _save_history(path: Path, history: dict, today: str, index: int, quote: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    items = [item for item in history.get("items", []) if item.get("date") != today]
    items.append({
        "date": today,
        "index": index,
        "text": quote.get("text", ""),
        "author": quote.get("author", ""),
    })
    history = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "timezone": str(APP_TZ),
        "history_days": HISTORY_DAYS,
        "items": items[-HISTORY_DAYS:],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
