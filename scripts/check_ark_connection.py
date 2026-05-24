import json
import sys
from pathlib import Path
from urllib import request
from urllib.error import URLError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.select_frames_ai import read_provider_config


def main():
    try:
        base_url, api_key, model = read_provider_config("ark", "")
        req = request.Request(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            method="GET",
        )
        with request.urlopen(req, timeout=15) as resp:
            body = resp.read(800).decode("utf-8", errors="replace")
        print(json.dumps({
            "ok": True,
            "base_url": base_url,
            "model": model,
            "status": getattr(resp, "status", None),
            "sample": body[:200],
        }, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "hint": "如果是 WinError 10013，说明当前 Python 进程被系统/沙盒/防火墙拦截，尚未连到豆包服务。",
        }, ensure_ascii=False, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
