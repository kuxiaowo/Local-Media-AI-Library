local LrTasks = import "LrTasks"
local LrPathUtils = import "LrPathUtils"
local LrDialogs = import "LrDialogs"
local function run(action)
    if _G.localmediaBusy then LrDialogs.message("LocalMedia", "已有操作正在执行。", "info"); return end
    _G.localmediaBusy = true
    LrTasks.startAsyncTask(function()
        local ok, message = LrTasks.pcall(function()
            local bridge = dofile(LrPathUtils.child(_PLUGIN.path, "Bridge.lua"))
            local client = bridge.new(dofile(LrPathUtils.child(_PLUGIN.path, "Config.lua")))
            if action == "normal" or action == "alternate" then
                client.launch("--switch", action, true)
                return
            end
            client.start()
            if action == "settings" then (import "LrHttp").openUrlInBrowser(client.item.url); return end
            if action == "search" then
                local query
                (import "LrFunctionContext").callWithContext("LocalMediaSearch", function(context)
                    local properties = (import "LrBinding").makePropertyTable(context)
                    properties.query = ""
                    local f = (import "LrView").osFactory()
                    local result = LrDialogs.presentModalDialog {
                        title = "LocalMedia AI 搜索", actionVerb = "搜索",
                        contents = f:column { bind_to_object = properties,
                            f:static_text { title = "用自然语言描述要寻找的照片" },
                            f:edit_field { value = (import "LrView").bind("query"), width_in_chars = 55 },
                        },
                    }
                    if result == "ok" then query = properties.query end
                end)
                if not query or query == "" then return end
                local photos = client.search(query)
                local collection
                client.catalog:withWriteAccessDo("LocalMedia AI 搜索结果", function()
                    local parent = client.catalog:createCollectionSet("LocalMedia AI", nil, true)
                    collection = client.catalog:createCollection("搜索结果", parent, true)
                    collection:removeAllPhotos()
                    if #photos > 0 then collection:addPhotos(photos) end
                end)
                client.catalog:setActiveSources({ collection })
                return "找到 " .. tostring(#photos) .. " 张照片，已显示在 LocalMedia AI / 搜索结果集合中。"
            end
            assert(client.catalog:getTargetPhoto(), "请先选择照片。")
            local photos = client.catalog:getTargetPhotos()
            assert(#photos > 0, "请先选择照片。")
            if action == "analyze" then
                local cloud = client.request("policy") == "cloud"
                if cloud and LrDialogs.confirm("分析所选照片", "当前 AI 配置使用云端服务，将发送所选照片的编辑后预览。", "继续") ~= "ok" then return end
                local count = 0
                for _, photo in ipairs(photos) do
                    if not photo:getRawMetadata("isVideo") then
                        local result = client.analyze(photo, cloud)
                        count = count + (tonumber(string.match(result, "\n(%d+)$")) or 0)
                    end
                end
                return "已提交 " .. tostring(count) .. " 张照片。完成后使用“读取 AI 描述”，或打开“AI 设置与任务”查看进度。"
            end
            local updates = {}
            for _, photo in ipairs(photos) do
                local state, text = client.description(photo)
                if state == "done" then updates[#updates + 1] = { photo, text } end
            end
            client.catalog:withWriteAccessDo("读取 LocalMedia AI 描述", function()
                for _, update in ipairs(updates) do update[1]:setPropertyForPlugin(_PLUGIN, "description", update[2]) end
            end)
            return "已更新 " .. tostring(#updates) .. " 张照片的 LocalMedia AI 描述字段。"
        end)
        _G.localmediaBusy = false
        if not ok or message then LrDialogs.message("LocalMedia", tostring(message), ok and "info" or "critical") end
    end)
end
return run
