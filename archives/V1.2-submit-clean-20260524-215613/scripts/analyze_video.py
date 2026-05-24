import argparse
import base64
import json
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path
from urllib import request
from urllib.error import HTTPError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.video_pipeline import (
    ROOT,
    compress_video_for_ai,
    download_with_videodl,
    extract_at_timestamps,
    extract_audio_track,
    extract_frames,
    extract_ocr_for_frames,
    find_cover_file,
    find_cover_for_video,
    make_contact_sheet,
    probe_duration,
    select_timestamps,
    transcribe_audio_track,
    write_manifest,
)
from scripts.select_frames_ai import ai_select, heuristic_select, image_data_url, frame_path, provider_uses_response_format, read_provider_config


RESULTS_DIR = ROOT / "results"
DEFAULT_BRIEF = {
    "brand": "",
    "product": "",
    "selling_points": [],
    "audience": "",
    "platform": "小红书短视频",
    "constraints": [],
    "target_duration_sec": 75,
    "imitation_shot_count": 8,
}


def ensure_dir(path):
    path.mkdir(parents=True, exist_ok=True)


def rationale_text(rationale, key):
    if isinstance(rationale, dict):
        return rationale.get(key, "")
    return str(rationale or "")


def looks_bad_transcript(text):
    if not text:
        return True
    bad_markers = sum(text.count(ch) for ch in ["�", "�", "\ufffd"])
    hangul = sum(1 for ch in text if "\uac00" <= ch <= "\ud7af")
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    latin = sum(1 for ch in text if ("a" <= ch.lower() <= "z"))
    if bad_markers:
        return True
    if hangul > max(8, cjk * 2):
        return True
    if cjk < 8 and latin > 30:
        return True
    return len(text.strip()) < 12


def load_brief(brief=None, brief_file=""):
    if brief_file:
        path = Path(brief_file).expanduser().resolve()
        if path.exists():
            brief = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(brief, dict):
        brief = {}
    merged = {**DEFAULT_BRIEF, **{k: v for k, v in brief.items() if v not in (None, "", [])}}
    for key in ["selling_points", "constraints"]:
        value = merged.get(key)
        if isinstance(value, str):
            merged[key] = [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]
        elif not isinstance(value, list):
            merged[key] = []
    try:
        merged["target_duration_sec"] = min(180, max(30, int(float(merged.get("target_duration_sec") or 75))))
    except (TypeError, ValueError):
        merged["target_duration_sec"] = 75
    try:
        merged["imitation_shot_count"] = min(30, max(3, int(float(merged.get("imitation_shot_count") or 8))))
    except (TypeError, ValueError):
        merged["imitation_shot_count"] = 8
    return merged


def _as_positive_float(value, default=0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _source_storyboard_count(payload):
    analysis_storyboard = ((payload.get("analysis") or {}).get("storyboard") or [])
    if isinstance(analysis_storyboard, list) and analysis_storyboard:
        return len(analysis_storyboard)
    selected_storyboard = ((payload.get("selected_frames") or {}).get("storyboard") or [])
    if isinstance(selected_storyboard, list):
        return len(selected_storyboard)
    return 0


def _source_duration_sec(payload):
    duration = _as_positive_float(payload.get("video_duration_sec"))
    if duration:
        return duration
    timestamps = [
        _as_positive_float(item.get("timestamp_sec"))
        for item in (payload.get("frame_metadata") or [])
        if isinstance(item, dict)
    ]
    return max(timestamps) if timestamps else 0


def recommend_source_storyboard_count(source_duration_sec=0, selected_storyboard_count=0):
    source_duration = _as_positive_float(source_duration_sec)
    selected_count = max(0, int(selected_storyboard_count or 0))
    if not source_duration:
        return min(selected_count or 18, 24)

    duration_based = round(source_duration / 8)
    min_by_duration = max(6, round(source_duration / 14))
    max_by_duration = min(36, max(min_by_duration, round(source_duration / 6)))
    recommended = min(max_by_duration, max(min_by_duration, duration_based))
    if selected_count:
        recommended = min(recommended, selected_count)
    return max(3, recommended)


def recommend_imitation_shot_count(brief=None, source_storyboard_count=0, source_duration_sec=0):
    brief = load_brief(brief or {})
    target_duration = brief.get("target_duration_sec", 75)
    source_count = max(0, int(source_storyboard_count or 0))
    source_duration = _as_positive_float(source_duration_sec)

    duration_based = round(target_duration / 8)
    if source_count and source_duration:
        source_avg = source_duration / source_count
        adapted_avg = min(12, max(5, source_avg * 1.4))
        source_based = round(target_duration / adapted_avg)
    elif source_count:
        source_based = source_count
    else:
        source_based = duration_based

    min_by_duration = max(3, round(target_duration / 12))
    max_by_duration = min(30, max(min_by_duration, round(target_duration / 5)))
    recommended = round((duration_based + source_based) / 2)
    return min(max_by_duration, max(min_by_duration, recommended))


def apply_recommended_imitation_shot_count(brief=None, source_storyboard_count=0, source_duration_sec=0):
    merged = load_brief(brief or {})
    recommended = recommend_imitation_shot_count(merged, source_storyboard_count, source_duration_sec)
    merged["imitation_shot_count"] = recommended
    merged["imitation_shot_count_mode"] = "auto"
    merged["imitation_shot_count_basis"] = {
        "target_duration_sec": merged.get("target_duration_sec", 75),
        "source_storyboard_count": int(source_storyboard_count or 0),
        "source_duration_sec": round(_as_positive_float(source_duration_sec), 2),
    }
    return merged


def brief_to_text(brief):
    brief = load_brief(brief or {})
    shot_count = brief.get("imitation_shot_count", 8)
    duration = brief.get("target_duration_sec", 75)
    target_text = f"仿写目标时长：{duration}秒；仿写镜头数量：{shot_count}个（系统根据目标时长和原视频可借鉴镜头自动推荐）。"
    if not has_brief(brief):
        return target_text + "用户未填写商单 brief；请先基于原视频拆解出可迁移风格，不要编造具体品牌、产品或功效。"
    return (
        f"品牌：{brief.get('brand', '')}；"
        f"产品：{brief.get('product', '')}；"
        f"主要卖点：{'、'.join(brief.get('selling_points') or [])}；"
        f"目标人群：{brief.get('audience', '')}；"
        f"投放平台：{brief.get('platform', '')}；"
        f"限制/合规要求：{'、'.join(brief.get('constraints') or [])}；"
        f"{target_text}"
    )


def has_brief(brief):
    return any([
        brief.get("brand"),
        brief.get("product"),
        brief.get("selling_points"),
        brief.get("audience"),
        brief.get("constraints"),
    ])


def build_analysis_prompt(brief=None):
    brief = load_brief(brief or {})
    shot_count = brief.get("imitation_shot_count", 8)
    duration = brief.get("target_duration_sec", 75)
    avg_duration = max(1, round(duration / max(shot_count, 1)))
    return (
        "你是短视频内容拆解分析师。请基于视频画面、候选帧、口播转写和屏幕字幕 OCR，输出结构化 JSON。"
        "如果提供了独立封面图，cover_title 必须优先分析该封面图；不要把页面截图、合集截图或普通抽帧误当成封面。"
        "输出必须包含 cover_title, topic, product, copy, storyboard, style_imitation, risk, summary。"
        "summary 是对象，包含 title, desc, key_points。"
        "cover_title 是对象，包含 title, desc, format, visual_highlights, cover_text, hook_tag, hook_analysis, original_title, title_formula, checks。"
        "checks 是数组，每项包含 label, pass, desc，用于检查具体人群、具体痛点/好奇、看完收益。"
        "topic 是对象，包含 title, desc, target_audience, pain_point, angle, tags, migration_references。"
        "migration_references 是数组，每项包含 title, audience, logic。"
        "product 是对象，包含 title, desc, items, logic。items 是数组，每项包含 name, role, tag, selling_point。"
        "copy 是对象，包含 title, desc, opening, structure, golden_sentences, ending。"
        "opening 包含 tags, line, analysis；structure 是数组，每项包含 time, content；golden_sentences 是数组，每项包含 line, analysis；ending 包含 line, analysis, suggestion。"
        "style_imitation 是对象，用于在不照搬原文的前提下做内容风格模仿，包含 title, desc, style_profile, imitation_script, imitation_storyboard, compliance_notes。"
        "style_profile 包含 title_formula, hook_pattern, scene_rhythm, tone_rules, visual_rules, reusable_structure, do_not_copy。"
        "imitation_script 是对象，基于用户提供的商单 brief 仿写。"
        "imitation_script 包含 title_options, hook, voiceover, product_insertions, cta。voiceover 是数组，按口播段落输出。"
        f"imitation_storyboard 是新商单拍摄脚本数组，必须返回 {shot_count} 个镜头对象，每个包含 shot, visual, voice_or_subtitle, props_scene, duration_sec, style_reference。"
        f"imitation_storyboard 总时长控制在 {duration} 秒左右，单镜头 duration_sec 可围绕 {avg_duration} 秒上下浮动；不要写 1-3 秒的原视频碎片时长。"
        "style_imitation 必须说明“模仿的是结构/节奏/语气/场景，不复制原句或镜头”。"
        "risk 是对象，包含 title, desc, checks。"
        "storyboard 是原视频拆解数组，数量由原视频内容决定；请只输出具有代表性的镜头，不要把同一场景连续 1-3 秒的小动作都拆成独立镜头。"
        "storyboard 必须以我提供的候选关键帧为锚点生成：每个分镜对应一张候选关键帧，visual 必须描述这张 evidence_frame 实际可见的画面。"
        "如果完整视频里出现了某个细节，但没有候选关键帧能证明它，不要把它单独写成 storyboard 分镜；可以在相邻分镜的 style_reference 里概括。"
        "当相邻片段属于同一场景、同一动作延续或同一叙事目的时，请合并成一个代表性分镜；每个分镜应能指导拍摄或仿写。"
        "storyboard 每个对象包含 shot, time_range, visual, voice_or_subtitle, props_scene, duration_sec, evidence_frame, shot_size, scene, action, audio, composition, style_reference, role, sort_time_sec, repeat_of, is_repeated_later。"
        "time_range 写该镜头的大致时间段，例如 0:03-0:08；shot_size 写景别，例如特写/近景/中景/全景；scene 写场景类型，例如室内·办公室/户外·街道。"
        "audio 写可观察或可从转写判断的声音信息，例如人声/BGM/环境声/音效；composition 写画面构图重点；style_reference 写这个镜头对仿写拍摄最值得借鉴的结构、节奏、字幕、情绪或画面方法。"
        "如果视频开头 0-5 秒快速闪过后面会正式出现的镜头，请把这些对象的 role 写为 hook_preview，is_repeated_later 写 true，repeat_of 写后文正式镜头的时间段或编号；主线正式镜头 role 写 main。"
        "sort_time_sec 用于主线排序：hook_preview 写它实际出现时间，main 写主线正式出现时间；不要把开头快闪当作主线叙事顺序。"
        "storyboard.evidence_frame 必须从我提供的候选关键帧路径中选择，格式类似 /results/<id>/frames/frame_01.jpg 或 /references/frames/frame_01.jpg。"
        "每个 storyboard 的 time_range、visual 和 evidence_frame 必须互相一致；不要把视频中另一个时间点的画面描述绑定到当前帧。"
        "如果某个分镜没有完全对应的候选关键帧，请不要输出这个分镜；不要为了补齐而绑定不相关图片。"
        "storyboard 的 visual 必须描述 evidence_frame 或完整视频中同一时间段实际可见的内容，不能把另一帧画面和当前文字混在一起。"
        "如果提供了完整视频，请优先参考视频里的连续动作、节奏、转场和场景推进；口播转写用于校准真实文案；OCR 用于校准屏幕字幕。"
        "不要编造播放量、完播率、过审率、转化率等无法从视频直接证明的精确数据；metrics 只能写可观察事实或明确标注为估计。"
        "风险分析不要使用 100% 过审、无风险、必爆等绝对化表述。"
        "请只输出 JSON。"
    )


def video_data_url(path):
    raw = Path(path).read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:video/mp4;base64,{encoded}"


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


def call_chat_completion(provider, model, content, timeout=240):
    base_url, api_key, selected_model = read_provider_config(provider, model)
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
        with request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {base_url}: {body}") from exc

    return parse_model_json(result["choices"][0]["message"]["content"])


def build_style_imitation_prompt(brief):
    brief = load_brief(brief or {})
    shot_count = brief.get("imitation_shot_count", 8)
    duration = brief.get("target_duration_sec", 75)
    avg_duration = max(1, round(duration / max(shot_count, 1)))
    return (
        "你是 MCN 商单内容策略师。请基于已经完成的视频拆解 JSON，新增 content style imitation。"
        "目标不是复制原文，而是把原视频的标题公式、开头钩子、镜头节奏、语气和场景结构迁移到新商单。"
        f"新商单 brief：{brief_to_text(brief)}"
        "只输出 JSON 对象，字段为 title, desc, style_profile, imitation_script, imitation_storyboard, compliance_notes。"
        "style_profile 包含 title_formula, hook_pattern, scene_rhythm, tone_rules, visual_rules, reusable_structure, do_not_copy。"
        "imitation_script 包含 title_options, hook, voiceover, product_insertions, cta。voiceover 是数组。"
        f"imitation_storyboard 必须返回 {shot_count} 个镜头对象，每个包含 shot, visual, voice_or_subtitle, props_scene, duration_sec, style_reference。"
        f"这是新商单拍摄分镜，总时长控制在 {duration} 秒左右，单镜头 duration_sec 可围绕 {avg_duration} 秒上下浮动；不要沿用原视频 1-3 秒碎片时长。"
        "desc 必须说明：模仿的是结构/节奏/语气/场景，不复制原句或镜头。"
    )


def build_style_source_payload(payload):
    analysis = payload.get("analysis") or {}
    return {
        "source_url": payload.get("source_url", ""),
        "cover_title": analysis.get("cover_title", {}),
        "topic": analysis.get("topic", {}),
        "product": analysis.get("product", {}),
        "copy": analysis.get("copy", {}),
        "storyboard": analysis.get("storyboard", []),
        "risk": analysis.get("risk", {}),
        "summary": analysis.get("summary", {}),
        "transcript": (((payload.get("audio") or {}).get("transcript") or {}).get("text") or "")[:2000],
        "ocr": [
            {"frame": item.get("frame", ""), "text": item.get("text", "")}
            for item in (payload.get("ocr") or [])
            if item.get("text")
        ][:40],
    }


def enforce_imitation_shot_count(analysis, brief):
    if not isinstance(analysis, dict):
        return analysis
    style = analysis.get("style_imitation")
    if not isinstance(style, dict):
        return analysis
    storyboard = style.get("imitation_storyboard")
    if not isinstance(storyboard, list):
        return analysis
    shot_count = load_brief(brief or {}).get("imitation_shot_count", 8)
    if len(storyboard) > shot_count:
        style["imitation_storyboard"] = storyboard[:shot_count]
    for index, shot in enumerate(style.get("imitation_storyboard") or [], start=1):
        if isinstance(shot, dict):
            shot["shot"] = index
    return analysis


def parse_time_to_sec(value):
    raw = str(value or "").strip()
    if not raw:
        return 0
    match = None
    for token in raw.replace("~", "-").split("-"):
        token = token.strip()
        if token:
            match = token
            break
    if not match:
        return 0
    if ":" in match:
        parts = match.split(":")
        try:
            numbers = [float(part) for part in parts]
        except ValueError:
            return 0
        if len(numbers) == 2:
            return numbers[0] * 60 + numbers[1]
        if len(numbers) == 3:
            return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    try:
        return float(match.rstrip("s秒"))
    except ValueError:
        return 0


def storyboard_sort_time(shot):
    if not isinstance(shot, dict):
        return 0
    from_range = parse_time_to_sec(shot.get("time_range") or shot.get("time") or shot.get("timestamp"))
    if from_range:
        return from_range
    sort_time = _as_positive_float(shot.get("sort_time_sec"))
    if sort_time:
        return sort_time
    return _as_positive_float(shot.get("timestamp_sec"))


def storyboard_duration(shot):
    if not isinstance(shot, dict):
        return 0
    duration = _as_positive_float(shot.get("duration_sec") or shot.get("duration"))
    if duration:
        return duration
    raw = str(shot.get("time_range") or "")
    if "-" not in raw:
        return 0
    start, end = raw.replace("~", "-").split("-", 1)
    return max(0, parse_time_to_sec(end) - parse_time_to_sec(start))


def normalize_source_storyboard(analysis, manifest_data):
    if not isinstance(analysis, dict):
        return analysis
    storyboard = analysis.get("storyboard")
    if not isinstance(storyboard, list) or not storyboard:
        return analysis

    frame_metadata = [
        item for item in manifest_data.get("frame_metadata", [])
        if isinstance(item, dict) and item.get("frame")
    ]
    selected_storyboard = (manifest_data.get("selected_frames") or {}).get("storyboard", []) or []
    selected_set = set(selected_storyboard)
    if selected_set:
        frame_metadata = [item for item in frame_metadata if item.get("frame") in selected_set] or frame_metadata

    def nearest_frame(time_sec):
        if not frame_metadata:
            return ""
        return min(
            frame_metadata,
            key=lambda item: abs(_as_positive_float(item.get("timestamp_sec")) - time_sec),
        ).get("frame", "")

    normalized = []
    for shot in storyboard:
        if not isinstance(shot, dict):
            continue
        next_shot = dict(shot)
        time_sec = storyboard_sort_time(next_shot)
        next_shot["sort_time_sec"] = round(time_sec, 2)
        evidence_frame = next_shot.get("evidence_frame")
        if not evidence_frame:
            fallback_frame = nearest_frame(time_sec)
            if fallback_frame:
                next_shot["evidence_frame"] = fallback_frame
        normalized.append(next_shot)

    target_count = recommend_source_storyboard_count(
        manifest_data.get("video_duration_sec"),
        len(selected_storyboard),
    )
    if len(normalized) > target_count:
        with_images = [shot for shot in normalized if shot.get("evidence_frame")]
        pool = with_images if len(with_images) >= target_count else normalized
        if target_count <= 1:
            normalized = pool[:target_count]
        else:
            ordered = sorted(pool, key=storyboard_sort_time)
            picked = []
            used = set()
            for index in range(target_count):
                source_index = round(index * (len(ordered) - 1) / (target_count - 1))
                while source_index in used and source_index + 1 < len(ordered):
                    source_index += 1
                used.add(source_index)
                picked.append(ordered[source_index])
            normalized = sorted(picked, key=storyboard_sort_time)

    normalized = sorted(normalized, key=storyboard_sort_time)
    for index, shot in enumerate(normalized, start=1):
        shot["shot"] = index
    analysis["storyboard"] = normalized
    return analysis


def ai_style_imitation(payload, provider, model, brief=None):
    brief = apply_recommended_imitation_shot_count(
        brief or payload.get("brief") or {},
        _source_storyboard_count(payload),
        _source_duration_sec(payload),
    )
    payload["brief"] = brief
    content = [
        {"type": "text", "text": build_style_imitation_prompt(brief)},
        {
            "type": "text",
            "text": "下面是原视频拆解结果，请从中抽象风格，再迁移到用户提供的商单 brief：\n"
            + json.dumps(build_style_source_payload(payload), ensure_ascii=False),
        },
    ]
    return enforce_imitation_shot_count({"style_imitation": call_chat_completion(provider, model, content, timeout=180)}, brief)["style_imitation"]


def add_text_evidence(content, manifest):
    brief = load_brief(manifest.get("brief") or {})
    content.append({
        "type": "text",
        "text": "本次仿写目标商单 brief 如下，请 style_imitation 必须基于该 brief 输出：\n" + brief_to_text(brief),
    })

    cover_image = manifest.get("cover_image")
    if cover_image:
        content.append({
            "type": "text",
            "text": f"独立封面图如下，这是平台笔记/视频的真实封面素材，封面&标题分析优先基于它：{cover_image}",
        })
        content.append({"type": "image_url", "image_url": {"url": image_data_url(frame_path(cover_image))}})

    frame_metadata = manifest.get("frame_metadata") or []
    if frame_metadata:
        content.append({
            "type": "text",
            "text": "候选帧时间信息如下。做 storyboard 时，只有明确引用某张候选帧，才把它的 frame 写入 evidence_frame：\n"
            + json.dumps(frame_metadata, ensure_ascii=False),
        })

    transcript = (manifest.get("audio") or {}).get("transcript") or {}
    if looks_bad_transcript(transcript.get("text", "")):
        transcript = {}
    ocr_items = manifest.get("ocr") or []

    if transcript.get("text"):
        content.append({
            "type": "text",
            "text": "本地 Whisper 口播转写如下，请用它校准真实文案、节奏和 CTA：\n" + transcript["text"],
        })
    if transcript.get("segments"):
        transcript_lines = [
            f"{seg.get('start', 0):.2f}-{seg.get('end', 0):.2f}s: {seg.get('text', '')}"
            for seg in transcript["segments"]
            if seg.get("text")
        ]
        if transcript_lines:
            content.append({
                "type": "text",
                "text": "口播分段如下，请在分镜分析里参考对应时间：\n" + "\n".join(transcript_lines),
            })

    ocr_lines = []
    for item in ocr_items:
        text = (item.get("text") or "").strip()
        if text:
            ocr_lines.append(f"{item.get('frame', '')}: {text}")
    if ocr_lines:
        content.append({
            "type": "text",
            "text": "屏幕字幕/OCR 文本如下，请结合画面与口播一起分析：\n" + "\n".join(ocr_lines[:120]),
        })


def ai_analyze_video(manifest, provider, model, ai_video_path):
    content = [{"type": "text", "text": build_analysis_prompt(manifest.get("brief") or {})}]
    content.append({
        "type": "text",
        "text": "下面是压缩后的完整视频，请直接分析视频里的连续动作、镜头节奏、转场和场景推进。",
    })
    content.append({"type": "video_url", "video_url": {"url": video_data_url(ai_video_path)}})
    add_text_evidence(content, manifest)
    selected = manifest.get("selected_frames", {})
    storyboard_urls = selected.get("storyboard") or manifest.get("frames", [])
    if storyboard_urls:
        content.append({
            "type": "text",
            "text": "下面是候选关键帧。storyboard 必须以这些图片为证据锚点：每个分镜只能引用其中一张 evidence_frame，且 visual 必须描述该图片实际可见内容。",
        })
        for idx, url in enumerate(storyboard_urls, start=1):
            content.append({"type": "text", "text": f"候选关键帧 {idx}: {url}。如果在 storyboard 中引用这张图，evidence_frame 必须原样填写这个路径。"})
            content.append({"type": "image_url", "image_url": {"url": image_data_url(frame_path(url))}})
    return call_chat_completion(provider, model, content, timeout=300)


def ai_analyze(manifest, provider, model):
    selected = manifest.get("selected_frames", {})
    categories = selected.get("categories", {})
    storyboard_urls = selected.get("storyboard") or manifest.get("frames", [])
    cover_url = categories.get("cover_title", {}).get("frame") or manifest.get("frames", [None])[0]
    topic_url = categories.get("topic", {}).get("frame") or manifest.get("frames", [None, None])[1]
    product_url = categories.get("product", {}).get("frame") or manifest.get("frames", [None, None, None])[2]
    copy_url = categories.get("copy", {}).get("frame") or manifest.get("frames", [None, None, None, None])[3]
    selected_urls = [u for u in [cover_url, topic_url, product_url, copy_url] if u]
    selected_urls.extend([u for u in storyboard_urls if u])
    seen = []
    frames = []
    for url in selected_urls:
        if url not in seen:
            seen.append(url)
            frames.append(url)

    content = [{"type": "text", "text": build_analysis_prompt(manifest.get("brief") or {})}]
    add_text_evidence(content, manifest)
    for idx, url in enumerate(frames, start=1):
        content.append({"type": "text", "text": f"候选关键帧 {idx}: {url}。如果在 storyboard 中引用这张图，evidence_frame 必须原样填写这个路径。"})
        content.append({"type": "image_url", "image_url": {"url": image_data_url(frame_path(url))}})
    return call_chat_completion(provider, model, content, timeout=180)


def fallback_analysis(reason, frames, manifest_data):
    transcript_text = ((manifest_data.get("audio") or {}).get("transcript") or {}).get("text", "")
    ocr_texts = [item.get("text", "") for item in manifest_data.get("ocr", []) if item.get("text")]
    storyboard_frames = (manifest_data.get("selected_frames") or {}).get("storyboard", [])
    reason_text = str(reason)
    if "HTTP 429" in reason_text:
        status = "rate_limited"
        desc = "模型接口触发限流，当前结果先保留抽帧、口播转写和 OCR 数据，稍后可重试生成完整分析。"
    else:
        status = "ai_failed"
        desc = "模型接口暂时不可用，当前结果先保留抽帧、口播转写和 OCR 数据，稍后可重试生成完整分析。"

    return {
        "summary": {
            "title": "AI 分析暂未完成",
            "desc": desc,
            "metrics": {
                "frames": len(frames),
                "transcript_chars": len(transcript_text),
                "ocr_frames": len(ocr_texts),
            },
        },
        "cover_title": {"title": "待分析", "desc": "已保留封面和关键帧，等待 AI 重试。", "metrics": []},
        "topic": {"title": "待分析", "desc": "已保留视频证据，等待 AI 重试。", "metrics": []},
        "product": {"title": "待分析", "desc": "已保留产品/画面证据，等待 AI 重试。", "metrics": []},
        "copy": {"title": "待分析", "desc": transcript_text[:500], "metrics": []},
        "style_imitation": {
            "title": "待仿写",
            "desc": "AI 分析暂未完成，尚未生成内容风格模仿。",
            "style_profile": {},
            "imitation_script": {},
            "imitation_storyboard": [],
            "compliance_notes": [],
        },
        "risk": {
            "title": "待质检",
            "desc": "脚本尚未生成，需在 AI 重试成功后进行风险质检。",
            "metrics": [],
        },
        "storyboard": [
            {
                "shot": idx + 1,
                "visual": f"关键帧 {idx + 1}",
                "voice_or_subtitle": "",
                "props_scene": "",
                "duration_sec": "",
                "evidence_frame": frame,
            }
            for idx, frame in enumerate(storyboard_frames)
        ],
        "status": status,
        "error_message": reason_text[-1200:],
    }


def analyze(
    provider,
    model,
    strategy,
    max_frames,
    interval,
    url=None,
    video=None,
    transcribe=False,
    ocr=False,
    whisper_model="small",
    whisper_language="",
    ocr_lang="chi_sim+eng",
    frame_select_mode="heuristic",
    analysis_mode="video",
    brief=None,
    brief_file="",
    task_id="",
):
    brief = load_brief(brief, brief_file)
    task_id = task_id or uuid.uuid4().hex[:12]
    task_dir = RESULTS_DIR / task_id
    ensure_dir(task_dir)

    if url:
        video_path = download_with_videodl(url, "VideoFKVideoClient")
        source_url = url
    elif video:
        video_path = Path(video).expanduser().resolve()
        if not video_path.exists():
            raise SystemExit(f"Video file does not exist: {video_path}")
        source_url = ""
    else:
        raise SystemExit("URL or video is required.")
    cover_path = find_cover_for_video(video_path)

    frame_metadata = []
    if strategy == "interval":
        frames = extract_frames(video_path, interval, max_frames)
        frame_metadata = [
            {
                "frame": f"/references/frames/{frame.name}",
                "timestamp_sec": round(index * interval, 2),
            }
            for index, frame in enumerate(frames)
        ]
    else:
        duration = probe_duration(video_path)
        timestamps = select_timestamps(duration, max_frames)
        frames = extract_at_timestamps(video_path, timestamps)
        frame_metadata = [
            {
                "frame": f"/references/frames/{frame.name}",
                "timestamp_sec": round(timestamps[index], 2) if index < len(timestamps) else None,
            }
            for index, frame in enumerate(frames)
        ]

    source_duration_sec = duration if strategy != "interval" else probe_duration(video_path)

    audio_path = None
    transcript = None
    if transcribe:
        audio_path = extract_audio_track(video_path)
        transcript = transcribe_audio_track(audio_path, model_size=whisper_model, language=whisper_language)

    ocr_items = None
    if ocr:
        ocr_items = extract_ocr_for_frames(frames, lang=ocr_lang)

    ai_video_path = None
    if analysis_mode == "video":
        ai_video_path = compress_video_for_ai(video_path)

    contact_sheet = make_contact_sheet(frames)
    manifest = write_manifest(source_url, video_path, frames, audio_path=audio_path, transcript=transcript, ocr=ocr_items, cover_path=cover_path)

    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data["brief"] = brief
    manifest_data["video_duration_sec"] = source_duration_sec
    manifest_data["frame_metadata"] = frame_metadata
    manifest.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")
    if frame_select_mode == "heuristic" or provider == "heuristic":
        selection, rationale, _ = heuristic_select(manifest_data)
        manifest_data["selected_frames"] = {
            "mode": "heuristic",
            "categories": {
                key: {"label": key, "frame": value, "rationale": rationale_text(rationale, key)}
                for key, value in selection.items()
                if key != "storyboard"
            },
            "storyboard": selection["storyboard"],
        }
        manifest.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        try:
            selection, rationale, _ = ai_select(manifest_data, provider, model)
            selection_mode = "ai"
        except Exception as exc:
            selection, rationale, _ = heuristic_select(manifest_data)
            selection_mode = "heuristic_after_ai_failed"
        manifest_data["selected_frames"] = {
            "mode": selection_mode,
            "categories": {
                key: {"label": key, "frame": value, "rationale": rationale_text(rationale, key)}
                for key, value in selection.items()
                if key != "storyboard"
            },
            "storyboard": selection["storyboard"],
        }
        manifest.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")

    brief = apply_recommended_imitation_shot_count(
        brief,
        len((manifest_data.get("selected_frames") or {}).get("storyboard", []) or []),
        source_duration_sec,
    )
    manifest_data["brief"] = brief
    manifest.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        if analysis_mode == "video" and ai_video_path:
            analysis = ai_analyze_video(manifest_data, provider, model, ai_video_path)
        else:
            analysis = ai_analyze(manifest_data, provider, model)
    except Exception as exc:
        analysis = fallback_analysis(exc, frames, manifest_data)
    analysis = normalize_source_storyboard(analysis, manifest_data)
    analysis = enforce_imitation_shot_count(analysis, brief)

    task_frames_dir = task_dir / "frames"
    ensure_dir(task_frames_dir)
    for frame in frames:
        shutil.copy2(frame, task_frames_dir / frame.name)

    task_contact_sheet = ""
    if contact_sheet and Path(contact_sheet).exists():
        copied = task_dir / "contact_sheet.jpg"
        shutil.copy2(contact_sheet, copied)
        task_contact_sheet = f"/results/{task_id}/contact_sheet.jpg"

    task_cover_image = ""
    reference_cover = frame_path(manifest_data.get("cover_image", "")) if manifest_data.get("cover_image") else None
    if reference_cover and Path(reference_cover).exists():
        copied = task_dir / "cover.jpg"
        shutil.copy2(reference_cover, copied)
        task_cover_image = f"/results/{task_id}/cover.jpg"

    if audio_path and Path(audio_path).exists():
        shutil.copy2(audio_path, task_dir / audio_path.name)

    task_ai_video = ""
    if ai_video_path and Path(ai_video_path).exists():
        shutil.copy2(ai_video_path, task_dir / Path(ai_video_path).name)
        task_ai_video = f"/results/{task_id}/{Path(ai_video_path).name}"

    result_frame_urls = {f"/references/frames/{p.name}": f"/results/{task_id}/frames/{p.name}" for p in frames}

    def result_frame_url(url):
        return result_frame_urls.get(url, url)

    for shot in analysis.get("storyboard", []) if isinstance(analysis, dict) else []:
        if not isinstance(shot, dict):
            continue
        for key in ("evidence_frame", "frame", "image"):
            if shot.get(key):
                shot[key] = result_frame_url(shot[key])

    selected_frames = manifest_data.get("selected_frames", {})
    if selected_frames:
        selected_frames = json.loads(json.dumps(selected_frames, ensure_ascii=False))
        for category in (selected_frames.get("categories") or {}).values():
            if category.get("frame"):
                category["frame"] = result_frame_url(category["frame"])
        selected_frames["storyboard"] = [
            result_frame_url(url)
            for url in selected_frames.get("storyboard", [])
        ]

    ocr_payload = []
    for item in manifest_data.get("ocr", []):
        next_item = dict(item)
        if next_item.get("frame"):
            next_item["frame"] = result_frame_url(next_item["frame"])
        ocr_payload.append(next_item)

    analysis_payload = {
        "id": task_id,
        "source_url": url,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "video_path": str(video_path),
        "video_duration_sec": source_duration_sec,
        "audio": manifest_data.get("audio", {}),
            "ai_video": {
            "mode": analysis_mode,
            "path": task_ai_video,
        },
        "brief": brief,
        "frame_metadata": [
            {
                **item,
                "frame": result_frame_url(item.get("frame", "")),
            }
            for item in frame_metadata
        ],
        "ocr": ocr_payload,
        "frames": [f"/results/{task_id}/frames/{p.name}" for p in frames],
        "cover_image": task_cover_image,
        "cover_source": manifest_data.get("cover_source", "frame_fallback"),
        "contact_sheet": task_contact_sheet,
        "selected_frames": selected_frames,
        "analysis": analysis,
    }
    (task_dir / "analysis.json").write_text(json.dumps(analysis_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return analysis_payload


def main():
    parser = argparse.ArgumentParser(description="Analyze a video link into a structured report.")
    parser.add_argument("--url", help="Video page URL.")
    parser.add_argument("--video", help="Local video file path.")
    parser.add_argument("--provider", choices=["ark", "openai", "custom"], default="ark")
    parser.add_argument("--model", default="")
    parser.add_argument("--strategy", choices=["smart", "interval"], default="smart")
    parser.add_argument("--max-frames", type=int, default=36)
    parser.add_argument("--interval", type=int, default=3)
    parser.add_argument("--transcribe", action="store_true")
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--ocr-lang", default="chi_sim+eng")
    parser.add_argument("--whisper-model", default="small")
    parser.add_argument("--whisper-language", default="")
    parser.add_argument("--frame-select-mode", choices=["heuristic", "ai"], default="heuristic")
    parser.add_argument("--analysis-mode", choices=["video", "frames"], default="video")
    parser.add_argument("--brief-file", default="", help="JSON file containing brand/product brief for style imitation.")
    parser.add_argument("--task-id", default="", help="Optional preassigned result id.")
    args = parser.parse_args()

    if args.provider == "ark" and not (ROOT / "豆包信息.txt").exists():
        raise SystemExit("Missing 豆包信息.txt")

    payload = analyze(
        provider=args.provider,
        model=args.model,
        strategy=args.strategy,
        max_frames=args.max_frames,
        interval=args.interval,
        url=args.url,
        video=args.video,
        transcribe=args.transcribe,
        ocr=args.ocr,
        whisper_model=args.whisper_model,
        whisper_language=args.whisper_language,
        ocr_lang=args.ocr_lang,
        frame_select_mode=args.frame_select_mode,
        analysis_mode=args.analysis_mode,
        brief_file=args.brief_file,
        task_id=args.task_id,
    )
    print(json.dumps({
        "id": payload["id"],
        "result_url": f"http://127.0.0.1:4173/analyze/{payload['id']}",
        "api_result_url": f"http://127.0.0.1:4173/api/result/{payload['id']}",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
