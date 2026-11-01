return {
    schemaVersion = 2,
    metadataFieldsForPhotos = {
        -- LR limits searchable strings to fewer than 512 UTF-8 bytes. Keep the
        -- complete text here; semantic search is provided by our separate index.
        { id = "description", title = "AI 描述", dataType = "string", searchable = false, version = 2 },
    },
}
