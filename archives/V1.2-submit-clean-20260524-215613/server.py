import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request


ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
CUSTOM_MODEL_PATH = ROOT / "custom_model.txt"


class Handler(SimpleHTTPRequestHandler):
    def _default_provider(self):
        return "custom" if CUSTOM_MODEL_PATH.exists() else "ark"

    def _default_frame_select_mode(self):
        return "ai" if CUSTOM_MODEL_PATH.exists() else "heuristic"

    def _looks_bad_transcript(self, text):
        if not text:
            return True
        bad_markers = sum(text.count(ch) for ch in ["�", "\ufffd"])
        hangul = sum(1 for ch in text if "\uac00" <= ch <= "\ud7af")
        cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
        latin = sum(1 for ch in text if "a" <= ch.lower() <= "z")
        if bad_markers:
            return True
        if hangul > max(8, cjk * 2):
            return True
        if cjk < 8 and latin > 30:
            return True
        return len(text.strip()) < 12

    def _send_json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError:
            self._send_json({"error": "invalid_json"}, status=400)
            return None

    def _parse_analysis_stdout(self, stdout):
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            pass
        matches = list(re.finditer(r"\{\s*\"id\"\s*:", stdout))
        decoder = json.JSONDecoder()
        for match in reversed(matches):
            try:
                payload, _ = decoder.raw_decode(stdout[match.start():])
                return payload
            except json.JSONDecodeError:
                continue
        raise json.JSONDecodeError("analysis result JSON not found", stdout, 0)

    def _parse_json_stdout(self, stdout):
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            pass
        matches = list(re.finditer(r"\{\s*\"", stdout))
        for match in reversed(matches):
            try:
                return json.loads(stdout[match.start():])
            except json.JSONDecodeError:
                continue
        raise json.JSONDecodeError("JSON not found", stdout, 0)

    def _history_item_from_payload(self, task_id, payload, updated_at):
        analysis = payload.get("analysis") or {}
        summary = analysis.get("summary") or {}
        brief = payload.get("brief") or {}
        cover_image = payload.get("cover_image") or ""
        if not cover_image:
            for cover_name in ("cover.webp", "cover.png", "cover.jpg", "cover.jpeg"):
                if (RESULTS_DIR / task_id / cover_name).exists():
                    cover_image = f"/results/{task_id}/{cover_name}"
                    break
        return {
            "id": payload.get("id") or task_id,
            "source_url": payload.get("source_url") or "",
            "created_at": payload.get("created_at") or "",
            "updated_at": updated_at,
            "title": summary.get("title") or (analysis.get("cover_title") or {}).get("title") or "未命名分析",
            "desc": summary.get("desc") or "",
            "status": analysis.get("status") or "ok",
            "cover_image": cover_image,
            "brief": {
                "brand": brief.get("brand") or "",
                "product": brief.get("product") or "",
            },
        }

    def _write_task_status(self, task_id, body, status="running", message="正在下载视频、抽帧、选帧并生成拆解结果。"):
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        task_dir = RESULTS_DIR / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        brief = body.get("brief") if isinstance(body.get("brief"), dict) else {}
        payload = {
            "id": task_id,
            "status": status,
            "source_url": (body.get("url") or "").strip(),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "updated_at": time.time(),
            "title": "正在生成分析报告",
            "desc": message,
            "cover_image": "",
            "brief": {
                "brand": brief.get("brand") or "",
                "product": brief.get("product") or "",
            },
        }
        (task_dir / "status.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    def translate_path(self, path):
        path = path.split("?", 1)[0].split("#", 1)[0]
        if path.startswith("/analyze"):
            return str(ROOT / "index.html")
        if path.startswith("/results/") or path.startswith("/references/"):
            return str(ROOT / path.lstrip("/"))
        if path == "/":
            return str(ROOT / "index.html")
        return super().translate_path(path)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/history":
            items = []
            if RESULTS_DIR.exists():
                for result_path in RESULTS_DIR.glob("*/analysis.json"):
                    task_id = result_path.parent.name
                    try:
                        payload = json.loads(result_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        continue
                    analysis = payload.get("analysis") or {}
                    summary = analysis.get("summary") or {}
                    brief = payload.get("brief") or {}
                    cover_image = payload.get("cover_image") or ""
                    if not cover_image:
                        for cover_name in ("cover.webp", "cover.png", "cover.jpg", "cover.jpeg"):
                            if (RESULTS_DIR / task_id / cover_name).exists():
                                cover_image = f"/results/{task_id}/{cover_name}"
                                break
                    items.append({
                        "id": payload.get("id") or task_id,
                        "source_url": payload.get("source_url") or "",
                        "created_at": payload.get("created_at") or "",
                        "updated_at": result_path.stat().st_mtime,
                        "title": summary.get("title") or (analysis.get("cover_title") or {}).get("title") or "未命名分析",
                        "desc": summary.get("desc") or "",
                        "status": analysis.get("status") or "ok",
                        "cover_image": cover_image,
                        "brief": {
                            "brand": brief.get("brand") or "",
                            "product": brief.get("product") or "",
                        },
                    })
            completed_ids = {item.get("id") for item in items}
            if RESULTS_DIR.exists():
                for status_path in RESULTS_DIR.glob("*/status.json"):
                    task_id = status_path.parent.name
                    if task_id in completed_ids or (status_path.parent / "analysis.json").exists():
                        continue
                    try:
                        payload = json.loads(status_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        continue
                    payload["id"] = payload.get("id") or task_id
                    payload["updated_at"] = payload.get("updated_at") or status_path.stat().st_mtime
                    items.append(payload)
            items.sort(key=lambda item: item.get("updated_at") or 0, reverse=True)
            self._send_json({"items": items[:30]})
            return

        if path.startswith("/api/result/"):
            task_id = path.rsplit("/", 1)[-1]
            result_path = RESULTS_DIR / task_id / "analysis.json"
            if not result_path.exists():
                status_path = RESULTS_DIR / task_id / "status.json"
                if status_path.exists():
                    try:
                        payload = json.loads(status_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        payload = {"id": task_id, "status": "running", "title": "正在生成分析报告"}
                    payload["id"] = payload.get("id") or task_id
                    self._send_json(payload)
                    return
                self._send_json({"error": "not_found", "id": task_id}, status=404)
                return
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            if not payload.get("cover_image"):
                for cover_name in ("cover.webp", "cover.png", "cover.jpg", "cover.jpeg"):
                    cover_path = RESULTS_DIR / task_id / cover_name
                    if cover_path.exists():
                        payload["cover_image"] = f"/results/{task_id}/{cover_name}"
                        payload["cover_source"] = payload.get("cover_source") or "downloaded"
                        break
            transcript = (((payload.get("audio") or {}).get("transcript")) or {})
            if self._looks_bad_transcript(transcript.get("text", "")):
                if payload.get("audio"):
                    payload["audio"]["transcript_raw"] = transcript
                    payload["audio"]["transcript"] = {}
                    payload["audio"]["transcript_status"] = "filtered"
                    payload["audio"]["transcript_message"] = "转写结果疑似背景音乐/非中文口播，已过滤。"
            self._send_json(payload)
            return

        if path == "/api/ark-health":
            self._send_json(self._check_ark_health())
            return
        return super().do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/analyze":
            body = self._read_body()
            if body is None:
                return
            source_url = (body.get("url") or "").strip()
            if not source_url:
                self._send_json({"error": "missing_url"}, status=400)
                return

            task_id = uuid.uuid4().hex[:12]
            self._write_task_status(task_id, body)
            cmd = [
                sys.executable,
                str(ROOT / "scripts" / "analyze_video.py"),
                "--url",
                source_url,
                "--task-id",
                task_id,
                "--provider",
                (body.get("provider") or self._default_provider()).strip() or self._default_provider(),
                "--frame-select-mode",
                (body.get("frame_select_mode") or self._default_frame_select_mode()).strip() or self._default_frame_select_mode(),
                "--analysis-mode",
                (body.get("analysis_mode") or "frames").strip() or "frames",
            ]
            model = (body.get("model") or "").strip()
            if model:
                cmd.extend(["--model", model])
            brief = body.get("brief") if isinstance(body.get("brief"), dict) else {}
            if brief:
                RESULTS_DIR.mkdir(parents=True, exist_ok=True)
                brief_file = tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    suffix=".json",
                    delete=False,
                    dir=RESULTS_DIR,
                )
                with brief_file:
                    json.dump(brief, brief_file, ensure_ascii=False, indent=2)
                cmd.extend(["--brief-file", brief_file.name])
            use_transcribe = body.get("transcribe", True)
            use_ocr = body.get("ocr", True)
            if use_transcribe and importlib.util.find_spec("faster_whisper") is not None:
                cmd.append("--transcribe")
                whisper_model = (body.get("whisper_model") or "").strip()
                whisper_language = (body.get("whisper_language") or "").strip()
                cmd.extend(["--whisper-model", whisper_model or "small"])
                cmd.extend(["--whisper-language", whisper_language or "zh"])
            if use_ocr:
                cmd.append("--ocr")
                ocr_lang = (body.get("ocr_lang") or "").strip()
                if ocr_lang:
                    cmd.extend(["--ocr-lang", ocr_lang])
            self._run_analysis_command(cmd)
            return

        if path == "/api/retry-analysis":
            body = self._read_body()
            if body is None:
                return
            task_id = (body.get("id") or "").strip()
            if not task_id:
                self._send_json({"error": "missing_id"}, status=400)
                return
            cmd = [
                sys.executable,
                str(ROOT / "scripts" / "retry_ai_analysis.py"),
                "--id",
                task_id,
                "--provider",
                (body.get("provider") or self._default_provider()).strip() or self._default_provider(),
                "--analysis-mode",
                (body.get("analysis_mode") or "frames").strip() or "frames",
            ]
            model = (body.get("model") or "").strip()
            if model:
                cmd.extend(["--model", model])
            brief = body.get("brief") if isinstance(body.get("brief"), dict) else {}
            if brief:
                RESULTS_DIR.mkdir(parents=True, exist_ok=True)
                brief_file = tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    suffix=".json",
                    delete=False,
                    dir=RESULTS_DIR,
                )
                with brief_file:
                    json.dump(brief, brief_file, ensure_ascii=False, indent=2)
                cmd.extend(["--brief-file", brief_file.name])
            if body.get("only_style"):
                cmd.append("--only-style")
            self._run_analysis_command(cmd)
            return

        if path == "/api/write-feishu":
            body = self._read_body()
            if body is None:
                return
            task_id = (body.get("id") or "").strip()
            if not task_id:
                self._send_json({"error": "missing_id"}, status=400)
                return
            result_path = RESULTS_DIR / task_id / "analysis.json"
            if not result_path.exists():
                self._send_json({"error": "not_found", "id": task_id}, status=404)
                return
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._send_json({"error": "invalid_result", "id": task_id}, status=500)
                return
            if not self._has_deliverable_script(payload):
                self._send_json(
                    {
                        "error": "script_not_ready",
                        "message": "脚本还没生成，请先重新生成 AI 分析后再写入飞书。",
                        "id": task_id,
                    },
                    status=409,
                )
                return
            cmd = [
                sys.executable,
                str(ROOT / "feishu" / "write_doc.py"),
                "--id",
                task_id,
            ]
            title = (body.get("title") or "").strip()
            if title:
                cmd.extend(["--title", title])
            self._run_json_command(cmd)
            return

        return super().do_POST()

    def _run_analysis_command(self, cmd):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        started_at = time.time()
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=ROOT,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        if proc.returncode != 0:
            recovered = self._recover_recent_result(started_at)
            if recovered:
                self._send_json(recovered)
                return
            self._send_json(
                {
                    "error": "analysis_failed",
                    "message": self._summarize_command_error(proc),
                    "stderr": proc.stderr[-4000:],
                    "stdout": proc.stdout[-4000:],
                },
                status=500,
            )
            return
        try:
            result = self._parse_analysis_stdout(proc.stdout)
        except json.JSONDecodeError:
            self._send_json(
                {
                    "error": "invalid_result",
                    "stdout": proc.stdout[-4000:],
                    "stderr": proc.stderr[-4000:],
                },
                status=500,
            )
            return
        result_id = result.get("id")
        if result_id:
            status_path = RESULTS_DIR / result_id / "status.json"
            if status_path.exists():
                try:
                    status_payload = json.loads(status_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    status_payload = {"id": result_id}
                status_payload["status"] = "completed"
                status_payload["updated_at"] = time.time()
                status_path.write_text(json.dumps(status_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._send_json(result)

    def _has_deliverable_script(self, payload):
        script = (((payload.get("analysis") or {}).get("style_imitation") or {}).get("imitation_script") or {})
        voiceover = script.get("voiceover")
        if isinstance(voiceover, list):
            return any(str(item).strip() for item in voiceover)
        return bool(str(voiceover or "").strip())

    def _check_ark_health(self):
        try:
            from scripts.select_frames_ai import read_provider_config

            base_url, api_key, model = read_provider_config("ark", "")
            req = request.Request(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
                method="GET",
            )
            with request.urlopen(req, timeout=15) as resp:
                sample = resp.read(400).decode("utf-8", errors="replace")
            return {
                "ok": True,
                "base_url": base_url,
                "model": model,
                "status": getattr(resp, "status", None),
                "sample": sample[:200],
            }
        except Exception as exc:
            return {
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "hint": "当前 server.py 进程无法连接豆包，请确认服务是从普通 PowerShell 启动，并且旧的 4173 进程已关闭。",
            }

    def _recover_recent_result(self, started_at):
        if not RESULTS_DIR.exists():
            return None
        candidates = []
        for result_path in RESULTS_DIR.glob("*/analysis.json"):
            try:
                mtime = result_path.stat().st_mtime
            except OSError:
                continue
            if mtime >= started_at - 5:
                candidates.append((mtime, result_path))
        if not candidates:
            return None
        _, result_path = max(candidates, key=lambda item: item[0])
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return {
            "id": payload.get("id") or result_path.parent.name,
            "result_url": f"http://127.0.0.1:4173/analyze/{payload.get('id') or result_path.parent.name}",
            "api_result_url": f"http://127.0.0.1:4173/api/result/{payload.get('id') or result_path.parent.name}",
            "recovered": True,
        }

    def _summarize_command_error(self, proc):
        text = "\n".join([proc.stderr or "", proc.stdout or ""]).strip()
        if not text:
            return "analysis command failed"
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in reversed(lines):
            if "Error" in line or "error" in line or "Traceback" in line or "WinError" in line:
                return line[-800:]
        return lines[-1][-800:] if lines else "analysis command failed"

    def _run_json_command(self, cmd):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=ROOT,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        if proc.returncode != 0:
            self._send_json(
                {
                    "error": "command_failed",
                    "stderr": proc.stderr[-4000:],
                    "stdout": proc.stdout[-4000:],
                },
                status=500,
            )
            return
        try:
            result = self._parse_json_stdout(proc.stdout)
        except json.JSONDecodeError:
            self._send_json(
                {
                    "error": "invalid_result",
                    "stdout": proc.stdout[-4000:],
                    "stderr": proc.stderr[-4000:],
                },
                status=500,
            )
            return
        self._send_json(result)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 4173), Handler)
    print("Serving on http://127.0.0.1:4173")
    server.serve_forever()
