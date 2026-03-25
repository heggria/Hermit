"""Tests for kernel/ledger/journal/store_support.py — utility functions."""

from __future__ import annotations

from collections import OrderedDict

from hermit.kernel.ledger.journal.store_support import (
    canonical_json,
    canonical_json_from_raw,
    json_loads,
    sha256_hex,
    sqlite_dict,
    sqlite_int,
    sqlite_list,
    sqlite_optional_float,
    sqlite_optional_text,
)


class TestJsonLoads:
    def test_valid_json(self) -> None:
        assert json_loads('{"key": "value"}') == {"key": "value"}

    def test_empty_string(self) -> None:
        assert json_loads("") == {}

    def test_none(self) -> None:
        assert json_loads(None) == {}

    def test_invalid_json(self) -> None:
        assert json_loads("not json") == {}


class TestCanonicalJson:
    def test_sorts_keys(self) -> None:
        result = canonical_json({"b": 2, "a": 1})
        assert result == '{"a":1,"b":2}'

    def test_no_spaces(self) -> None:
        result = canonical_json({"key": "value"})
        assert " " not in result

    def test_unicode_not_escaped(self) -> None:
        result = canonical_json({"key": "value"})
        assert "\\u" not in result


class TestCanonicalJsonFromRaw:
    def test_valid_json_string(self) -> None:
        raw = '{"b": 1, "a": 2}'
        result = canonical_json_from_raw(raw)
        assert result == '{"a":2,"b":1}'

    def test_none_returns_empty_dict(self) -> None:
        result = canonical_json_from_raw(None)
        assert result == canonical_json({})

    def test_empty_string_returns_empty_dict(self) -> None:
        result = canonical_json_from_raw("")
        assert result == canonical_json({})

    def test_invalid_json_returns_canonical_of_string(self) -> None:
        result = canonical_json_from_raw("not json")
        assert result == canonical_json("not json")


class TestSha256Hex:
    def test_string_input(self) -> None:
        result = sha256_hex("hello")
        assert len(result) == 64
        # Known SHA256 of "hello"
        assert result == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

    def test_bytes_input(self) -> None:
        result = sha256_hex(b"hello")
        assert result == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

    def test_empty_string(self) -> None:
        result = sha256_hex("")
        assert len(result) == 64


class TestSqliteOptionalText:
    def test_none_returns_none(self) -> None:
        assert sqlite_optional_text(None) is None

    def test_string_returned(self) -> None:
        assert sqlite_optional_text("hello") == "hello"

    def test_int_converted(self) -> None:
        assert sqlite_optional_text(42) == "42"

    def test_float_converted(self) -> None:
        assert sqlite_optional_text(3.14) == "3.14"

    def test_bool_converted(self) -> None:
        assert sqlite_optional_text(True) == "True"

    def test_other_type_returns_default(self) -> None:
        assert sqlite_optional_text([1, 2]) is None
        assert sqlite_optional_text([1, 2], default="fallback") == "fallback"


class TestSqliteOptionalFloat:
    def test_none_returns_none(self) -> None:
        assert sqlite_optional_float(None) is None

    def test_int_converted(self) -> None:
        assert sqlite_optional_float(42) == 42.0

    def test_float_returned(self) -> None:
        assert sqlite_optional_float(3.14) == 3.14

    def test_bool_converted(self) -> None:
        assert sqlite_optional_float(True) == 1.0
        assert sqlite_optional_float(False) == 0.0

    def test_other_type_returns_default(self) -> None:
        assert sqlite_optional_float("not a number") is None
        assert sqlite_optional_float("x", default=99.9) == 99.9


class TestSqliteInt:
    def test_int_returned(self) -> None:
        assert sqlite_int(42) == 42

    def test_string_int_converted(self) -> None:
        assert sqlite_int("10") == 10

    def test_invalid_returns_default(self) -> None:
        assert sqlite_int("xyz") == 0
        assert sqlite_int("xyz", default=5) == 5

    def test_none_returns_default(self) -> None:
        assert sqlite_int(None) == 0

    def test_minimum_applied(self) -> None:
        assert sqlite_int(-5, minimum=0) == 0
        assert sqlite_int(3, minimum=0) == 3
        assert sqlite_int(3, minimum=5) == 5

    def test_minimum_with_default(self) -> None:
        assert sqlite_int(None, default=-1, minimum=0) == 0


class TestSqliteDict:
    def test_dict_returned(self) -> None:
        assert sqlite_dict({"a": 1}) == {"a": 1}

    def test_ordered_dict_converted(self) -> None:
        result = sqlite_dict(OrderedDict([("b", 2), ("a", 1)]))
        assert isinstance(result, dict)
        assert result == {"b": 2, "a": 1}

    def test_non_mapping_returns_default(self) -> None:
        assert sqlite_dict("not a dict") == {}
        assert sqlite_dict("x", default={"fallback": True}) == {"fallback": True}

    def test_none_returns_empty(self) -> None:
        assert sqlite_dict(None) == {}


class TestSqliteList:
    def test_list_returned(self) -> None:
        assert sqlite_list([1, 2, 3]) == [1, 2, 3]

    def test_tuple_converted(self) -> None:
        assert sqlite_list((1, 2)) == [1, 2]

    def test_string_not_treated_as_sequence(self) -> None:
        assert sqlite_list("hello") == []

    def test_bytes_not_treated_as_sequence(self) -> None:
        assert sqlite_list(b"hello") == []

    def test_none_returns_empty(self) -> None:
        assert sqlite_list(None) == []

    def test_non_sequence_returns_default(self) -> None:
        assert sqlite_list(42, default=[99]) == [99]
