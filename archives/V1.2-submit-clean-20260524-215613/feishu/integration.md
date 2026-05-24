# 飞书接入方案

## 目标
把最终脚本、分镜、质检结果自动写入飞书文档。

## 推荐方案
1. 用脚本生成 Markdown 正文
2. 通过飞书 Open API 创建文档
3. 把 Markdown 转成飞书支持的块结构或富文本
4. 返回 `document_id` 和参考地址到 `README.md`

## 最小实现思路
- 先创建一个飞书应用
- 获取 `tenant_access_token`
- 调用文档创建接口
- 把 Markdown 转成飞书文档块
- 写入标题和正文块
- 保存返回的 `document_id`、参考地址和写入结果
- 如果知道企业飞书域名，可配置 `FEISHU_DOC_BASE_URL=https://你的企业.feishu.cn` 生成更接近真实访问入口的参考地址

## 交付说明
README 里要写清楚：
- 用了什么鉴权方式
- 如何创建文档
- 如何写入正文
- `document_id`、参考地址和写入证明从哪里返回

## 当前状态
已补充 `feishu/write_doc.py` 作为最小可运行脚本。脚本会读取本地交付文件，创建飞书文档，并把正文拆成文档块写入。

如果需要更精细的表格样式，可以继续把 Markdown 表格升级为飞书原生表格块。

## 本次写入结果
- 文档标题：轻醒酸奶小红书商单脚本-20260524最终版-优化排版
- Document ID：`TJZtdKLAmoMSBSxjnXvc892ynXg`
- 参考地址：`https://feishu.cn/docx/TJZtdKLAmoMSBSxjnXvc892ynXg`
- 写入字符数：4179
- 写入块数：169
- 写入证明：`feishu/write-proof.md`

## 排查记录
飞书 Open API 创建文档接口返回的是 `document_id`，不返回租户内真实可访问 URL。本项目最初用 `https://feishu.cn/docx/<document_id>` 拼接参考地址，但该地址在部分浏览器环境会跳转到 `www.feishu.cn` 后返回 404，原因通常是租户域名、登录状态或文档权限不匹配。

处理方式：
- 保留 API 返回的 `document_id`、写入字符数和写入块数作为自动化写入证据。
- 在 `feishu/write-proof.md` 中记录本次写入证明。
- README 中同时写明参考地址和权限限制说明，避免把参考地址误写成一定可公开访问的链接。
