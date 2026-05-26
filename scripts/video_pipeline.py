import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
VIDEOS_DIR = ROOT / "data" / "videos"
FRAMES_DIR = ROOT / "references" / "frames"
LOCAL_FFMPEG = ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def require_tool(name, install_hint):
    if shutil.which(name):
        return name
    if name == "ffmpeg" and LOCAL_FFMPEG.exists():
        return str(LOCAL_FFMPEG)
    raise SystemExit(f"Missing `{name}`. {install_hint}")


def newest_video_file(folder):
    candidates = [
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _cover_name_score(path):
    name = path.stem.lower()
    if name in {"cover", "thumbnail", "thumb", "poster"} or "封面" in path.stem:
        return 4
    if any(token in name for token in ["cover", "thumbnail", "thumb", "poster"]):
        return 3
    if name.startswith("image") or name.startswith("img"):
        return 2
    return 1


def validate_image(path):
    try:
        with Image.open(path) as img:
            width, height = img.size
        return width >= 80 and height >= 80
    except Exception:
        return False


def find_cover_file(folder, video_path=None):
    folder = Path(folder)
    ignored_names = {"contact_sheet"}
    candidates = [
        p for p in folder.rglob("*")
        if p.is_file()
        and p.suffix.lower() in IMAGE_EXTS
        and p.stem.lower() not in ignored_names
        and not p.name.lower().startswith("frame_")
        and validate_image(p)
    ]
    if not candidates:
        return None

    video_stem = Path(video_path).stem.lower() if video_path else ""

    def score(path):
        stem = path.stem.lower()
        same_title = 2 if video_stem and (stem == video_stem or stem.startswith(video_stem[:20])) else 0
        return (_cover_name_score(path), same_title, path.stat().st_mtime)

    return max(candidates, key=score)


def find_cover_for_video(video_path):
    video_path = Path(video_path)
    search_roots = [video_path.parent, *list(video_path.parents[1:4])]
    seen = set()
    for root in search_roots:
        if root in seen or not root.exists():
            continue
        seen.add(root)
        if cover := find_cover_file(root, video_path):
            return cover
    return None


def _first_cover_url(value):
    if not value:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        for item in value:
            if url := _first_cover_url(item):
                return url
    if isinstance(value, dict):
        for key in ["url", "src", "href", "cover", "cover_url", "thumbnail", "preview_url"]:
            if url := _first_cover_url(value.get(key)):
                return url
        for item in value.values():
            if url := _first_cover_url(item):
                return url
    return ""


def parse_cover_url_with_videodl(url, client, work_dir):
    try:
        from videodl.videodl import VideoClient
    except Exception:
        return ""

    urls = [url]
    if str(url).startswith("http://"):
        urls.append("https://" + str(url)[len("http://"):])

    for candidate_url in urls:
        try:
            vc = VideoClient(
                allowed_video_sources=[client],
                init_video_clients_cfg={client: {"work_dir": str(work_dir / "videodl_outputs")}},
                apply_common_video_clients_only=True,
            )
            infos = vc.parsefromurl(candidate_url)
        except Exception:
            continue

        for info in infos or []:
            cover_url = _first_cover_url(info.get("cover_url") if hasattr(info, "get") else getattr(info, "cover_url", ""))
            if cover_url:
                return urljoin(candidate_url, cover_url)
    return ""


def download_cover_image(cover_url, output_path):
    if not cover_url:
        return None
    req = Request(
        cover_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.xiaohongshu.com/",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        },
    )
    try:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with urlopen(req, timeout=25) as resp:
            data = resp.read()
            content_type = (resp.headers.get("Content-Type") or "").lower()
        suffix = ".webp" if "webp" in content_type else ".png" if "png" in content_type else output_path.suffix
        output_path = output_path.with_suffix(suffix or ".jpg")
        tmp_path = output_path.with_suffix(".download")
        tmp_path.write_bytes(data)
        with Image.open(tmp_path) as img:
            if img.width < 80 or img.height < 80:
                tmp_path.unlink(missing_ok=True)
                return None
            img.verify()
        tmp_path.replace(output_path)
        return output_path
    except Exception:
        return None


def ensure_reference_cover(cover_path):
    for old in FRAMES_DIR.glob("cover.*"):
        old.unlink()
    if not cover_path or not Path(cover_path).exists():
        return None
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    out = FRAMES_DIR / "cover.jpg"
    try:
        with Image.open(cover_path) as img:
            img.convert("RGB").save(out, quality=94)
    except Exception:
        shutil.copy2(cover_path, out)
    return out


def download_with_videodl(url, client):
    require_tool(
        "videodl",
        "Install it with: pip install videofetch",
    )
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    run_dir = VIDEOS_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    cmd = ["videodl", "-i", url, "-g", "-a", client]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, cwd=run_dir, check=True)

    video = newest_video_file(run_dir)
    if not video:
        raise SystemExit(f"videodl finished, but no video file was found in {run_dir}")

    if not find_cover_for_video(video):
        cover_url = parse_cover_url_with_videodl(url, client, run_dir)
        if cover_url:
            download_cover_image(cover_url, video.parent / "cover.jpg")
    return video


def extract_frames(video_path, interval, max_frames):
    ffmpeg = require_tool(
        "ffmpeg",
        "Install FFmpeg and make sure `ffmpeg` is on PATH.",
    )
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    for old in FRAMES_DIR.glob("frame_*.jpg"):
        old.unlink()

    pattern = FRAMES_DIR / "frame_%02d.jpg"
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"fps=1/{interval},scale=720:-1",
        "-frames:v",
        str(max_frames),
        str(pattern),
    ]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)

    frames = sorted(FRAMES_DIR.glob("frame_*.jpg"))
    if not frames:
        raise SystemExit("No frames were extracted. Check the video path and ffmpeg output.")
    return frames


def probe_duration(video_path):
    ffprobe = LOCAL_FFMPEG.with_name("ffprobe.exe")
    if not ffprobe.exists():
        return None
    cmd = [
        str(ffprobe),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def extract_at_timestamps(video_path, timestamps):
    ffmpeg = require_tool(
        "ffmpeg",
        "Install FFmpeg and make sure `ffmpeg` is on PATH.",
    )
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    for old in FRAMES_DIR.glob("frame_*.jpg"):
        old.unlink()

    frames = []
    for index, ts in enumerate(timestamps, start=1):
        out = FRAMES_DIR / f"frame_{index:02d}.jpg"
        cmd = [
            ffmpeg,
            "-y",
            "-ss",
            f"{ts:.2f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            "scale=720:-1",
            str(out),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if out.exists():
            frames.append(out)
    if not frames:
        raise SystemExit("No frames were extracted. Check the video path and ffmpeg output.")
    return frames


def extract_audio_track(video_path, output_path=None):
    ffmpeg = require_tool(
        "ffmpeg",
        "Install FFmpeg and make sure `ffmpeg` is on PATH.",
    )
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    audio_path = Path(output_path) if output_path else FRAMES_DIR / "audio.wav"
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(audio_path),
    ]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    if not audio_path.exists():
        raise SystemExit("Audio extraction finished, but no audio file was created.")
    return audio_path


def compress_video_for_ai(video_path, output_path=None, width=360, crf=32, audio_bitrate="32k"):
    ffmpeg = require_tool(
        "ffmpeg",
        "Install FFmpeg and make sure `ffmpeg` is on PATH.",
    )
    out = Path(output_path) if output_path else FRAMES_DIR / "direct_video.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"scale={width}:-2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        str(crf),
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        str(out),
    ]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    if not out.exists():
        raise SystemExit("Video compression finished, but no output file was created.")
    return out


def transcribe_audio_track(audio_path, model_size="small", language="zh"):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SystemExit(
            "Missing `faster-whisper`. Install it with: pip install faster-whisper"
        ) from exc

    local_model = ROOT / "models" / f"faster-whisper-{model_size}"
    model_ref = str(local_model) if local_model.exists() else model_size
    device = "cpu"
    compute_type = "int8"
    try:
        model = WhisperModel(model_ref, device=device, compute_type=compute_type)
    except Exception as exc:
        raise SystemExit(
            f"Unable to load faster-whisper model '{model_size}'. "
            "If Hugging Face download is blocked, run without --transcribe or pre-download the model."
        ) from exc
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=language or "zh",
        vad_filter=True,
    )

    segments = []
    texts = []
    for segment in segments_iter:
        text = segment.text.strip()
        if not text:
            continue
        segments.append(
            {
                "start": round(float(segment.start), 2),
                "end": round(float(segment.end), 2),
                "text": text,
            }
        )
        texts.append(text)

    return {
        "model": model_size,
        "language": getattr(info, "language", "") or "",
        "language_probability": round(float(getattr(info, "language_probability", 0.0) or 0.0), 4),
        "text": " ".join(texts).strip(),
        "segments": segments,
    }


def _find_tesseract_cmd():
    if shutil.which("tesseract"):
        return "tesseract"
    candidates = [
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    return ""


def ocr_image_text(image_path, lang="chi_sim+eng"):
    try:
        cmd = _find_tesseract_cmd()
        if cmd:
            import pytesseract

            pytesseract.pytesseract.tesseract_cmd = cmd
            img = Image.open(image_path)
            data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)

            lines = []
            current = []
            current_num = None
            for i, text in enumerate(data.get("text", [])):
                text = (text or "").strip()
                if not text:
                    continue
                block_num = data.get("block_num", [None])[i]
                par_num = data.get("par_num", [None])[i]
                line_num = data.get("line_num", [None])[i]
                key = (block_num, par_num, line_num)
                if current_num is None:
                    current_num = key
                if key != current_num:
                    line_text = " ".join(current).strip()
                    if line_text:
                        lines.append(line_text)
                    current = [text]
                    current_num = key
                else:
                    current.append(text)
            if current:
                line_text = " ".join(current).strip()
                if line_text:
                    lines.append(line_text)

            return {
                "engine": "tesseract",
                "lang": lang,
                "text": "\n".join(lines).strip(),
                "lines": lines,
            }
    except Exception:
        pass

    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise SystemExit(
            "Missing OCR engine. Install either Tesseract+pytesseract or rapidocr-onnxruntime."
        ) from exc

    ocr = RapidOCR()
    result, _ = ocr(str(image_path))
    lines = []
    if result:
        for item in result:
            if len(item) >= 2:
                text = str(item[1]).strip()
                if text:
                    lines.append(text)

    return {
        "engine": "rapidocr",
        "lang": lang,
        "text": "\n".join(lines).strip(),
        "lines": lines,
    }


def extract_ocr_for_frames(frames, lang="chi_sim+eng"):
    results = []
    for frame in frames:
        try:
            ocr = ocr_image_text(frame, lang=lang)
        except SystemExit:
            raise
        except Exception as exc:
            ocr = {
                "engine": "tesseract",
                "lang": lang,
                "text": "",
                "lines": [],
                "error": str(exc),
            }
        results.append({
            "frame": f"/references/frames/{frame.name}",
            **ocr,
        })
    return results


def select_timestamps(duration, max_frames):
    if not duration:
        return [i * 3 for i in range(max_frames)]

    # Denser around the beginning and then evenly across the full video.
    anchors = [0.5, 1.5, 3, 5, 8, 12]
    remaining = max_frames - len([t for t in anchors if t < duration])
    if remaining > 0:
        step = duration / (remaining + 1)
        anchors.extend(step * i for i in range(1, remaining + 1))

    clean = sorted({round(min(max(t, 0), max(duration - 0.2, 0)), 2) for t in anchors if t < duration})
    return clean[:max_frames]


def make_contact_sheet(frames, columns=4):
    thumbs = []
    for frame in frames:
        img = Image.open(frame).convert("RGB")
        img.thumbnail((220, 392))
        thumbs.append((frame, img.copy()))

    if not thumbs:
        return None

    rows = (len(thumbs) + columns - 1) // columns
    cell_w, cell_h = 240, 440
    sheet = Image.new("RGB", (columns * cell_w, rows * cell_h), "#f5f7fb")
    draw = ImageDraw.Draw(sheet)

    for idx, (frame, img) in enumerate(thumbs):
        x = (idx % columns) * cell_w
        y = (idx // columns) * cell_h
        sheet.paste(img, (x + (cell_w - img.width) // 2, y + 12))
        draw.text((x + 12, y + cell_h - 34), frame.stem.replace("frame_", "#"), fill="#172033")

    out = FRAMES_DIR / "contact_sheet.jpg"
    sheet.save(out, quality=92)
    return out


def write_manifest(source_url, video_path, frames, audio_path=None, transcript=None, ocr=None, cover_path=None):
    reference_cover = ensure_reference_cover(cover_path)
    manifest = {
        "source_url": source_url,
        "video_path": str(video_path),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cover_image": f"/references/frames/{reference_cover.name}" if reference_cover else "",
        "cover_source": "downloaded" if reference_cover else "frame_fallback",
        "frames": [f"/references/frames/{frame.name}" for frame in frames],
        "contact_sheet": "/references/frames/contact_sheet.jpg",
        "notes": [
            "Use these frames as visual evidence for cover/title, topic, product, copy and storyboard analysis.",
            "cover_image is the downloaded platform cover when available; otherwise cover/title analysis falls back to selected video frames.",
            "Audio transcription is stored under audio.transcript when enabled.",
            "OCR text is stored under ocr when enabled.",
            "The current HTML page will load this manifest automatically when served by server.py.",
        ],
    }
    if audio_path:
        manifest["audio"] = {
            "path": f"/references/frames/{Path(audio_path).name}",
            "transcript": transcript or {},
        }
    if ocr:
        manifest["ocr"] = ocr
    manifest_path = FRAMES_DIR / "analysis.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def main():
    parser = argparse.ArgumentParser(description="Download a video with videodl and extract storyboard frames.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="Video page URL. Use only for content you are allowed to analyze.")
    source.add_argument("--video", help="Local video file path.")
    parser.add_argument("--client", default="VideoFKVideoClient", help="videodl client name.")
    parser.add_argument("--interval", type=int, default=3, help="Extract one frame every N seconds.")
    parser.add_argument("--max-frames", type=int, default=18, help="Maximum number of frames to extract.")
    parser.add_argument("--strategy", choices=["interval", "smart"], default="smart", help="Frame extraction strategy.")
    parser.add_argument("--transcribe", action="store_true", help="Extract audio and run local faster-whisper transcription.")
    parser.add_argument("--ocr", action="store_true", help="Run local OCR on extracted frames.")
    parser.add_argument("--ocr-lang", default="chi_sim+eng", help="Tesseract OCR language pack, e.g. chi_sim+eng.")
    parser.add_argument("--whisper-model", default="base", help="faster-whisper model size.")
    parser.add_argument("--whisper-language", default="", help="Optional transcription language hint, e.g. zh.")
    args = parser.parse_args()

    if args.url:
        video_path = download_with_videodl(args.url, args.client)
        source_url = args.url
    else:
        video_path = Path(args.video).expanduser().resolve()
        if not video_path.exists():
            raise SystemExit(f"Video file does not exist: {video_path}")
        source_url = ""
    cover_path = find_cover_for_video(video_path)

    if args.strategy == "interval":
        frames = extract_frames(video_path, args.interval, args.max_frames)
    else:
        duration = probe_duration(video_path)
        timestamps = select_timestamps(duration, args.max_frames)
        frames = extract_at_timestamps(video_path, timestamps)
    audio_path = None
    transcript = None
    if args.transcribe:
        audio_path = extract_audio_track(video_path)
        transcript = transcribe_audio_track(audio_path, model_size=args.whisper_model, language=args.whisper_language)
    ocr = None
    if args.ocr:
        ocr = extract_ocr_for_frames(frames, lang=args.ocr_lang)
    contact_sheet = make_contact_sheet(frames)
    manifest = write_manifest(source_url, video_path, frames, audio_path=audio_path, transcript=transcript, ocr=ocr, cover_path=cover_path)

    print(json.dumps({
        "video": str(video_path),
        "cover": str(cover_path) if cover_path else "",
        "frames": len(frames),
        "audio": str(audio_path) if audio_path else "",
        "transcript_segments": len(transcript.get("segments", [])) if transcript else 0,
        "ocr_frames": len(ocr or []),
        "contact_sheet": str(contact_sheet) if contact_sheet else "",
        "manifest": str(manifest),
        "result_page": "http://127.0.0.1:4173/analyze/9423b9da6e66",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
