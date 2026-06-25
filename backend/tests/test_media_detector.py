from app.services.media_detector import detect_media_type, image_support_status


def test_detect_supported_image() -> None:
    assert detect_media_type("x.JPG") == "image"
    assert image_support_status("x.webp") == "supported"


def test_detect_heic_as_recognized_unsupported() -> None:
    assert detect_media_type("x.heic") == "image"
    assert image_support_status("x.heif") == "recognized_unsupported"


def test_ignore_unknown_extension() -> None:
    assert detect_media_type("x.txt") is None
