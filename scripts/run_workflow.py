import argparse
import json
import re
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "results"


RISK_RULES = [
    ("减肥/瘦身承诺", r"减肥|瘦身|掉秤|暴瘦|燃脂|刮油"),
    ("医疗/治疗表达", r"治疗|治愈|改善疾病|降糖|降血糖|药效|疗效"),
    ("绝对化宣传", r"100%|百分百|一定|保证|必爆|无风险|最有效|第一"),
    ("夸大前后对比", r"立刻见效|马上见效|一天见效|前后对比"),
]


def load_result(task_id):
    result_path = RESULTS_DIR / task_id / "analysis.json"
    if not result_path.exists():
        raise SystemExit(f"Result not found: {task_id}")
    return json.loads(result_path.read_text(encoding="utf-8"))


def write_result(task_id, payload):
    result_path = RESULTS_DIR / task_id / "analysis.json"
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[\n,，、；;]+", value) if item.strip()]
    return [value]


def plain(value):
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def bullet_lines(items):
    lines = []
    for item in as_list(items):
        text = plain(item).strip()
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines) if lines else "- 暂无"


def numbered_lines(items):
    lines = []
    for index, item in enumerate(as_list(items), start=1):
        text = plain(item).strip()
        if text:
            lines.append(f"{index}. {text}")
    return "\n".join(lines) if lines else "1. 暂无"


def collect_script_text(style):
    script = style.get("imitation_script") or {}
    parts = []
    for key in ("title_options", "hook", "voiceover", "product_insertions", "cta"):
        parts.extend(plain(item) for item in as_list(script.get(key)))
    parts.extend(plain(item) for item in as_list(style.get("compliance_notes")))
    return "\n".join(parts)


def quality_check(payload):
    analysis = payload.get("analysis") or {}
    style = analysis.get("style_imitation") or {}
    text = collect_script_text(style)
    issues = []
    for label, pattern in RISK_RULES:
        matches = sorted(set(re.findall(pattern, text, flags=re.IGNORECASE)))
        if matches:
            issues.append({
                "label": label,
                "matches": matches,
                "suggestion": "建议改成个人体验、场景描述或可观察事实，避免功效承诺。",
            })

    has_script = bool((style.get("imitation_script") or {}).get("voiceover"))
    if not has_script:
        issues.append({
            "label": "脚本缺失",
            "matches": [],
            "suggestion": "当前结果没有可交付口播脚本，请先重跑 AI 分析或只重跑风格仿写。",
        })

    return {
        "pass": not issues,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "issues": issues,
        "fixes": [
            "保留产品卖点，但用使用场景、口感、搭配和个人感受表达。",
            "避免减肥、治疗、降糖、立刻见效、百分百等高风险措辞。",
            "风格模仿只迁移结构、节奏、语气和场景，不复制原句或镜头。",
        ],
    }


def build_brief_section(brief):
    return "\n".join([
        "## Brief 拆解",
        f"- 品牌：{brief.get('brand') or '未填写'}",
        f"- 产品：{brief.get('product') or '未填写'}",
        f"- 主要卖点：{'、'.join(as_list(brief.get('selling_points'))) or '未填写'}",
        f"- 目标人群：{brief.get('audience') or '未填写'}",
        f"- 投放平台：{brief.get('platform') or '小红书短视频'}",
        f"- 限制/禁用表达：{'、'.join(as_list(brief.get('constraints'))) or '未填写'}",
    ])


def build_style_section(style):
    profile = style.get("style_profile") or {}
    return "\n".join([
        "## 内容风格模仿",
        style.get("desc") or "模仿结构、节奏、语气和场景，不复制原句或镜头。",
        "",
        "### 可迁移结构",
        f"- 标题公式：{plain(profile.get('title_formula')) or '暂无'}",
        f"- 开头钩子：{plain(profile.get('hook_pattern')) or '暂无'}",
        f"- 镜头节奏：{plain(profile.get('scene_rhythm') or profile.get('reusable_structure')) or '暂无'}",
        "",
        "### 语气规则",
        bullet_lines(profile.get("tone_rules")),
        "",
        "### 不照搬项",
        bullet_lines(profile.get("do_not_copy")),
    ])


def build_script_section(style):
    script = style.get("imitation_script") or {}
    return "\n".join([
        "## 脚本生成",
        "### 标题备选",
        bullet_lines(script.get("title_options")),
        "",
        "### 开头钩子",
        plain(script.get("hook")) or "暂无",
        "",
        "### 口播文案",
        numbered_lines(script.get("voiceover")),
        "",
        "### 产品植入点",
        bullet_lines(script.get("product_insertions")),
        "",
        "### 结尾 CTA",
        plain(script.get("cta")) or "暂无",
    ])


def build_storyboard_section(style, analysis):
    storyboard = style.get("imitation_storyboard") or analysis.get("storyboard") or []
    lines = [
        "## 分镜脚本",
        "| 镜头 | 画面 | 口播/字幕 | 道具/场景 | 时长 | 借鉴点 |",
        "|---|---|---|---|---|---|",
    ]
    for index, shot in enumerate(storyboard, start=1):
        if not isinstance(shot, dict):
            continue
        cells = [
            plain(shot.get("shot") or index),
            plain(shot.get("visual")),
            plain(shot.get("voice_or_subtitle")),
            plain(shot.get("props_scene")),
            plain(shot.get("duration_sec")),
            plain(shot.get("style_reference")),
        ]
        lines.append("| " + " | ".join(cell.replace("|", "/") for cell in cells) + " |")
    if len(lines) == 3:
        lines.append("| 1 | 暂无 | 暂无 | 暂无 | 暂无 | 暂无 |")
    return "\n".join(lines)


def build_quality_section(check):
    lines = [
        "## 风险质检",
        f"- 质检结果：{'通过' if check.get('pass') else '需修改'}",
        f"- 质检时间：{check.get('checked_at') or ''}",
        "",
        "### 问题",
    ]
    issues = check.get("issues") or []
    if issues:
        for item in issues:
            matches = "、".join(item.get("matches") or [])
            lines.append(f"- {item.get('label')}: {matches or '未命中具体词'}；{item.get('suggestion') or ''}")
    else:
        lines.append("- 未发现本地规则命中的高风险表达。")
    lines.extend(["", "### 修改原则", bullet_lines(check.get("fixes"))])
    return "\n".join(lines)


def build_delivery_markdown(payload):
    analysis = payload.get("analysis") or {}
    style = analysis.get("style_imitation") or {}
    brief = payload.get("brief") or {}
    summary = analysis.get("summary") or {}
    check = quality_check(payload)
    title = (
        (summary.get("title") if isinstance(summary, dict) else "")
        or (brief.get("product") and f"{brief.get('product')}小红书商单脚本")
        or "小红书商单脚本交付稿"
    )
    body = "\n\n".join([
        f"# {title}",
        build_brief_section(brief),
        build_style_section(style),
        build_script_section(style),
        build_storyboard_section(style, analysis),
        build_quality_section(check),
    ])
    return body, check


def build_and_save(task_id):
    payload = load_result(task_id)
    body, check = build_delivery_markdown(payload)
    task_dir = RESULTS_DIR / task_id
    markdown_path = task_dir / "delivery.md"
    markdown_path.write_text(body, encoding="utf-8")

    workflow = payload.setdefault("workflow", {})
    workflow["delivery_markdown"] = f"/results/{task_id}/delivery.md"
    workflow["quality_check"] = check
    workflow["updated_at"] = datetime.now().isoformat(timespec="seconds")
    write_result(task_id, payload)
    return {
        "id": task_id,
        "delivery_markdown": str(markdown_path),
        "quality_check": check,
        "body_chars": len(body),
    }


def main():
    parser = argparse.ArgumentParser(description="Build delivery markdown and quality check for an analysis result.")
    parser.add_argument("--id", required=True, help="Result id under results/<id>/analysis.json.")
    args = parser.parse_args()
    print(json.dumps(build_and_save(args.id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
