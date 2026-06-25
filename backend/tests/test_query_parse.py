from app.models.schemas import SearchRequest
from app.services.search_service import _select_embedding_model, parse_query_filters


def test_parse_video_media_type_from_chinese_query() -> None:
    query = "找学校活动的视频"
    parsed = parse_query_filters(SearchRequest(query=query))
    assert parsed.media_type == "video"
    assert parsed.semantic_query == query


def test_parse_image_media_type_from_chinese_query() -> None:
    parsed = parse_query_filters(SearchRequest(query="海边日落照片"))
    assert parsed.media_type == "image"


class EmptySession:
    def scalar(self, statement):
        return None


def test_select_embedding_model_uses_global_default() -> None:
    assert _select_embedding_model(EmptySession()) == "nomic-embed-text"
