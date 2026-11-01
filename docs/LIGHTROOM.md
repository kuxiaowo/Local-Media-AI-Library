# Lightroom 图库切换与 AI 插件（Windows 试验版）

用两套独立 Catalog 分开加载照片。隐藏时正常关闭独立图库并打开普通图库；显示时反向切换。同一份 Catalog 始终保留自己的编辑历史、快照、虚拟副本和预览，无须每次重新导入。

这是显示隔离，不是加密。中性图库名称能减少日常界面的内容暴露，磁盘文件和 LR 最近打开记录仍可能被查看。普通图库里不能同时保留需要隐藏的照片记录，否则那些记录依然可见。

## 准备和使用

1. 先备份并在 LR 中准备两套 Catalog。把需要分开的现有照片迁移为独立 Catalog 是一次性工作；本工具不会自行删除普通图库的照片记录，不会移动或改名原片。
2. 激活项目 Conda 环境，使用项目的 requirements-localmedia.txt 安装依赖。
3. 在本机建立一个忽略提交的 settings.lightroom.json，填写三个绝对路径：

```json
{
  "lightroom_executable": "C:/Apps/Adobe Lightroom Classic/Lightroom.exe",
  "normal_catalog": "D:/Libraries/Library-A/Library-A.lrcat",
  "alternate_catalog": "D:/Libraries/Library-B/Library-B.lrcat"
}
```

4. 运行以下命令。它会在配置旁生成插件副本、两套独立 AI 数据目录以及三个 VBS 启动入口；重复运行保留服务连接令牌和已有 AI 配置。

```powershell
python tools/lightroom/setup.py --config D:/Libraries/settings.lightroom.json
```

5. 在 LR 的“文件 → 增效工具管理器 → 添加”中选择生成的 LocalMedia.lrplugin 文件夹。这是首次注册插件所需的手动步骤，程序不会改写 LR 的插件注册配置。
6. 双击“隐藏独立图库.vbs”或“显示独立图库.vbs”，也可以打开“图库切换.vbs”使用两个按钮。LR 的“图库 → 增效工具额外信息”中有同样的切换命令。

切换只检查并操作已配置的 Lightroom.exe 和两套 Catalog。发现未知图库、多个 LR 进程，或 LR 正在等待保存/备份/退出确认时会停止，不会强制结束进程。首次退出确认和按计划弹出的备份仍需在 LR 中处理。无需管理员权限、加密卷、桌面截图或鼠标键盘模拟。

## AI 功能

- **AI 设置与任务**：按当前 Catalog 启动独立的本机 LocalMedia 服务，打开现有配置和任务界面。两套 Catalog 默认分别使用 18766、18767 端口，只监听 127.0.0.1。先配置描述模型和向量模型；连接令牌与模型凭据仅保存在本机。
- **AI 分析所选照片**：LR 导出最长边 1600 像素、sRGB 的当前编辑效果，再复用项目已有的预览、分析任务和向量索引。按 Catalog 身份和照片 UUID 映射，虚拟副本有独立记录。云端模型沿用明确确认流程。
- **读取所选照片的 AI 描述**：把完成的结果写入插件自己的“AI 描述”字段。在右侧元数据面板选择 LocalMedia AI 标签集查看。不会覆盖标题、说明、关键词或显影参数。
- **AI 搜索**：用自然语言检索当前图库的 AI 索引，把结果显示在 LocalMedia AI / 搜索结果集合中。重复搜索更新这个专用集合，不会删照片。

此次 LR 桥接先支持照片；视频分析继续使用现有 LocalMedia 界面。分析期间切库不会销毁任务，按需启动的 AI 服务可以继续处理该图库自己的数据。普通图库服务不读取另一套 AI 数据库。隐藏不会关闭已经手动打开的浏览器页面。

## 验证和限制

本机 Lightroom Classic 13.4 / Windows 已用 24 张合成照片和 1 个虚拟副本验证三轮往返。关闭期间的 91 个测试图库文件校验一致；SDK 显影参数、渐变蒙版、评分、描述、关键词、24 个快照，以及数据库中的 74 条编辑历史保持一致。

初次三轮测量：隐藏 4.8–5.3 秒，显示 10.6–10.8 秒。该计时到目标进程、Catalog 锁文件和主窗口可用；SDK 状态读取另行等待。样本很小，不能外推到大图库、慢硬盘或后台导出中的图库。

未验证 LR 自带 AI 蒙版模型下载后生成的特殊数据、其他 LR 版本及 macOS；不宣称所有编辑类型已全面覆盖。真实照片未参与这轮测试。

AI 实测使用本机 Qwen3.5 4B 与 BGE-M3：从 LR 导出原片和虚拟副本的编辑后预览，完成描述、向量化、搜索回原来的两个 LR 对象并生成集合。此前 Qwen3-VL 4B 的输出触发长度上限，失败结果没有发布；模型质量与速度仍取决于配置。

完整 AI 描述使用非搜索型插件字段，避免 LR 搜索型字符串“小于 512 字节”的限制。已实际回写并逐字比对一段 541 字符、1481 UTF-8 字节的中文结果，未截断。自然语言搜索由 LocalMedia 向量索引提供。

自动测试：

```powershell
python -m pytest tests_localmedia -q
```

SDK 依据：[LrCatalog](https://lrc.mcor.dev/modules/LrCatalog.html)、[LrExportSession](https://lrc.mcor.dev/modules/LrExportSession.html)、[LrPhoto](https://lrc.mcor.dev/modules/LrPhoto.html)、[LrHttp](https://lrc.mcor.dev/modules/LrHttp.html)。
