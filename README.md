# 书脉 AI · 古籍阅读工作台

把古籍变成可交互的知识地图。Flask 后端 + 单页前端（前后端同源部署）。

## 本地运行

```bash
pip install -r requirements.txt
python server.py
# 打开 http://localhost:5001/
```

如需 AI 活化（生成深度注释），配置环境变量：

```bash
# Windows PowerShell
$env:DEEPSEEK_API_KEY="你的key"

# Linux / macOS
export DEEPSEEK_API_KEY=你的key
```

未配置 Key 时，已缓存的书仍可正常活化；书架上的 3 本预置书（山海经 / 世说新语 / 天工开物）无需后端即可阅读。

## 部署到 Railway

### 1. 推送到 GitHub
新建仓库，将本项目全部文件提交并推送。

### 2. 在 Railway 创建项目
- 登录 railway.com → New Project → Deploy from GitHub repo → 选择仓库
- Railway 会自动识别为 Python 项目并安装 `requirements.txt`

### 3. 配置环境变量
在服务的 Variables 标签添加：
- `DEEPSEEK_API_KEY` = 你的 DeepSeek Key

### 4. 配置持久化（重要）
为避免重启丢失 AI 活化缓存，给 `cache/` 目录挂载一个 Volume：
- Settings → Volumes → Add Volume
- Mount path 填 `/app/cache`（Railway 默认部署在 /app）
- 1 GB 足够（约 $0.15/月，在 Hobby $5 额度内）

### 5. 生成域名
Settings → Networking → Generate Domain，获得 `xxx.up.railway.app` 公网地址，即可访问。

## 项目结构

```
├── server.py          # Flask 后端：ctext/DeepSeek API、SSE 流式活化、静态托管
├── index.html         # 单页前端（192KB，含样式与逻辑）
├── requirements.txt   # Python 依赖
├── runtime.txt        # Python 版本
├── Procfile           # 启动命令（gunicorn）
├── railway.json       # Railway 部署配置（含健康检查）
├── texts/             # 83 本本地古籍原文 JSON
├── cache/             # AI 活化结果缓存（部署时建议挂载 Volume）
├── data/              # 书架 3 本预置书的静态数据
└── assets/            # 封面图
```

## 说明

- 前后端同源：`server.py` 同时提供 API（`/api/*`）和静态页面，前端用相对路径请求，部署后无需额外配置 API 地址。
- AI 活化使用 DeepSeek（deepseek-chat），单本书约调用 20+ 次生成实体提取、深度注释与知识关系。
