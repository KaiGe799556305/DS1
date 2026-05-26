import argparse
import base64
import json
import sys
from pathlib import Path
from urllib import request
from urllib.error import HTTPError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.select_frames_ai import read_provider_config


PROMPT = (
    "你是短视频拆解分析师。请直接观看这个短视频，输出中文 JSON。"
    "字段必须包括 supported, cover_title, topic, product, copy, storyboard, risk, summary。"
    "cover_title/topic/product/copy/risk/summary 都是对象，包含 title, desc, metrics。"
    "storyboard 是数组，返回 6-8 个镜头对象，每个对象包含 shot, visual, voice_or_subtitle, props_scene, duration_sec。"
    "请重点分析画面变化、节奏、转场、商品/场景露出、口播或字幕线索。只输出 JSON。"
)


def video_data_url(path):
    raw = Path(path).read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:video/mp4;base64,{encoded}"


def call_chat_completions(provider, model, video_path, content_type):
    base_url, api_key, selected_model = read_provider_config(provider, model)
    if content_type == "video_url":
        video_part = {"type": "video_url", "video_url": {"url": video_data_url(video_path)}}
    else:
        video_part = {"type": "input_video", "input_video": {"data": video_data_url(video_path), "mime_type": "video/mp4"}}

    payload = {
        "model": selected_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    video_part,
                ],
            }
        ],
    }
    if provider != "ark":
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
        with request.urlopen(req, timeout=180) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def main():
    parser = argparse.ArgumentParser(description="Probe direct video input support for chat completions.")
    parser.add_argument("--video", required=True)
    parser.add_argument("--provider", choices=["ark", "openai"], default="ark")
    parser.add_argument("--model", default="")
    parser.add_argument("--content-type", choices=["video_url", "input_video"], default="video_url")
    args = parser.parse_args()

    status, body = call_chat_completions(args.provider, args.model, args.video, args.content_type)
    print(json.dumps({"status": status, "body": body[:4000]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
