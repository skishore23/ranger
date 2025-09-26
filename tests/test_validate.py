import pytest
from core.validate import ensure, json_schema_validator, require_keys


def test_ensure_valid():
    ensure(True, "This should not raise")


def test_ensure_invalid():
    with pytest.raises(ValueError):
        ensure(False, "This should fail")


def test_require_keys_valid():
    data = {"key1": "value1", "key2": "value2"}
    require_keys(data, {"key1", "key2"})


def test_require_keys_invalid():
    data = {"key1": "value1"}
    with pytest.raises(ValueError):
        require_keys(data, {"key1", "key2"})
