# -*- coding: utf-8 -*-
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "detailed-project-flow.png"
W, H = 1600, 1120


def font(size, bold=False, mono=False):
    if mono:
        path = Path(r"C:\Windows\Fonts\consola.ttf")
        if path.exists():
            return ImageFont.truetype(str(path), size)
    path = Path(r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc")
    if path.exists():
        return ImageFont.truetype(str(path), size)
    for fallback in [
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\msyh.ttc",
    ]:
        if Path(fallback).exists():
            return ImageFont.truetype(fallback, size)
    return ImageFont.load_default()


img = Image.new("RGB", (W, H), "#f6f8fb")
d = ImageDraw.Draw(img)


def rect(x, y, w, h, fill="#ffffff", outline="#d9e4ec", radius=10, width=2):
    d.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=fill, outline=outline, width=width)


def text(x, y, value, size=15, fill="#132638", bold=False, mono=False, anchor=None):
    d.text((x, y), value, font=font(size, bold=bold, mono=mono), fill=fill, anchor=anchor)


def badge(x, y, value, fill):
    d.ellipse([x, y, x + 34, y + 34], fill=fill)
    text(x + 17, y + 17, value, 14, "#ffffff", bold=True, anchor="mm")


def arrow(x1, y1, x2, y2, fill="#34495a", width=3, dashed=False):
    import math

    if dashed:
        dx, dy = x2 - x1, y2 - y1
        dist = math.hypot(dx, dy)
        if dist:
            ux, uy = dx / dist, dy / dist
            pos = 0
            while pos < dist - 14:
                end = min(pos + 12, dist - 14)
                d.line([x1 + ux * pos, y1 + uy * pos, x1 + ux * end, y1 + uy * end], fill=fill, width=width)
                pos += 20
    else:
        d.line([x1, y1, x2, y2], fill=fill, width=width)

    angle = math.atan2(y2 - y1, x2 - x1)
    length = 14
    spread = 0.55
    p1 = (x2 - length * math.cos(angle - spread), y2 - length * math.sin(angle - spread))
    p2 = (x2 - length * math.cos(angle + spread), y2 - length * math.sin(angle + spread))
    d.polygon([(x2, y2), p1, p2], fill=fill)


def stage(x, y, num, title, file_name, lines, fill="#ffffff", badge_fill="#1677ff", w=205):
    rect(x, y, w, 205, fill=fill)
    badge(x + 17, y + 17, num, badge_fill)
    text(x + 58, y + 20, title, 21, bold=True)
    text(x + 24, y + 70, file_name, 14, "#436075", bold=True, mono=True)
    yy = y + 105
    for line in lines:
        text(x + 24, yy, line, 15, "#4d6274")
        yy += 28


text(70, 45, "电商助手项目详细流程图", 38, "#122334", bold=True)
text(70, 90, "按阶段梳理：网页提交任务，后端调度脚本，处理视频素材，AI 生成脚本分镜，人工确认后交付飞书。", 18, "#607184")

rect(60, 145, 1480, 420, "#ffffff", "#dce5ed", radius=12)
text(84, 172, "主流程", 22, bold=True)
text(84, 204, "从左到右是一次完整任务的正常路径，底部只保留关键产物，减少交叉线。", 13, "#5e7181")

stage(92, 235, "1", "网页输入", "index.html", ["打开 /analyze", "填写视频链接", "填写商单 Brief"])
stage(342, 235, "2", "服务调度", "server.py", ["POST /api/analyze", "创建任务 ID", "写入 status.json"])
stage(592, 235, "3", "素材处理", "video_pipeline.py", ["下载视频", "抽封面和关键帧", "转写 / OCR 可选"], badge_fill="#10a66e")
stage(842, 235, "4", "AI 生成", "analyze_video.py", ["AI/启发式选帧", "拆解主题和文案", "生成脚本和分镜"], badge_fill="#10a66e")
stage(1092, 235, "5", "保存结果", "results/<id>", ["analysis.json", "frames / cover", "contact_sheet.jpg"], fill="#fffaf0", badge_fill="#c07a00")
stage(1342, 235, "6", "结果页", "/analyze/<id>", ["查看拆解", "查看脚本", "查看分镜"], w=165)

for x1, x2 in [(297, 332), (547, 582), (797, 832), (1047, 1082), (1297, 1332)]:
    arrow(x1, 337, x2, 337)

rect(165, 470, 295, 60, "#fffaf0", "#e4c36f")
text(185, 486, "status.json", 14, "#436075", bold=True, mono=True)
text(185, 511, "任务运行中、页面可读进度", 13, "#5e7181")

rect(535, 470, 330, 60, "#fffaf0", "#e4c36f")
text(555, 486, "references/frames/analysis.json", 14, "#436075", bold=True, mono=True)
text(555, 511, "临时素材清单：帧、封面、转写、OCR", 13, "#5e7181")

rect(940, 470, 330, 60, "#fffaf0", "#e4c36f")
text(960, 486, "results/<id>/analysis.json", 14, "#436075", bold=True, mono=True)
text(960, 511, "最终分析、仿写脚本、分镜和风险信息", 13, "#5e7181")

for x in [445, 695, 1195]:
    arrow(x, 440, x, 460, "#8092a0", 2, dashed=True)

rect(60, 620, 1480, 355, "#ffffff", "#dce5ed", radius=12)
text(84, 647, "确认、重试与交付", 22, bold=True)
text(84, 675, "结果页之后只有两个方向：不满意就重试 AI，满意就生成交付稿并写入飞书。", 13, "#5e7181")

d.line([1425, 440, 1425, 590, 245, 590, 245, 700], fill="#34495a", width=3)
arrow(245, 590, 245, 700)
text(1160, 582, "结果页查看后进入人工确认", 13, "#5e7181")

d.polygon([(245, 715), (360, 787), (245, 859), (130, 787)], fill="#eef7ff", outline="#9cc8f1")
text(245, 775, "结果是否满意？", 21, bold=True, anchor="mm")
text(245, 805, "人工判断", 15, "#4d6274", anchor="mm")

rect(445, 675, 290, 112)
badge(461, 691, "R", "#607184")
text(505, 694, "不满意：重试", 21, bold=True)
text(469, 743, "POST /api/retry-analysis", 14, "#436075", bold=True, mono=True)
text(469, 770, "保留素材，只重跑 AI 分析/仿写", 15, "#4d6274")
arrow(360, 760, 435, 760, "#8092a0", 2, dashed=True)
text(376, 744, "不满意", 13, "#5e7181")
d.line([735, 731, 1488, 731, 1488, 455], fill="#8092a0", width=2)
arrow(1488, 731, 1488, 455, "#8092a0", 2, dashed=True)
text(1328, 713, "重试后回到结果页", 13, "#5e7181")

rect(445, 845, 290, 112, "#f4fff7", "#98d2ab")
badge(461, 861, "A", "#10a66e")
text(505, 864, "满意：交付", 21, bold=True)
text(469, 913, "POST /api/write-feishu", 14, "#436075", bold=True, mono=True)
text(469, 940, "要求脚本已经生成", 15, "#4d6274")

rect(790, 845, 290, 112, "#f4fff7", "#98d2ab")
badge(806, 861, "B", "#10a66e")
text(850, 864, "质检并出稿", 21, bold=True)
text(814, 913, "run_workflow.py", 14, "#436075", bold=True, mono=True)
text(814, 940, "生成 delivery.md 和风险质检", 15, "#4d6274")

rect(1135, 845, 290, 112, "#f4fff7", "#98d2ab")
badge(1151, 861, "C", "#10a66e")
text(1195, 864, "写入飞书", 21, bold=True)
text(1159, 913, "feishu/write_doc.py", 14, "#436075", bold=True, mono=True)
text(1159, 940, "创建文档，写入 blocks，回写记录", 15, "#4d6274")

d.line([245, 859, 245, 901, 435, 901], fill="#34495a", width=3)
arrow(245, 901, 435, 901)
text(302, 886, "满意", 13, "#5e7181")
arrow(735, 901, 780, 901)
arrow(1080, 901, 1125, 901)

rect(70, 1010, 1460, 66, "#edf4fa", "#d1dce5", radius=9)
text(90, 1026, "补充说明", 14, "#436075", bold=True)
text(90, 1049, "模型来源：优先 custom_model.txt；没有时使用 Ark 配置。AI 失败时会保存已有素材和兜底结果，后续可从结果页重试。", 13, "#5e7181")

OUT.parent.mkdir(parents=True, exist_ok=True)
img.save(OUT)
print(OUT)
