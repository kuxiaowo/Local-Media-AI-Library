from app.core.path_utils import path_has_prefix


def test_path_prefix_requires_directory_boundary() -> None:
    assert path_has_prefix("D:/Photos/School/a.jpg", "D:/Photos")
    assert not path_has_prefix("D:/Photos2/a.jpg", "D:/Photos")
