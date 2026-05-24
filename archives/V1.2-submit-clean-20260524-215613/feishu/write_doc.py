import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib import request
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_workflow import build_and_save


MAX_BLOCKS_PER_REQUEST = 50


def load_secret_file():
    secret_path = ROOT / "信息.txt"
    if not secret_path.exists():
        return

    text = secret_path.read_text(encoding="utf-8")
    app_id_match = re.search(r"(cli_[A-Za-z0-9]+)", text)
    domain_match = re.search(
        r"(?:FEISHU_DOC_BASE_URL|FEISHU_DOMAIN|飞书域名|文档域名)[：:=\s]+(https?://[^\s]+|[A-Za-z0-9-]+\.feishu\.cn)",
        text,
        re.IGNORECASE,
    )
    secret_match = re.search(
        r"(?:APP_SECRET|APPSecret|App Secret|app_secret|secret|密钥)[：:=\s]+([A-Za-z0-9_-]+)",
        text,
        re.IGNORECASE,
    )

    if app_id_match and not os.getenv("FEISHU_APP_ID"):
        os.environ["FEISHU_APP_ID"] = app_id_match.group(1)
    if domain_match and not os.getenv("FEISHU_DOC_BASE_URL"):
        domain = domain_match.group(1).rstrip("/")
        if not domain.startswith("http"):
            domain = f"https://{domain}"
        os.environ["FEISHU_DOC_BASE_URL"] = domain
    if secret_match and not os.getenv("FEISHU_APP_SECRET"):
        os.environ["FEISHU_APP_SECRET"] = secret_match.group(1)


def post_json(url, payload, token=None):
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=data, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc


def get_tenant_access_token(app_id, app_secret):
    result = post_json(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        {"app_id": app_id, "app_secret": app_secret},
    )
    if result.get("code") != 0:
        raise RuntimeError(f"Failed to get tenant token: {result}")
    return result["tenant_access_token"]


def build_doc_markdown(task_id=""):
    if task_id:
        build_and_save(task_id)
        delivery_path = ROOT / "results" / task_id / "delivery.md"
        if not delivery_path.exists():
            raise SystemExit(f"Delivery markdown not found: {delivery_path}")
        return delivery_path.read_text(encoding="utf-8")

    report = (ROOT / "report.md").read_text(encoding="utf-8")
    script = (ROOT / "output" / "final-script.md").read_text(encoding="utf-8")
    storyboard = (ROOT / "output" / "storyboard.md").read_text(encoding="utf-8")

    return "\n\n".join(
        [
            "# 小红书商单脚本交付稿",
            "## 达人调研与选择",
            report,
            "## 最终脚本",
            script,
            "## 分镜设计",
            storyboard,
        ]
    )


def create_doc(token, title):
    payload = {"title": title}
    folder_token = os.getenv("FEISHU_FOLDER_TOKEN")
    if folder_token:
        payload["folder_token"] = folder_token

    result = post_json(
        "https://open.feishu.cn/open-apis/docx/v1/documents",
        payload,
        token=token,
    )
    if result.get("code") != 0:
        raise RuntimeError(f"Failed to create doc: {result}")
    return result["data"]["document"]["document_id"]


def text_elements(content):
    return [{"text_run": {"content": content}}]


def make_block(block_type, key, content):
    return {"block_type": block_type, key: {"elements": text_elements(content)}}


def split_markdown_table_row(line):
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    return cells


def is_markdown_table_separator(cells):
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells)


def append_table_row_blocks(blocks, headers, cells):
    if not headers or len(cells) != len(headers):
        blocks.append(make_block(2, "text", " | ".join(cells)))
        return

    row = dict(zip(headers, cells))
    first_header = headers[0]
    first_value = row.get(first_header, "").strip()

    title = f"{first_header} {first_value}".strip() if first_value else "表格行"
    blocks.append(make_block(5, "heading3", title))

    for header in headers[1:]:
        value = row.get(header, "").strip()
        if value:
            blocks.append(make_block(12, "bullet", f"{header}：{value}"))


def markdown_to_blocks(markdown):
    blocks = []
    table_headers = None
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if not line:
            table_headers = None
            continue

        cells = split_markdown_table_row(line)
        if cells:
            if is_markdown_table_separator(cells):
                continue
            if table_headers is None:
                table_headers = cells
                continue
            append_table_row_blocks(blocks, table_headers, cells)
            continue

        table_headers = None
        if line.startswith("# "):
            blocks.append(make_block(3, "heading1", line[2:].strip()))
        elif line.startswith("## "):
            blocks.append(make_block(4, "heading2", line[3:].strip()))
        elif line.startswith("### "):
            blocks.append(make_block(5, "heading3", line[4:].strip()))
        elif line.startswith("- "):
            blocks.append(make_block(12, "bullet", line[2:].strip()))
        elif re.match(r"^\d+\.\s+", line):
            blocks.append(make_block(13, "ordered", re.sub(r"^\d+\.\s+", "", line).strip()))
        else:
            blocks.append(make_block(2, "text", line))
    return blocks


def write_blocks(token, document_id, markdown):
    blocks = markdown_to_blocks(markdown)
    url = (
        "https://open.feishu.cn/open-apis/docx/v1/documents/"
        f"{document_id}/blocks/{document_id}/children?document_revision_id=-1"
    )

    for start in range(0, len(blocks), MAX_BLOCKS_PER_REQUEST):
        chunk = blocks[start : start + MAX_BLOCKS_PER_REQUEST]
        result = post_json(url, {"children": chunk, "index": -1}, token=token)
        if result.get("code") != 0:
            raise RuntimeError(f"Failed to write blocks: {result}")
        time.sleep(0.4)

    return len(blocks)


def save_feishu_result(task_id, result):
    if not task_id:
        return
    result_path = ROOT / "results" / task_id / "analysis.json"
    if not result_path.exists():
        return
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    workflow = payload.setdefault("workflow", {})
    workflow["feishu"] = {
        **result,
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_doc(task_id="", title=""):
    load_secret_file()

    app_id = os.getenv("FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET")
    title = title or os.getenv("FEISHU_DOC_TITLE") or "小红书商单脚本交付稿"

    if not app_id or not app_secret:
        raise SystemExit("Missing FEISHU_APP_ID or FEISHU_APP_SECRET.")

    token = get_tenant_access_token(app_id, app_secret)
    document_id = create_doc(token, title)
    body = build_doc_markdown(task_id)
    block_count = write_blocks(token, document_id, body)

    output = {
        "document_id": document_id,
        "reference_url": f"{os.getenv('FEISHU_DOC_BASE_URL', 'https://feishu.cn').rstrip('/')}/docx/{document_id}",
        "body_chars": len(body),
        "blocks_written": block_count,
    }
    save_feishu_result(task_id, output)
    return output


def main():
    parser = argparse.ArgumentParser(description="Create a Feishu doc and write delivery markdown.")
    parser.add_argument("--id", default="", help="Result id under results/<id>/analysis.json.")
    parser.add_argument("--title", default="", help="Feishu document title.")
    args = parser.parse_args()

    output = write_doc(task_id=args.id, title=args.title)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
