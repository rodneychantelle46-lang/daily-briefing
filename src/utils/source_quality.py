import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger("source_quality")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PATH = PROJECT_ROOT / "data" / "source_quality.json"


def load_source_quality(path: Path | None = None) -> dict:
    p = path or DEFAULT_PATH
    try:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"加载来源质量数据失败: {e}")
    return {"sources": {}}


def save_source_quality(data: dict, path: Path | None = None):
    p = path or DEFAULT_PATH
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存来源质量数据失败: {e}")


def source_scores(data: dict) -> dict[str, float]:
    sources = data.get("sources", {}) if isinstance(data, dict) else {}
    return {name: float(info.get("score", 0.85)) for name, info in sources.items()}


def update_source_quality(
    fetched_articles: list[dict],
    selected_articles: list[dict],
    data: dict | None = None,
) -> dict:
    data = data or load_source_quality()
    sources = data.setdefault("sources", {})

    fetched = Counter(a.get("source", "未知来源") for a in fetched_articles if a.get("source"))
    selected = Counter(a.get("source", "未知来源") for a in selected_articles if a.get("source"))
    now = datetime.now().isoformat(timespec="seconds")

    for source, fetched_count in fetched.items():
        item = sources.setdefault(source, {
            "runs": 0,
            "fetched_total": 0,
            "selected_total": 0,
            "score": 0.85,
        })
        item["runs"] = int(item.get("runs", 0)) + 1
        item["fetched_total"] = int(item.get("fetched_total", 0)) + fetched_count
        item["selected_total"] = int(item.get("selected_total", 0)) + selected.get(source, 0)
        item["last_fetched"] = fetched_count
        item["last_selected"] = selected.get(source, 0)
        item["last_seen_at"] = now
        item["score"] = _score_source(item)

    data["updated_at"] = now
    save_source_quality(data)
    logger.info(f"来源质量评分已更新：{len(fetched)} 个来源")
    return data


def summarize_source_counts(articles: list[dict]) -> dict[str, int]:
    return dict(Counter(a.get("source", "未知来源") for a in articles if a.get("source")))


def _score_source(item: dict) -> float:
    fetched_total = max(1, int(item.get("fetched_total", 0)))
    selected_total = int(item.get("selected_total", 0))
    runs = int(item.get("runs", 0))

    # 选中率通常很低：一次候选几十条只选 5 条。这里是温和加权，不搞一刀切。
    selected_rate = selected_total / fetched_total
    confidence = min(1.0, runs / 7)
    score = 0.82 + selected_rate * 5.0
    score = 0.82 * (1 - confidence) + score * confidence
    return round(max(0.55, min(1.35, score)), 3)
