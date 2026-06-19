"""Tests for formvalidate, focused on the interesting validation cases."""

import pytest

import formvalidate as fv
from formvalidate import (
    Schema,
    Field,
    String,
    Int,
    Float,
    Bool,
    Email,
    Choice,
    List,
    Dict,
    Each,
    Optional,
    Invalid,
    ValidationError,
    Result,
)
from formvalidate.core import _MISSING


# --------------------------------------------------------------------------
# Happy paths
# --------------------------------------------------------------------------


def test_valid_signup_form():
    schema = Schema({
        "email": Email(required=True),
        "name": String(min_len=1, max_len=100),
        "age": Int(min=18, max=120),
    })
    result = schema.validate({"email": "alice@example.com", "name": "Alice", "age": 30})
    assert result.valid
    assert result.data == {"email": "alice@example.com", "name": "Alice", "age": 30}


def test_optional_field_missing_is_ok():
    schema = Schema({
        "email": Email(),
        "newsletter": Bool(required=False),
    })
    result = schema.validate({"email": "x@y.com"})
    assert result.valid
    assert "newsletter" not in result.data


def test_optional_field_with_default():
    schema = Schema({
        "email": Email(),
        "newsletter": Bool(required=False, default=False),
    })
    result = schema.validate({"email": "x@y.com"})
    assert result.valid
    assert result.data["newsletter"] is False


def test_blank_string_treated_as_missing():
    schema = Schema({"name": String(min_len=1)})
    result = schema.validate({"name": "   "})
    assert not result.valid
    assert "name" in result.errors


# --------------------------------------------------------------------------
# Collecting every error, not just the first
# --------------------------------------------------------------------------


def test_collects_all_errors():
    schema = Schema({
        "email": Email(),
        "name": String(min_len=2),
        "age": Int(min=18, max=120),
    })
    result = schema.validate({"email": "not-an-email", "name": "A", "age": 5})
    assert not result.valid
    assert set(result.errors) == {"email", "name", "age"}
    assert "valid email" in result.errors["email"]
    assert "at least 2" in result.errors["name"]
    assert "at least 18" in result.errors["age"]


def test_missing_required_reported():
    schema = Schema({"email": Email(), "name": String()})
    result = schema.validate({})
    assert not result.valid
    assert set(result.errors) == {"email", "name"}
    assert result.errors["email"] == "is required"


def test_errors_and_missing_together():
    schema = Schema({"email": Email(), "age": Int(min=18)})
    result = schema.validate({"email": "bad"})
    assert not result.valid
    assert "email" in result.errors
    assert "age" in result.errors
    assert result.errors["age"] == "is required"


# --------------------------------------------------------------------------
# Scalar field behaviours
# --------------------------------------------------------------------------


def test_int_accepts_numeric_string():
    schema = Schema({"age": Int(min=18, max=120)})
    result = schema.validate({"age": "25"})
    assert result.valid
    assert result.data["age"] == 25
    assert isinstance(result.data["age"], int)


def test_int_rejects_garbage():
    schema = Schema({"age": Int()})
    result = schema.validate({"age": "old"})
    assert not result.valid
    assert "integer" in result.errors["age"]


def test_int_rejects_bool():
    schema = Schema({"age": Int()})
    result = schema.validate({"age": True})
    assert not result.valid


def test_float_accepts_int_and_string():
    schema = Schema({"score": Float(min=0, max=10)})
    assert schema.validate({"score": 8}).valid
    assert schema.validate({"score": 8.5}).valid
    assert schema.validate({"score": "8.5"}).valid
    assert schema.validate({"score": "8.5"}).data["score"] == 8.5


def test_float_rejects_bool():
    schema = Schema({"score": Float()})
    result = schema.validate({"score": True})
    assert not result.valid


def test_bool_string_spellings():
    schema = Schema({"active": Bool()})
    for yes in ("true", "True", "1", "yes", "on", "TRUE"):
        assert schema.validate({"active": yes}).data["active"] is True, yes
    for no in ("false", "False", "0", "no", "off", "FALSE"):
        assert schema.validate({"active": no}).data["active"] is False, no
    assert schema.validate({"active": True}).data["active"] is True
    assert schema.validate({"active": False}).data["active"] is False


def test_bool_rejects_garbage():
    schema = Schema({"active": Bool()})
    result = schema.validate({"active": "maybe"})
    assert not result.valid
    assert "true or false" in result.errors["active"]


def test_string_strips_and_checks_length():
    schema = Schema({"name": String(min_len=2, max_len=5)})
    assert schema.validate({"name": "  ab  "}).data["name"] == "ab"
    assert schema.validate({"name": "abcdef"}).errors["name"].startswith("must be at most")
    assert schema.validate({"name": "a"}).errors["name"].startswith("must be at least")


def test_string_choices():
    schema = Schema({"color": String(choices=["red", "green", "blue"])})
    assert schema.validate({"color": "red"}).valid
    result = schema.validate({"color": "purple"})
    assert not result.valid
    assert "red" in result.errors["color"]


def test_string_rejects_non_string():
    schema = Schema({"name": String()})
    result = schema.validate({"name": 42})
    assert not result.valid
    assert "text" in result.errors["name"]


def test_email_valid_and_invalid():
    schema = Schema({"email": Email()})
    for good in ("a@b.com", "alice.smith@example.co.uk", "x@y.io"):
        assert schema.validate({"email": good}).valid, good
    for bad in ("nope", "no@atsign", "@nope.com", "a@b", "a @b.com", "a@b .com"):
        result = schema.validate({"email": bad})
        assert not result.valid, bad
        assert "valid email" in result.errors["email"]


def test_choice_with_ints():
    schema = Schema({"level": Choice([1, 2, 3])})
    assert schema.validate({"level": 2}).valid
    result = schema.validate({"level": 5})
    assert not result.valid
    assert "1" in result.errors["level"]


# --------------------------------------------------------------------------
# Nested structures
# --------------------------------------------------------------------------


def test_nested_dict_valid():
    schema = Schema({
        "name": String(),
        "address": Dict({
            "city": String(),
            "zip": String(min_len=5, max_len=10),
        }),
    })
    result = schema.validate({
        "name": "Alice",
        "address": {"city": "NYC", "zip": "10001"},
    })
    assert result.valid
    assert result.data["address"]["city"] == "NYC"


def test_nested_dict_collects_dotted_errors():
    schema = Schema({
        "address": Dict({
            "city": String(),
            "zip": String(min_len=5),
        }),
    })
    result = schema.validate({"address": {"zip": "12"}})
    assert not result.valid
    assert "address.city" in result.errors
    assert "address.zip" in result.errors
    assert result.errors["address.city"] == "is required"


def test_nested_dict_not_a_dict():
    schema = Schema({"address": Dict({"city": String()})})
    result = schema.validate({"address": "NYC"})
    assert not result.valid
    assert "object" in result.errors["address"]


def test_list_of_strings_valid():
    schema = Schema({"tags": List(String(min_len=1))})
    result = schema.validate({"tags": ["python", "forms"]})
    assert result.valid
    assert result.data["tags"] == ["python", "forms"]


def test_list_reports_per_index_errors():
    schema = Schema({"tags": List(String(min_len=2))})
    result = schema.validate({"tags": ["ok", "x", "also-ok"]})
    assert not result.valid
    assert "tags.1" in result.errors
    assert "tags.0" not in result.errors
    assert "tags.2" not in result.errors


def test_list_length_constraints():
    schema = Schema({"tags": List(String(), min_len=1, max_len=2)})
    assert schema.validate({"tags": ["one"]}).valid
    assert not schema.validate({"tags": []}).valid
    result = schema.validate({"tags": ["a", "b", "c"]})
    assert not result.valid
    assert "at most 2" in result.errors["tags"]


def test_list_not_a_list():
    schema = Schema({"tags": List(String())})
    result = schema.validate({"tags": "python"})
    assert not result.valid
    assert "list" in result.errors["tags"]


def test_each_validates_dict_values():
    schema = Schema({"hours": Each(String())})
    result = schema.validate({"hours": {"mon": "9-5", "tue": "9-5"}})
    assert result.valid

    result = schema.validate({"hours": {"mon": "9-5", "tue": 42}})
    assert not result.valid
    assert "hours.tue" in result.errors


def test_deeply_nested():
    schema = Schema({
        "contacts": List(Dict({
            "name": String(),
            "emails": List(Email()),
        })),
    })
    result = schema.validate({
        "contacts": [
            {"name": "A", "emails": ["a@b.com"]},
            {"name": "B", "emails": ["bad", "c@d.com"]},
        ],
    })
    assert not result.valid
    assert "contacts.1.emails.0" in result.errors
    assert "contacts.0.emails.0" not in result.errors


# --------------------------------------------------------------------------
# Optional wrapper
# --------------------------------------------------------------------------


def test_optional_wrapper():
    schema = Schema({
        "email": Email(),
        "nickname": Optional(String(min_len=2)),
    })
    assert schema.validate({"email": "x@y.com"}).valid
    result = schema.validate({"email": "x@y.com", "nickname": "A"})
    assert not result.valid
    assert "nickname" in result.errors


# --------------------------------------------------------------------------
# Conditional / dependent fields
# --------------------------------------------------------------------------


def test_conditional_required_via_custom_field():
    """A field that's only required when another field has a certain value.

    The library doesn't ship a knob for this -- instead you compose a tiny
    custom Field. This keeps the rule readable and co-located with the
    schema instead of buried in constructor arguments.
    """

    class RequiredIf(Field):
        """Required only when a sibling field equals ``equals``."""

        def __init__(self, field, when, equals):
            super().__init__(required=False)
            self.field = field
            self.when = when
            self.equals = equals

        def _run(self, path, value, result, siblings=None):
            siblings = siblings or {}
            required = siblings.get(self.when) == self.equals
            absent = value is None or value is _MISSING or value == ""
            if absent:
                if required:
                    result.add_error(path, "is required")
                return None
            return self.field._run(path, value, result)

    card_number = RequiredIf(String(min_len=12), when="payment_type", equals="card")

    # cash: not required -> ok even when absent
    result = Result()
    card_number._run("card_number", None, result, siblings={"payment_type": "cash"})
    assert result.valid

    # card: required -> absent is an error
    result = Result()
    card_number._run("card_number", None, result, siblings={"payment_type": "card"})
    assert not result.valid
    assert result.errors["card_number"] == "is required"

    # card: present but too short -> field's own error
    result = Result()
    card_number._run("card_number", "123", result, siblings={"payment_type": "card"})
    assert not result.valid
    assert "at least 12" in result.errors["card_number"]

    # card: present and valid -> ok
    result = Result()
    card_number._run("card_number", "424242424242", result, siblings={"payment_type": "card"})
    assert result.valid


# --------------------------------------------------------------------------
# validate_or_raise
# --------------------------------------------------------------------------


def test_validate_or_raise_on_invalid():
    schema = Schema({"email": Email()})
    with pytest.raises(ValidationError) as exc_info:
        schema.validate_or_raise({"email": "bad"})
    assert "email" in exc_info.value.result.errors


def test_validate_or_raise_returns_data_on_valid():
    schema = Schema({"email": Email()})
    data = schema.validate_or_raise({"email": "x@y.com"})
    assert data == {"email": "x@y.com"}


# --------------------------------------------------------------------------
# Edge cases
# --------------------------------------------------------------------------


def test_none_input():
    schema = Schema({"email": Email()})
    result = schema.validate(None)
    assert not result.valid
    assert "email" in result.errors


def test_non_dict_input():
    schema = Schema({"email": Email()})
    result = schema.validate("not a dict")
    assert not result.valid


def test_empty_string_treated_as_missing_for_int():
    schema = Schema({"age": Int(required=False)})
    result = schema.validate({"age": ""})
    assert result.valid
    assert "age" not in result.data


def test_result_repr():
    schema = Schema({"email": Email()})
    valid = schema.validate({"email": "x@y.com"})
    assert "valid=True" in repr(valid)
    invalid = schema.validate({"email": "bad"})
    assert "valid=False" in repr(invalid)


def test_field_used_directly():
    """Fields can be used outside a schema too."""
    assert Int(min=0).validate("5") == 5
    with pytest.raises(Invalid):
        Int(min=0).validate("-1")
    assert Email().validate("a@b.com") == "a@b.com"


def test_list_field_used_directly():
    field = List(String(min_len=1))
    assert field.validate(["a", "bb"]) == ["a", "bb"]
    with pytest.raises(Invalid):
        field.validate(["a", ""])


def test_dict_field_used_directly():
    field = Dict({"name": String()})
    assert field.validate({"name": "Alice"}) == {"name": "Alice"}
    with pytest.raises(Invalid):
        field.validate({"name": ""})