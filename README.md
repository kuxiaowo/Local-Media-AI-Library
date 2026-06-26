# Local Media AI Library

本地 AI 媒体记忆库。首轮 MVP 面向 Windows，本机运行后端和 Web UI，扫描图片和视频目录，生成缩略图，调用本地 Ollama 视觉模型生成摘要，再用 Ollama embedding + MySQL JSON 向量做语义搜索。

## 当前范围

- 已实现：目录规则、图片/视频扫描、EXIF/视频 creation_time/文件时间提取、图片和视频缩略图、视频抽帧、关键帧识别、视频级摘要、Ollama 图片 JSON 分析、embedding、媒体浏览、详情页、AI 搜索、任务队列。
- 暂不实现：Tauri 桌面端、账号系统、多用户权限、时间范围总结、SQLite + FAISS。
- 原始媒体只读使用，不复制进项目，不删除、不移动。

## 系统要求

- Windows 10/11
- Python 3.11+
- Node.js 20+
- MySQL 8.0 或 8.4 LTS
- Ollama
- ffmpeg/ffprobe，视频缩略图、元数据读取和抽帧需要加入 PATH。

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
6. 在“媒体浏览”打开图片或视频详情；视频详情会显示关键帧。
7. 在“AI 搜索”输入自然语言查询。

## 视频抽帧设置

目录规则里可以配置视频抽帧参数：

- `抽帧策略`：`hybrid`、`fixed_interval` 或 `scene`。
- `抽帧间隔秒`：固定间隔抽帧的秒数。
- `最大帧数`：单个视频最多保留多少个关键帧。
- `关键帧最大宽度`：抽出的关键帧图片最大宽度，默认 `1280`；原视频更小时不会放大。
- `关键帧高度`：可为空；为空时按宽度保持比例，填写后按固定宽高缩放。
- `每批帧数`：递进式 AI 识别时每次发送给视觉模型的关键帧数量，默认 `6`。
- `相邻批次重叠帧数`：相邻批次复用的关键帧数量，默认 `1`，用于保持片段连续性。

修改这些设置后，已完成分析的受影响媒体会被标记为 `needs_reanalysis`，需要重新分析才会使用新抽帧参数。
视频分析会按批处理关键帧：每批只投喂当前批次图片和上一批全局记忆，生成当前片段摘要、重要观察、更新后的全局记忆和不确定点；最终总结只基于文本化分段结果生成概括、详细描述和时间线，不会一次性把所有帧塞进模型上下文。

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

图片分析不依赖 ffmpeg；视频元数据、缩略图和抽帧依赖 ffmpeg/ffprobe。安装后确认命令可用：

```powershell
ffmpeg -version
ffprobe -version
```

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
