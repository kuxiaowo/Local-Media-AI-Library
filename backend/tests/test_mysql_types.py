import uuid

from app.models.types import GUID


def test_guid_binds_uuid_as_char_36() -> None:
    value = uuid.uuid4()
    assert GUID().process_bind_param(value, None) == str(value)


def test_guid_restores_uuid_from_string() -> None:
    value = uuid.uuid4()
    restored = GUID().process_result_value(str(value), None)
    assert restored == value
