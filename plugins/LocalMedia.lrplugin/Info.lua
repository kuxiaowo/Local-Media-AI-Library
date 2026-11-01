return {
    LrSdkVersion = 13.0,
    LrSdkMinimumVersion = 6.0,
    LrToolkitIdentifier = "org.localmedia.lightroom",
    LrPluginName = "LocalMedia AI",
    LrMetadataProvider = "Metadata.lua",
    LrMetadataTagsetFactory = "Tagset.lua",
    LrLibraryMenuItems = {
        { title = "LocalMedia: 隐藏独立图库", file = "Hide.lua" },
        { title = "LocalMedia: 显示独立图库", file = "Show.lua" },
        { title = "LocalMedia: AI 分析所选照片", file = "Analyze.lua" },
        { title = "LocalMedia: 读取所选照片的 AI 描述", file = "Sync.lua" },
        { title = "LocalMedia: AI 搜索", file = "Search.lua" },
        { title = "LocalMedia: AI 设置与任务", file = "Settings.lua" },
    },
    VERSION = { major = 0, minor = 1, revision = 0, build = 2 },
}
