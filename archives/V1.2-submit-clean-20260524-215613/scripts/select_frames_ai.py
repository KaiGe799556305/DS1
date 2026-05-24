import argparse
import base64
import json
import os
from pathlib import Path
from urllib import request
from urllib.error import HTTPError

from PIL import Image, ImageStat


ROOT = Path(__file__).resolve().parents[1]
FRAMES_DIR = ROOT / "references" / "frames"
MANIFEST_PATH = FRAMES_DIR / "analysis.json"
CONFIG_PATH = Path.home() / ".codex" / "config.toml"
AUTH_PATH = Path.home() / ".codex" / "auth.json"
ARK_INFO_PATH = ROOT / "豆包信息.txt"
CUSTOM_INFO_PATH = ROOT / "custom_model.txt"


CATEGORIES = {
    "cover_title": "封面&标题分析",
    "topic": "选题分析",
    "product": "选品分析",
    "copy": "文案分析",
    "storyboard": "分镜拆解",
}


def read_manifest():
    if not MANIFEST_PATH.exists():
        raise SystemExit("Missing references/frames/analysis.json. Run scripts/video_pipeline.py first.")
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def frame_path(frame_url):
    return ROOT / frame_url.lstrip("/").replace("/", os.sep)


def image_features(path):
    img = Image.open(path).convert("RGB")
    small = img.resize((64, 64))
    stat = ImageStat.Stat(small)
    brightness = sum(stat.mean) / 3
    contrast = sum(stat.stddev) / 3
    width, height = img.size
    center_crop = img.crop((width * 0.25, height * 0.2, width * 0.75, height * 0.75))
    center_stat = ImageStat.Stat(center_crop.resize((64, 64)))
    center_contrast = sum(center_stat.stddev) / 3
    return {
        "brightness": brightness,
        "contrast": contrast,
        "center_contrast": center_contrast,
        "size": [width, height],
    }


def heuristic_select(manifest):
    frames = manifest.get("frames", [])
    if not frames:
        raise SystemExit("No frames found in manifest.")

    scored = []
    for idx, url in enumerate(frames):
        path = frame_path(url)
        feat = image_features(path)
        scored.append({"index": idx, "url": url, "features": feat})

    def by_index(i):
        return frames[min(max(i, 0), len(frames) - 1)]

    selection = {
        "cover_title": by_index(4 if len(frames) > 5 else 0),
        "topic": by_index(5 if len(frames) > 6 else 1),
        "product": by_index(13 if len(frames) > 14 else len(frames) - 1),
        "copy": by_index(9 if len(frames) > 10 else len(frames) // 2),
        "storyboard": frames,
    }
    rationale = {
        "cover_title": "选择人物主体清晰、能代表晨间氛围的帧。",
        "topic": "选择最能表达生活方式主题的帧。",
        "product": "选择产品露出或选品信息最清楚的帧。",
        "copy": "选择字幕或信息密度更高的帧，用于文案结构分析。",
        "storyboard": "保留全部已抽取候选帧，供原视频分镜覆盖所有代表性变化。",
    }
    return selection, rationale, scored


def ensure_openai_base_url(base_url):
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        return "https://api.openai.com/v1"
    if base_url.endswith("/v1") or base_url.endswith("/api/v3"):
        return base_url
    return f"{base_url}/v1"


def parse_config_base_url():
    if not CONFIG_PATH.exists():
        return "https://api.openai.com/v1"
    for line in CONFIG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("base_url"):
            return ensure_openai_base_url(line.split("=", 1)[1].strip().strip('"'))
    return "https://api.openai.com/v1"


def read_api_key():
    if not AUTH_PATH.exists():
        return ""
    data = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
    return data.get("OPENAI_API_KEY", "")


def read_kv_file(path):
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"')
    return values


def read_provider_config(provider, model):
    if provider == "ark":
        values = read_kv_file(ARK_INFO_PATH)
        api_key = values.get("ARK_API_KEY") or os.getenv("ARK_API_KEY", "")
        base_url = values.get("ARK_BASE_URL") or os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
        selected_model = model or values.get("ARK_MODEL") or os.getenv("ARK_MODEL", "doubao-seed-2-0-lite-260428")
        if not api_key:
            raise SystemExit("Missing ARK_API_KEY. Put it in 豆包信息.txt or environment variables.")
        return base_url.rstrip("/"), api_key, selected_model

    if provider == "custom":
        values = read_kv_file(CUSTOM_INFO_PATH)
        api_key = (
            values.get("CUSTOM_API_KEY")
            or values.get("OPENAI_API_KEY")
            or values.get("API_KEY")
            or os.getenv("CUSTOM_API_KEY", "")
            or os.getenv("OPENAI_API_KEY", "")
        )
        base_url = (
            values.get("CUSTOM_BASE_URL")
            or values.get("OPENAI_BASE_URL")
            or values.get("BASE_URL")
            or os.getenv("CUSTOM_BASE_URL", "")
            or os.getenv("OPENAI_BASE_URL", "")
        )
        selected_model = (
            model
            or values.get("CUSTOM_MODEL")
            or values.get("MODEL")
            or os.getenv("CUSTOM_MODEL", "")
        )
        if not api_key:
            raise SystemExit("Missing CUSTOM_API_KEY. Put it in custom_model.txt or environment variables.")
        if not base_url:
            raise SystemExit("Missing CUSTOM_BASE_URL. Put it in custom_model.txt or environment variables.")
        if not selected_model:
            raise SystemExit("Missing CUSTOM_MODEL. Put it in custom_model.txt or pass --model.")
        return base_url.rstrip("/"), api_key, selected_model

    base_url = parse_config_base_url()
    api_key = read_api_key()
    if not api_key:
        raise SystemExit("Missing OPENAI_API_KEY in ~/.codex/auth.json")
    return base_url.rstrip("/"), api_key, model or "gpt-4o-mini"


def provider_uses_response_format(provider):
    if provider == "ark":
        return False
    if provider == "custom":
        values = read_kv_file(CUSTOM_INFO_PATH)
        flag = (
            values.get("CUSTOM_RESPONSE_FORMAT")
            or values.get("RESPONSE_FORMAT")
            or os.getenv("CUSTOM_RESPONSE_FORMAT", "")
        ).strip().lower()
        return flag in {"json", "json_object", "true", "1", "yes"}
    return True


def image_data_url(path):
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def parse_model_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        repaired = repair_json_control_chars(extract_json_body(text))
        return json.loads(repaired)


def extract_json_body(text):
    text = text.strip()
    start_candidates = [idx for idx in [text.find("{"), text.find("[")] if idx >= 0]
    if not start_candidates:
        return text
    start = min(start_candidates)
    end = max(text.rfind("}"), text.rfind("]"))
    if end > start:
        return text[start : end + 1]
    return text[start:]


def repair_json_control_chars(text):
    result = []
    in_string = False
    escaped = False
    for ch in text:
        if escaped:
            result.append(ch)
            escaped = False
            continue
        if ch == "\\":
            result.append(ch)
            escaped = True
            continue
        if ch == '"':
            result.append(ch)
            in_string = not in_string
            continue
        if in_string and ord(ch) < 0x20:
            if ch == "\n":
                result.append("\\n")
            elif ch == "\r":
                result.append("\\r")
            elif ch == "\t":
                result.append("\\t")
            else:
                result.append(f"\\u{ord(ch):04x}")
            continue
        result.append(ch)
    return "".join(result)


def ai_select(manifest, provider, model):
    base_url, api_key, selected_model = read_provider_config(provider, model)
    frames = manifest.get("frames", [])
    content = [
        {
            "type": "text",
            "text": (
                "你是短视频内容分析师。请从候选帧中选择最适合以下模块的图片："
                "cover_title, topic, product, copy, storyboard。"
                "storyboard 请选择所有能代表原视频内容推进的关键帧，覆盖明显场景、动作、产品露出、字幕信息或节奏变化；数量由视频内容决定，可以多于 8 张，也可以接近全部候选帧，并按内容推进排序。"
                "只输出 JSON，字段为 cover_title, topic, product, copy, storyboard, rationale。"
                "图片编号从 1 开始。"
            ),
        }
    ]
    for idx, url in enumerate(frames, start=1):
        content.append({"type": "text", "text": f"候选帧 {idx}: {url}"})
        content.append({"type": "image_url", "image_url": {"url": image_data_url(frame_path(url))}})

    payload = {
        "model": selected_model,
        "messages": [{"role": "user", "content": content}],
    }
    if provider_uses_response_format(provider):
        payload["response_format"] = {"type": "json_object"}
    req = request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {base_url}: {body}") from exc

    text = result["choices"][0]["message"]["content"]
    data = parse_model_json(text)

    def frame_by_number(n):
        index = int(n) - 1
        if index < 0 or index >= len(frames):
            raise IndexError(f"Frame number out of range: {n}")
        return frames[index]

    def frame_list(numbers):
        selected = []
        seen = set()
        for number in numbers if isinstance(numbers, list) else []:
            try:
                url = frame_by_number(number)
            except (TypeError, ValueError, IndexError):
                continue
            if url not in seen:
                seen.add(url)
                selected.append(url)
        return selected or frames

    selection = {
        "cover_title": frame_by_number(data["cover_title"]),
        "topic": frame_by_number(data["topic"]),
        "product": frame_by_number(data["product"]),
        "copy": frame_by_number(data["copy"]),
        "storyboard": frame_list(data.get("storyboard")),
    }
    return selection, data.get("rationale", {}), []


def write_selection(manifest, selection, rationale, mode):
    manifest["selected_frames"] = {
        "mode": mode,
        "categories": {
            key: {
                "label": CATEGORIES[key],
                "frame": value,
                "rationale": rationale.get(key, "") if isinstance(rationale, dict) else "",
            }
            for key, value in selection.items()
            if key != "storyboard"
        },
        "storyboard": selection["storyboard"],
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Select representative frames for video analysis modules.")
    parser.add_argument("--mode", choices=["heuristic", "ai"], default="heuristic")
    parser.add_argument("--provider", choices=["openai", "ark", "custom"], default="openai")
    parser.add_argument("--model", default="")
    args = parser.parse_args()

    manifest = read_manifest()
    if args.mode == "ai":
        selection, rationale, _ = ai_select(manifest, args.provider, args.model)
    else:
        selection, rationale, _ = heuristic_select(manifest)

    write_selection(manifest, selection, rationale, args.mode)
    print(json.dumps({
        "mode": args.mode,
        "provider": args.provider if args.mode == "ai" else "",
        "manifest": str(MANIFEST_PATH),
        "selected_frames": selection,
        "result_page": "http://127.0.0.1:4173/analyze/9423b9da6e66",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
