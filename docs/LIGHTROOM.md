# Lightroom 普通目录 / 完整目录（Windows 试验版）

| 状态 | 打开的目录 | 可见内容 |
| --- | --- | --- |
| 隐藏 | 普通目录 | 只有普通照片 |
| 显示 | 完整目录 | 普通照片和隐藏范围内的照片一起显示 |

两个 Catalog 共用磁盘上的原片。完整目录保留全部记录，普通目录是排除指定路径后的子集；目录名、预览和记录不会作为隐藏范围的照片出现在普通目录中。这是日常显示隔离，不是加密，也不会关闭已有浏览器页面或抹除历史备份。

## 日常操作

在 LR 图库模块的顶部菜单“图库 → 增效工具额外信息”中使用：

- **隐藏指定照片（普通目录）**：通过 SDK 选择普通照片，用 LR 的“导出为目录”生成新的普通目录；核对范围后正常关闭完整目录，保留上一份普通目录备份，再打开新普通目录。
- **显示全部照片（完整目录）**：保存并关闭普通目录，打开完整目录；必要时通过 LR 的“从另一个目录导入”同步普通照片的编辑。完整目录在普通模式期间若有独立变化，会拒绝自动覆盖，要求先处理冲突。

也可以双击配置旁生成的“图库切换.vbs”“隐藏指定照片.vbs”“显示全部照片.vbs”。面板显示“已交给 Lightroom”仅表示已提交命令；切换成功以实际打开的目录为准，详细结果写入配置旁的 last-switch.json。

请在**完整目录**中导入需要隐藏的照片。配置一个隐藏路径不会自动导入该路径下尚未收录的文件。未导入隐藏照片时，两种模式的照片数量相同。

原生导出不复制负片文件、不构建智能预览，也不复制现有预览缓存，避免预览缓存导出失败阻断切换；普通目录的缩略图由 LR 按需重建。原生合并选择“仅元数据与修改照片设置”，不会复制或替换原片。切换器不直接改写 LR 的 SQLite 数据库。LR 按计划要求备份时，程序选择执行备份，等待其正常结束；不会跳过计划备份或强杀 LR。

## 首次配置

先备份原目录。建议复制完整目录及同名 .lrcat-data 到专用管理文件夹，在 LR 中升级副本，保留原目录作为回退。用 LR 原生“导出为目录”把普通照片导出为初始普通目录，保留记录、快照和编辑，不复制原片。

以下是本机配置示例，必须使用绝对路径。真实配置和连接令牌不要提交到 Git：

```json
{
  "lightroom_executable": "C:/Apps/Adobe Lightroom Classic/Lightroom.exe",
  "catalog_mode": "normal_full",
  "catalog_workspace": "D:/Libraries/LocalMedia",
  "normal_catalog": "D:/Libraries/LocalMedia/Normal/Normal.lrcat",
  "full_catalog": "D:/Libraries/LocalMedia/Full/Full.lrcat",
  "hidden_roots": ["D:/PrivatePhotos"]
}
```

激活项目 Conda 环境、安装项目依赖后运行：

```powershell
python tools/lightroom/setup.py --config D:/Libraries/LocalMedia/settings.lightroom.json
```

初始化会检查普通目录确实是完整目录的普通照片子集，核对编辑记录，并生成 LocalMedia.lrplugin、启动入口和本机 AI 配置。在 LR 的“文件 → 增效工具管理器 → 添加”中注册生成的插件一次。旧版 alternate_catalog 配置仍兼容；新模式用 full_catalog，两个字段同时存在时必须指向同一文件。

普通目录必须位于 catalog_workspace 的专用子文件夹中。上一代普通目录、合并前的完整目录和未成功发布的导出保留在管理区域中；没有自动删除备份的任务，空间不足时会失败并保留原目录。

## AI

AI 分析所选照片、读取 AI 描述、AI 搜索、AI 设置与任务仍在同一个 LR 菜单中。按当前 Catalog 启动独立的本机 LocalMedia 服务，仅监听 127.0.0.1。LR 导出当前编辑效果供分析，UUID 区分原片和虚拟副本；云端分析沿用明确确认流程。读取结果写入专用长文本字段，不覆盖标题、说明、关键词或显影参数。

两套 AI 数据库和向量索引目前独立，切换目录不会自动合并 AI 索引。要在完整目录中搜索所有照片，需要在完整模式分析相应照片。LR 桥接当前支持照片，视频仍通过现有 LocalMedia 界面处理。

## 验证与限制

已在 Windows / Lightroom Classic 15.6 中文界面验证：完整测试目录 27 个条目，普通目录 2 个条目，隐藏部分 25 个条目；共享照片 UUID 和原片引用一致。普通目录中的曝光修改、五星评分、快照和显影历史通过 LR 原生合并带回完整目录。快照的内部标识可能由 LR 重新生成，验证比较内容和名称，不要求其内部 ID 不变。

测试目录的一次自动隐藏约 21 秒、显示约 14 秒。耗时受目录大小、LR 启停、导出和计划备份影响；这些数字不代表大型目录性能。每次隐藏当前会重新生成普通目录，尚未实现按照片增量导出。

自动检查覆盖范围隔离、路径边界、共享身份、正常关闭、发布失败恢复、历史/快照内容比对，以及仅在确认没有隐藏照片时接受 LR 的“全部导出”界面。仅核对过相应字段，未独立验收 LR 自带 AI 蒙版的全部类型、发布服务、其他插件元数据及所有 LR 版本。遇到未识别的原生对话框会停止，而不会猜测按钮。

```powershell
python -m pytest tests_localmedia -q
```

SDK 依据：[LrCatalog](https://lrc.mcor.dev/modules/LrCatalog.html)、[LrPhoto](https://lrc.mcor.dev/modules/LrPhoto.html)；目录合并使用 [Adobe 原生目录管理流程](https://helpx.adobe.com/lightroom-classic/desktop/manage-catalogs-and-files/create-catalogs.html)。
