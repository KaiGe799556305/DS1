# MCN AI 商单脚本助手

这是一个围绕「小红书达人调研 + 商单脚本生成助手」测试题完成的轻量 AI Agent 项目。目标不是生成泛泛文案，而是把 MCN 商单从 brief 拆解、达人筛选、内容参考、脚本分镜、风险质检到飞书文档写入串成一条可复用流程。

## 题目 Brief

- 品牌：轻食酸奶「轻醒」
- 产品：0 蔗糖高蛋白希腊酸奶，口味包含原味、蓝莓、黄桃
- 卖点：高蛋白、饱腹感、低负担，适合早餐、运动后、下午茶
- 人群：22-35 岁城市女性，关注健身、控糖、轻食和上班族效率生活
- 平台：小红书短视频
- 限制：自然种草，不要硬广；不能承诺减肥、治疗、降糖等功效；避免夸大效果

## 交付物

- `report.md`：业务理解、达人筛选、参考内容拆解、AI 工作流和交付说明
- `references/creator-research.md`：3 位候选达人的调研证据和选择理由
- `references/creator-1.jpg`、`references/creator-2.jpg`、`references/creator-3.jpg`：达人参考截图
- `prompts/core-prompt.md`：核心 Prompt
- `skills/mcn-script-assistant/SKILL.md`：可复用 Skill
- `output/final-script.md`：最终 60-90 秒短视频脚本
- `output/storyboard.md`：至少 6 个镜头的分镜设计
- `output/quality-check.md`：合规风险质检结果
- `feishu/write_doc.py`：飞书文档自动写入脚本
- `feishu/integration.md`：飞书接入说明
- `index.html`、`server.py`：本地结果页和分析接口 demo

## AI 工作流

1. Brief 解析：提取产品卖点、目标人群、平台调性和风险禁区。
2. 达人匹配：按人设、内容场景、表达方式和品牌匹配度筛选达人。
3. 内容风格模仿：只迁移标题公式、开头钩子、镜头节奏、语气和场景结构，不复制原句。
4. 脚本与分镜生成：输出标题、口播、产品植入点、CTA 和 6 个以上镜头。
5. 风险质检：检查减肥、治疗、降糖、绝对化宣传、照搬达人内容等风险。
6. 飞书写入：把最终交付稿通过脚本写入飞书文档。

## 最终达人选择

- 主达人：脸圆的聪花
- 副达人：小张今晚不想熬夜
- 备选达人：撒铁的面包

选择逻辑见 `report.md` 和 `references/creator-research.md`。

## 本地运行

推荐方式：双击根目录下的 `启动网页.vbs`，脚本会在后台启动 `server.py` 并自动打开本地页面。

停止服务：双击 `停止网页服务.bat`。

命令行备选：

```bash
python server.py
```

打开：

- `http://127.0.0.1:4173/analyze`
- `http://127.0.0.1:4173/analyze/9423b9da6e66`

页面包含商单 brief 输入、视频分析结果、风格仿写、分镜拆解、历史记录和飞书写入入口。

## 视频分析能力

如果要从小红书视频链接或本地视频生成结构化拆解结果，需要安装视频下载和转码依赖：

```bash
pip install videofetch
```

同时需要安装 FFmpeg，或使用项目里的 `tools/ffmpeg/bin/ffmpeg.exe`。

从链接分析：

```bash
python scripts/analyze_video.py --url "你有权限分析的小红书视频链接" --transcribe --ocr
```

从本地视频分析：

```bash
python scripts/analyze_video.py --video "C:\path\to\video.mp4" --transcribe --ocr
```

生成结果会进入 `results/<id>/analysis.json`，页面可通过 `/analyze/<id>` 查看。

### 自定义模型

默认会优先使用 `custom_model.txt` 中配置的 OpenAI 兼容模型；如果没有这个文件，才使用 `豆包信息.txt` 中的 Ark 配置。

先复制模板：

```bash
copy custom_model.example.txt custom_model.txt
```

然后填写：

```text
CUSTOM_BASE_URL=https://你的模型服务地址/v1
CUSTOM_API_KEY=你的 API Key
CUSTOM_MODEL=你的模型名
CUSTOM_RESPONSE_FORMAT=json
```

说明：

- 接口需要兼容 `/chat/completions`。
- 如果服务商不支持 `response_format`，把 `CUSTOM_RESPONSE_FORMAT=false`。
- `custom_model.txt` 已加入 `.gitignore`，不要提交真实密钥。

命令行也可以显式指定：

```bash
python scripts/analyze_video.py --url "你有权限分析的小红书视频链接" --provider custom --analysis-mode video
```

已有结果重试：

```bash
python scripts/retry_ai_analysis.py --id <result_id> --provider custom --analysis-mode video
```

## 飞书文档接入

飞书写入脚本位于 `feishu/write_doc.py`。需要先在飞书开放平台创建企业自建应用，并配置：

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- 可选：`FEISHU_FOLDER_TOKEN`
- 可选：`FEISHU_DOC_TITLE`
- 可选：`FEISHU_DOC_BASE_URL`，例如 `https://你的企业.feishu.cn`。如果不配置，脚本只能输出 `https://feishu.cn/docx/<document_id>` 作为参考地址，部分租户环境可能打不开。

可以写入默认交付内容：

```bash
python feishu/write_doc.py
```

也可以把某次视频分析结果写入飞书：

```bash
python feishu/write_doc.py --id <result_id>
```

脚本会自动创建飞书文档、写入 Markdown 结构化内容，并输出 `document_id` 和参考访问地址。当前最终写入记录：

- 文档标题：轻醒酸奶小红书商单脚本-20260524最终版-优化排版
- Document ID：`TJZtdKLAmoMSBSxjnXvc892ynXg`
- 参考地址：`https://feishu.cn/docx/TJZtdKLAmoMSBSxjnXvc892ynXg`
- 写入证明：`feishu/write-proof.md`

飞书 Open API 创建文档接口返回的是 `document_id`，不返回租户内真实可访问 URL。若参考地址因租户域名、登录状态或权限限制无法打开，以 `feishu/write-proof.md` 和 `feishu/integration.md` 中的写入记录作为兜底证明。

如果知道当前企业的飞书域名，可在本地环境或 `信息.txt` 中补充 `FEISHU_DOC_BASE_URL=https://你的企业.feishu.cn`，再重新运行 `python feishu/write_doc.py`，脚本会输出对应租户域名下的参考地址。

## 使用的 AI 工具

- Codex：项目实现、文件整理、工作流和交付材料沉淀。
- 通义千问 Qwen3-VL-Plus：通过 OpenAI 兼容接口接入，用于视频帧、封面、字幕、口播和风格拆解。
- 视觉/多模态模型接口：用于参考视频的画面理解、关键帧分析和内容结构化。
- 本地 Whisper 可选能力：用于口播转写。
- Tesseract OCR 可选能力：用于画面字幕识别。

## 风险控制

脚本生成和质检会规避：

- 减肥、瘦身、掉秤、燃脂
- 治疗、改善疾病、降糖、药效
- 100%、一定有效、必爆等绝对化表达
- 夸大前后对比
- 复制达人原句或镜头

详细质检结果见 `output/quality-check.md`。
