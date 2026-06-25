# Local Media AI Library

本地 AI 媒体记忆库。首轮 MVP 面向 Windows，本机运行后端和 Web UI，扫描图片目录，生成缩略图，调用本地 Ollama 视觉模型生成摘要，再用 Ollama embedding + MySQL JSON 向量做语义搜索。

## 当前范围

- 已实现：目录规则、图片扫描、EXIF/文件时间提取、缩略图、Ollama 图片 JSON 分析、embedding、媒体浏览、详情页、AI 搜索、任务队列。
- 暂不实现：Tauri 桌面端、账号系统、多用户权限、完整视频分析、时间范围总结、SQLite + FAISS。
- 原始媒体只读使用，不复制进项目，不删除、不移动。

## 系统要求

- Windows 10/11
- Python 3.11+
- Node.js 20+
- MySQL 8.0 或 8.4 LTS
- Ollama
- 可选：ffmpeg，首轮图片 MVP 不强依赖，后续视频支持会用到。

## MySQL

安装 MySQL Community Server 后，用管理员账号登录：

```sql
mysql -u root -p
```

创建数据库和用户：

```sql
CREATE DATABASE media_ai CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'media_ai'@'localhost' IDENTIFIED BY 'media_ai';
GRANT ALL PRIVILEGES ON media_ai.* TO 'media_ai'@'localhost';
FLUSH PRIVILEGES;
```

确认 `backend/.env` 中的连接串为：

```env
DATABASE_URL=mysql+pymysql://media_ai:media_ai@localhost:3306/media_ai?charset=utf8mb4
```

如果你的 MySQL 密码不同，只改连接串里的第二个 `media_ai`。

## Ollama

安装 Ollama 后，按需拉取模型。示例：

```powershell
ollama pull qwen2.5vl:7b
ollama pull qwen3:8b
ollama pull nomic-embed-text
```

系统不会自动下载大模型。目录规则里填写的模型必须已经在本机 Ollama 中存在。

## 后端启动

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

也可以在项目根目录直接运行调试脚本：

```powershell
python run_backend.py
```

这个脚本会先自动执行一次 `alembic upgrade head`，再启动后端。

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

## 前端启动

```powershell
cd frontend
npm install
npm run dev
```

也可以在项目根目录直接运行调试脚本：

```powershell
python run_frontend.py
```

如果用 conda 环境并且 IDE 里提示找不到 `node`，可以在该 conda 环境安装 Node.js：

```powershell
conda install -c conda-forge nodejs
```

或者把本机 Node.js 安装目录加入 IDE 的 PATH。

浏览器打开：

```text
http://127.0.0.1:5173
```

## 基本流程

1. 打开 Web UI。
2. 在“媒体库设置”添加目录，例如 `D:/Photos`。
3. 填写本机已有的 Ollama 模型。
4. 点击扫描。
5. 在“扫描任务”查看队列。
6. 在“媒体浏览”打开图片详情。
7. 在“AI 搜索”输入自然语言查询。

## 常见问题

### Ollama 连不上

确认 Ollama 正在运行：

```powershell
ollama list
```

默认地址是 `http://localhost:11434`，可在 `backend/.env` 修改 `OLLAMA_BASE_URL`。

### 模型不存在

后端会保存任务错误。手动拉取缺失模型：

```powershell
ollama pull <model>
```

### MySQL 连不上

检查 `DATABASE_URL`、数据库名、用户名、密码和端口。确认服务正在运行：

```powershell
Get-Service *mysql*
```

如果 MySQL 8 使用默认的 `caching_sha2_password` 认证，后端依赖里已经包含 `cryptography`，避免 PyMySQL 认证时报错。

### HEIC 不支持

首轮会识别 `.heic/.heif`，但不生成预览和 AI 分析，会标记为失败并保留错误信息。

### ffmpeg 不存在

图片 MVP 不依赖 ffmpeg。后续视频抽帧需要安装 ffmpeg 并加入 PATH。

## 测试

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
pytest
```

前端构建：

```powershell
cd frontend
npm run build
```
