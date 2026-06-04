import os
import time
import requests
from src.utils.logger import get_logger

logger = get_logger("llm_client")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


TIMEOUT = _env_int("OPENAI_TIMEOUT", 60)
MAX_RETRIES = _env_int("OPENAI_MAX_RETRIES", 1)
RETRY_DELAY = _env_int("OPENAI_RETRY_DELAY", 5)
ERROR_BODY_LIMIT = _env_int("OPENAI_ERROR_BODY_LIMIT", 500)


def _raise_for_status_with_body(resp: requests.Response) -> None:
    if resp.status_code < 400:
        return
    body = (resp.text or "").strip().replace("\n", " ")[:ERROR_BODY_LIMIT]
    message = f"{resp.status_code} Client Error for url: {resp.url}"
    if body:
        message = f"{message} | body: {body}"
    raise requests.HTTPError(message, response=resp)


def _is_retryable_error(error: requests.exceptions.RequestException) -> bool:
    if isinstance(error, requests.HTTPError):
        status = getattr(error.response, "status_code", None)
        return status == 429 or (status is not None and 500 <= status < 600)
    return isinstance(error, (requests.Timeout, requests.ConnectionError))


def chat_completion(
    messages: list[dict],
    model: str = "gpt-5.5",
    api_key: str = None,
    base_url: str = None,
    temperature: float = 0.3,
    max_tokens: int = 1000,
    timeout: int | None = None,
    max_retries: int | None = None,
) -> str:
    key = api_key or os.getenv("OPENAI_API_KEY", "")
    if not key:
        raise ValueError("OPENAI_API_KEY 未设置")

    url = base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    endpoint = f"{url.rstrip('/')}/chat/completions"
    request_timeout = timeout or TIMEOUT
    retries = MAX_RETRIES if max_retries is None else max_retries

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    for attempt in range(retries + 1):
        try:
            resp = requests.post(endpoint, headers=headers, json=payload, timeout=request_timeout)
            _raise_for_status_with_body(resp)
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except requests.exceptions.RequestException as e:
            if attempt >= retries or not _is_retryable_error(e):
                raise
            logger.warning(
                f"LLM 请求失败，准备重试 ({attempt + 1}/{retries})：{type(e).__name__}: {e}"
            )
            time.sleep(RETRY_DELAY)

    raise RuntimeError("LLM 请求失败")
