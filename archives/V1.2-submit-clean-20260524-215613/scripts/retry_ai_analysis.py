import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.analyze_video import (
    ROOT,
    ai_analyze,
    ai_analyze_video,
    ai_style_imitation,
    apply_recommended_imitation_shot_count,
    enforce_imitation_shot_count,
)


RESULTS_DIR = ROOT / "results"


def _local_url_path(path):
    path = (path or "").strip()
    if not path:
        return None
    return ROOT / path.lstrip("/").replace("/", "\\")


def _load_brief_file(brief_file):
    if not brief_file:
        return None
    path = Path(brief_file).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"Brief file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def retry_ai(task_id, provider="ark", model="", analysis_mode="video", only_style=False, brief=None):
    task_dir = RESULTS_DIR / task_id
    result_path = task_dir / "analysis.json"
    if not result_path.exists():
        raise SystemExit(f"Result not found: {task_id}")

    payload = json.loads(result_path.read_text(encoding="utf-8"))
    if brief:
        payload["brief"] = brief
    payload["brief"] = apply_recommended_imitation_shot_count(
        payload.get("brief") or {},
        len(((payload.get("analysis") or {}).get("storyboard") or []) or ((payload.get("selected_frames") or {}).get("storyboard") or [])),
        payload.get("video_duration_sec") or 0,
    )
    previous = {
        "analysis": payload.get("analysis"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    history = payload.setdefault("analysis_history", [])
    if previous["analysis"]:
        history.append(previous)

    if only_style:
        analysis = payload.get("analysis") or {}
        analysis["style_imitation"] = ai_style_imitation(payload, provider, model, brief=payload.get("brief"))
        payload["analysis"] = analysis
    elif analysis_mode == "video":
        video_path = _local_url_path((payload.get("ai_video") or {}).get("path"))
        if not video_path or not video_path.exists():
            raise SystemExit("No compressed ai_video was found for video-mode retry.")
        analysis = ai_analyze_video(payload, provider, model, video_path)
        payload["ai_video"] = {
            **(payload.get("ai_video") or {}),
            "mode": "video",
            "path": (payload.get("ai_video") or {}).get("path", ""),
        }
    else:
        analysis = ai_analyze(payload, provider, model)
        payload["ai_video"] = {
            **(payload.get("ai_video") or {}),
            "mode": "frames",
        }

    analysis = enforce_imitation_shot_count(analysis, payload.get("brief") or {})
    payload["analysis"] = analysis
    payload["analysis_updated_at"] = datetime.now().isoformat(timespec="seconds")
    payload["analysis_retry"] = {
        "provider": provider,
        "model": model,
        "analysis_mode": analysis_mode,
        "only_style": only_style,
        "history_count": len(history),
    }
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main():
    parser = argparse.ArgumentParser(description="Retry only the AI analysis step for an existing result.")
    parser.add_argument("--id", required=True)
    parser.add_argument("--provider", choices=["ark", "openai", "custom"], default="ark")
    parser.add_argument("--model", default="")
    parser.add_argument("--analysis-mode", choices=["video", "frames"], default="video")
    parser.add_argument("--only-style", action="store_true", help="Only generate/update analysis.style_imitation.")
    parser.add_argument("--brief-file", default="", help="JSON file containing brand/product brief for style imitation.")
    args = parser.parse_args()

    payload = retry_ai(
        args.id,
        provider=args.provider,
        model=args.model,
        analysis_mode=args.analysis_mode,
        only_style=args.only_style,
        brief=_load_brief_file(args.brief_file),
    )
    print(json.dumps({
        "id": payload["id"],
        "result_url": f"http://127.0.0.1:4173/analyze/{payload['id']}",
        "api_result_url": f"http://127.0.0.1:4173/api/result/{payload['id']}",
        "history_count": len(payload.get("analysis_history") or []),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
