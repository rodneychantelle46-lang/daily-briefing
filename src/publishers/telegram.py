import re
import time

import requests

from src.utils.logger import get_logger

logger = get_logger("telegram")

MAX_RETRIES = 2
RETRY_DELAY = 5
MAX_MESSAGE_CHARS = 3600

LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def send_telegram_brief(card: dict, telegram_config: dict) -> bool:
    return send_telegram_text(render_card_as_telegram_text(card), telegram_config)


def send_telegram_text(text: str, telegram_config: dict) -> bool:
    bot_token = telegram_config.get("bot_token", "")
    chat_id = telegram_config.get("chat_id", "")
    thread_id = telegram_config.get("thread_id", "")
    proxy = telegram_config.get("proxy", "")

    if not bot_token or not chat_id:
        logger.error("Telegram 配置不完整（需要 bot_token, chat_id）")
        return False

    chunks = split_telegram_text(text)
    for index, chunk in enumerate(chunks, start=1):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
        }
        if thread_id:
            payload["message_thread_id"] = thread_id
        if len(chunks) > 1:
            payload["text"] = f"{payload['text']}\n\n({index}/{len(chunks)})"
        if not _send_message(bot_token, payload, proxy=proxy):
            return False
    return True


def render_card_as_telegram_text(card: dict) -> str:
    header = card.get("header", {}).get("title", {}).get("content", "").strip()
    parts: list[str] = []
    if header:
        parts.append(header)

    for element in card.get("elements", []):
        if element.get("tag") != "markdown":
            continue
        content = _clean_markdown_for_telegram(element.get("content", ""))
        if content:
            parts.append(content)

    return "\n\n".join(parts).strip()


def split_telegram_text(text: str, max_chars: int = MAX_MESSAGE_CHARS) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(block) <= max_chars:
            current = block
        else:
            chunks.extend(_split_long_block(block, max_chars))

    if current:
        chunks.append(current)
    return chunks


def _split_long_block(block: str, max_chars: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in block.splitlines():
        if len(line) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(line[i:i + max_chars] for i in range(0, len(line), max_chars))
            continue
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = line
    if current:
        chunks.append(current)
    return chunks


def _clean_markdown_for_telegram(text: str) -> str:
    text = LINK_RE.sub(lambda m: f"{m.group(1)}\n{m.group(2)}", text)
    text = text.replace("**", "")
    text = text.replace("`", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _send_message(bot_token: str, payload: dict, proxy: str = "") -> bool:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    proxies = _build_proxies(proxy)
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, timeout=10, proxies=proxies)
            resp.raise_for_status()
            result = resp.json()
            if result.get("ok") is True:
                logger.info("Telegram 推送成功")
                return True
            logger.warning(f"Telegram 返回异常: {result}")
        except Exception as e:
            logger.warning(f"Telegram 推送失败（第 {attempt + 1} 次）: {_sanitize_error(e, bot_token)}")
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)
    logger.error("Telegram 推送最终失败，已达最大重试次数")
    return False


def _build_proxies(proxy: str) -> dict | None:
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def _sanitize_error(error: Exception, bot_token: str) -> str:
    return str(error).replace(bot_token, "<redacted>")
