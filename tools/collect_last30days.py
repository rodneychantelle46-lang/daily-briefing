import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.fetchers.last30days_fetcher import normalize_last30days_payload

DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "last30days_topics.yaml"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "last30days_findings.json"
DEFAULT_SKILL_DIR = PROJECT_ROOT.parent / "skills" / "last30days-official"


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    output_path = Path(args.output)

    try:
        config = load_config(config_path)
    except Exception as exc:
        write_payload(output_path, build_error_payload(f"config_error: {exc}"))
        return 0

    if not config.get("enabled", True):
        write_payload(
            output_path,
            {
                "status": "disabled",
                "generated_at": utc_now(),
                "items": [],
                "topics": [],
                "errors": [],
            },
        )
        return 0

    skill_dir = resolve_skill_dir(args.skill_dir)
    script_path = skill_dir / "scripts" / "last30days.py"
    if not script_path.exists():
        write_payload(output_path, build_error_payload(f"last30days_script_missing: {script_path}"))
        return 0

    lookback_days = int(config.get("lookback_days") or 30)
    max_items = int(config.get("max_items") or 5)
    sources = [str(source) for source in config.get("sources") or ["hackernews", "github", "reddit"]]
    topics = [topic for topic in config.get("topics") or [] if isinstance(topic, dict)]
    timeout_seconds = int(os.getenv("LAST30DAYS_TIMEOUT_SECONDS") or config.get("timeout_seconds") or 180)

    topic_results = []
    with tempfile.TemporaryDirectory(prefix="last30days-plans-") as tmpdir:
        for topic in topics:
            topic_results.append(
                collect_topic(
                    script_path=script_path,
                    skill_dir=skill_dir,
                    plan_dir=Path(tmpdir),
                    topic=topic,
                    sources=sources,
                    lookback_days=lookback_days,
                    max_items=max_items,
                    timeout_seconds=timeout_seconds,
                )
            )

    items = round_robin_items([result.get("items", []) for result in topic_results], max_items=max_items)
    errors = [error for result in topic_results for error in result.get("errors", [])]
    if items:
        status = "partial" if errors else "ok"
    else:
        status = "error" if errors else "empty"
    payload = {
        "status": status,
        "generated_at": utc_now(),
        "lookback_days": lookback_days,
        "sources": sources,
        "items": items,
        "topics": topic_results,
        "errors": errors,
    }
    write_payload(output_path, payload)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect last30days sidecar findings for daily briefings.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--skill-dir", default="")
    return parser.parse_args()


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_skill_dir(arg_value: str) -> Path:
    candidates = [
        arg_value,
        os.getenv("LAST30DAYS_SKILL_DIR", ""),
        str(DEFAULT_SKILL_DIR),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser().resolve()
        if (path / "scripts" / "last30days.py").exists():
            return path
    return Path(candidates[-1]).expanduser().resolve()


def collect_topic(
    *,
    script_path: Path,
    skill_dir: Path,
    plan_dir: Path,
    topic: dict,
    sources: list[str],
    lookback_days: int,
    max_items: int,
    timeout_seconds: int,
) -> dict:
    name = str(topic.get("name") or topic.get("query") or "last30days")
    label = str(topic.get("label") or name)
    query = str(topic.get("query") or name)
    topic_sources = [str(source) for source in topic.get("sources") or sources]
    plan_path = plan_dir / f"{safe_slug(name)}.json"
    plan_path.write_text(
        json.dumps(build_plan(query, label, topic_sources), ensure_ascii=True, indent=2),
        encoding="utf-8",
    )

    command = [
        sys.executable,
        str(script_path),
        query,
        "--emit=json",
        "--quick",
        "--lookback-days",
        str(lookback_days),
        "--search",
        ",".join(topic_sources),
        "--plan",
        str(plan_path),
    ]

    try:
        completed = subprocess.run(
            command,
            cwd=str(skill_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return topic_error(name, label, query, f"timeout_after_{timeout_seconds}s")
    except Exception as exc:
        return topic_error(name, label, query, f"subprocess_error: {exc}")

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip().splitlines()
        reason = stderr[-1] if stderr else f"exit_code_{completed.returncode}"
        return topic_error(name, label, query, reason)

    try:
        raw_report = parse_json_stdout(completed.stdout)
    except Exception as exc:
        return topic_error(name, label, query, f"json_parse_error: {exc}")

    items = normalize_last30days_payload(raw_report, topic=name, label=label, max_items=max_items)
    errors = []
    if isinstance(raw_report, dict):
        for source, error in (raw_report.get("errors_by_source") or {}).items():
            errors.append(f"{name}/{source}: {error}")

    return {
        "name": name,
        "label": label,
        "query": query,
        "status": "ok" if items else "empty",
        "item_count": len(items),
        "items": items,
        "errors": errors,
        "warnings": raw_report.get("warnings", []) if isinstance(raw_report, dict) else [],
    }


def build_plan(query: str, label: str, sources: list[str]) -> dict:
    return {
        "intent": "concept",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "debate",
        "raw_topic": query,
        "source_weights": {source: 1.0 for source in sources},
        "subqueries": [
            {
                "label": label,
                "search_query": query,
                "ranking_query": f"Recent community discussion and developer signals about {query}",
                "sources": sources,
                "weight": 1.0,
            }
        ],
        "notes": ["Generated by daily-briefing sidecar collector."],
    }


def parse_json_stdout(stdout: str) -> dict:
    text = (stdout or "").strip()
    if not text:
        raise ValueError("empty stdout")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def round_robin_items(item_groups: list[list[dict]], *, max_items: int) -> list[dict]:
    output = []
    seen = set()
    index = 0
    while len(output) < max_items:
        added = False
        for group in item_groups:
            if index >= len(group):
                continue
            item = group[index]
            key = item.get("url") or f"{item.get('source')}::{item.get('title')}"
            if key not in seen:
                seen.add(key)
                output.append(item)
                added = True
                if len(output) >= max_items:
                    break
        if not added:
            break
        index += 1
    return output


def topic_error(name: str, label: str, query: str, reason: str) -> dict:
    return {
        "name": name,
        "label": label,
        "query": query,
        "status": "error",
        "item_count": 0,
        "items": [],
        "errors": [f"{name}: {reason}"],
        "warnings": [],
    }


def build_error_payload(reason: str) -> dict:
    return {
        "status": "error",
        "generated_at": utc_now(),
        "items": [],
        "topics": [],
        "errors": [reason],
    }


def write_payload(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"last30days sidecar wrote {path} with status={payload.get('status')}")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_slug(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    return slug or "topic"


if __name__ == "__main__":
    raise SystemExit(main())
