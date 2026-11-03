local LrApplication = import "LrApplication"
local LrTasks = import "LrTasks"
local LrHttp = import "LrHttp"
local LrPathUtils = import "LrPathUtils"
local LrFileUtils = import "LrFileUtils"
local LrExportSession = import "LrExportSession"
local M = {}
function M.encode(text)
    return (string.gsub(tostring(text), "([^%w%-_%.~])", function(c) return string.format("%%%02X", string.byte(c)) end))
end
function M.decode(text)
    return (string.gsub(text, "%%(%x%x)", function(c) return string.char(tonumber(c, 16)) end))
end
local function quote(value)
    assert(not string.find(value, '["%%!\r\n]'), "启动路径含不支持的命令字符。")
    return '"' .. value .. '"'
end
function M.new(config)
    local catalog = LrApplication.activeCatalog()
    local key = string.lower(string.gsub(catalog:getPath(), "\\", "/"))
    local item = assert(config.catalogs[key], "当前图库未配置，请先运行 LocalMedia 的 setup.py。")
    assert(string.match(item.url, "^http://127%.0%.0%.1:%d+$"), "AI 服务必须使用本机地址。")
    local client = { catalog = catalog, item = item }
    function client.launch(action, mode, detach, prepared)
        local command = quote(config.python) .. " " .. quote(config.launcher) .. " --config " .. quote(config.config)
            .. " " .. action .. " " .. (mode or item.mode) .. (detach and " --detach" or "") .. (prepared and " --prepared" or "")
        assert(LrTasks.execute('"' .. command .. '"') == 0, "后台服务启动失败，请检查本地配置和 service.log。")
    end
    function client.prepareNormal()
        local function normal(path)
            return string.lower(string.gsub(path, "\\", "/")):gsub("/+$", "")
        end
        assert(config.hiddenRoots and #config.hiddenRoots > 0, "尚未配置隐藏范围。")
        local selected = {}
        for _, photo in ipairs(catalog:getAllPhotos()) do
            local path, hidden = normal(photo:getRawMetadata("path")), false
            for _, root in ipairs(config.hiddenRoots) do
                root = normal(root)
                if path == root or string.sub(path, 1, #root + 1) == root .. "/" then hidden = true; break end
            end
            if not hidden then selected[#selected + 1] = photo end
        end
        assert(#selected > 0, "普通目录没有照片，无法导出普通目录。")
        catalog:setActiveSources({ catalog.kAllPhotos })
        catalog:setViewFilter({})
        -- Source changes are reflected on a later UI tick. Selecting immediately
        -- can be overwritten by LR's own source-change selection update.
        LrTasks.sleep(0.3)
        catalog:setSelectedPhotos(selected[1], selected)
        LrTasks.sleep(0.3)
        assert(#catalog:getTargetPhotos() == #selected, "普通照片选择尚未完成，请稍后重试；没有导出目录。")
        return #selected
    end
    function client.request(path, body)
        assert(LrApplication.activeCatalog():getPath() == catalog:getPath(), "图库已切换。")
        local headers = {
            { field = "X-LocalMedia-Token", value = item.token },
            { field = "X-LocalMedia-Catalog", value = item.catalog_id },
            { field = "Content-Type", value = body and "image/jpeg" or "text/plain" },
        }
        local result, info
        if body then result, info = LrHttp.post(item.url .. "/api/lightroom/" .. path, body, headers, "POST", 30)
        else result, info = LrHttp.get(item.url .. "/api/lightroom/" .. path, headers, 30) end
        assert(result and string.sub(result, 1, 3) == "ok\n", "AI 请求未完成：" .. string.sub(result or "服务无法连接", 1, 500))
        return string.sub(result, 4)
    end
    function client.start()
        client.launch("--ai")
        assert(client.request("health") == item.catalog_id, "AI 图库身份不匹配。")
    end
    function client.upload(photo)
        assert(not photo:getRawMetadata("isVideo"), "当前 LR 桥接先支持照片；视频仍可在 LocalMedia 中分析。")
        local id = photo:getRawMetadata("uuid")
        local directory = LrPathUtils.child(LrPathUtils.getStandardFilePath("temp"), "LocalMedia-" .. id)
        LrFileUtils.createAllDirectories(directory)
        local session = LrExportSession {
            photosToExport = { photo },
            exportSettings = {
                LR_export_destinationType = "specificFolder", LR_export_destinationPathPrefix = directory,
                LR_export_useSubfolder = false, LR_format = "JPEG", LR_jpeg_quality = 0.8,
                LR_export_colorSpace = "sRGB", LR_collisionHandling = "overwrite",
                LR_size_doConstrain = true, LR_size_resizeType = "longEdge", LR_size_units = "pixels",
                LR_size_maxHeight = 1600, LR_size_maxWidth = 1600, LR_size_resolution = 72,
                LR_minimizeEmbeddedMetadata = true, LR_removeLocationMetadata = true,
                LR_renamingTokensOn = false, LR_reimportExportedPhoto = false,
                LR_export_postProcessing = "doNothing",
            },
        }
        local rendered
        local ok, result = LrTasks.pcall(function()
            for _, rendition in session:renditions({ stopIfCanceled = true }) do
                local success, path = rendition:waitForRender()
                assert(success, path or "照片预览导出失败。")
                rendered = path
                local filename = LrPathUtils.leafName(photo:getRawMetadata("path"))
                return client.request("ingest?photo=" .. M.encode(id) .. "&filename=" .. M.encode(filename), LrFileUtils.readFile(path))
            end
            error("没有生成可分析的预览。")
        end)
        if rendered then LrFileUtils.delete(rendered) end
        -- Only our now-empty temporary folder; never recursively delete paths.
        LrTasks.pcall(function() LrFileUtils.delete(directory) end)
        assert(ok, result)
        return id
    end
    function client.analyze(photo, cloud)
        local id = client.upload(photo)
        return client.request("analyze?photo=" .. M.encode(id) .. "&cloud_ack=" .. tostring(cloud == true), "")
    end
    function client.description(photo)
        local result = client.request("description?photo=" .. M.encode(photo:getRawMetadata("uuid")))
        local state, text = string.match(result, "^([^\n]+)\n(.*)$")
        assert(state ~= "failed", M.decode(text or "AI 分析失败，请查看任务列表。"))
        return state, M.decode(text or "")
    end
    function client.search(query)
        local result, photos = client.request("search?q=" .. M.encode(query)), {}
        for line in string.gmatch(result, "[^\n]+") do
            local id = string.match(line, "^([^\t]+)")
            local photo = catalog:findPhotoByUuid(string.upper(id)) or catalog:findPhotoByUuid(id)
            if photo then photos[#photos + 1] = photo end
        end
        return photos
    end
    return client
end
return M
