"""Tests for formvalidate. Run with: python -m pytest -q"""

import pytest

import formvalidate as fv
from formvalidate import (
    Schema, validate, optional,
    string, number, integer, boolean, none,
    list_of, dict_of, one_of, email, regex, length, range_, predicate,
    any_, all_,
)
from formvalidate.schema import dict_schema, when


# --- basics ----------------------------------------------------------------

def test_valid_data_returns_ok():
    s = dict_schema({"name": string()})
    r = validate(s, {"name": "Ada"})
    assert r.ok is True
    assert r.errors == []
    assert r.value == {"name": "Ada"}


def test_missing_required_field():
    s = dict_schema({"name": string()})
    r = validate(s, {})
    assert r.ok is False
    assert len(r.errors) == 1
    assert r.errors[0].path == ("name",)
    assert "required" in r.errors[0].message


def test_optional_field_can_be_missing():
    s = dict_schema({"nickname": optional(string())})
    r = validate(s, {})
    assert r.ok is True


def test_optional_field_can_be_none():
    s = dict_schema({"nickname": optional(string())})
    r = validate(s, {"nickname": None})
    assert r.ok is True


def test_optional_field_still_validated_when_present():
    s = dict_schema({"nickname": optional(string())})
    r = validate(s, {"nickname": ""})
    assert r.ok is False
    assert r.errors[0].path == ("nickname",)


# --- collecting every error -------------------------------------------------

def test_all_errors_collected_not_just_first():
    s = dict_schema({
        "name": string(),
        "age": integer(),
        "email": email(),
    })
    r = validate(s, {"name": "", "age": "old", "email": "nope"})
    assert r.ok is False
    # three field errors, one per field
    paths = sorted(e.path for e in r.errors)
    assert paths == [("age",), ("email",), ("name",)]


def test_nested_errors_carry_full_path():
    s = dict_schema({
        "user": dict_schema({
            "email": email(),
        }),
    })
    r = validate(s, {"user": {"email": "bad"}})
    assert r.ok is False
    assert r.errors[0].path == ("user", "email")


def test_list_errors_indexed():
    s = dict_schema({
        "items": list_of(dict_schema({"id": integer(), "qty": integer()})),
    })
    data = {"items": [{"id": "x", "qty": 1}, {"id": 2, "qty": "y"}, {"id": 3, "qty": 4}]}
    r = validate(s, data)
    assert r.ok is False
    paths = sorted(e.path for e in r.errors)
    assert ("items", "0", "id") in paths
    assert ("items", "1", "qty") in paths
    # the third item is fine, so no error for it
    assert ("items", "2", "id") not in paths


# --- type checks ------------------------------------------------------------

def test_string_rejects_non_string():
    s = string()
    r = validate(s, 42)
    assert r.ok is False
    assert "string" in r.errors[0].message


def test_string_rejects_empty_by_default():
    s = string()
    assert validate(s, "").ok is False
    assert validate(s, "x").ok is True


def test_string_can_allow_empty():
    assert validate(string(allow_empty=True), "").ok is True


def test_integer_rejects_bool():
    # bool is a subclass of int — we must not silently accept True as 1
    s = integer()
    assert validate(s, True).ok is False
    assert validate(s, 1).ok is True


def test_number_rejects_bool():
    assert validate(number(), True).ok is False
    assert validate(number(), 1.5).ok is True


def test_integer_with_coerce():
    assert validate(integer(coerce=True), "42").ok is True
    assert validate(integer(coerce=True), "abc").ok is False


def test_boolean():
    assert validate(boolean(), True).ok is True
    assert validate(boolean(), False).ok is True
    assert validate(boolean(), 0).ok is False
    assert validate(boolean(), "true").ok is False


def test_none():
    assert validate(none(), None).ok is True
    assert validate(none(), 0).ok is False


# --- value constraints ------------------------------------------------------

def test_one_of():
    s = one_of("red", "green", "blue")
    assert validate(s, "red").ok is True
    assert validate(s, "purple").ok is False


def test_length():
    s = length(min=2, max=4)
    assert validate(s, "ab").ok is True
    assert validate(s, "a").ok is False
    assert validate(s, "abcde").ok is False


def test_range():
    s = range_(min=0, max=120)
    assert validate(s, 30).ok is True
    assert validate(s, -1).ok is False
    assert validate(s, 200).ok is False


def test_email():
    assert validate(email(), "ada@example.com").ok is True
    assert validate(email(), "not-an-email").ok is False
    assert validate(email(), "missing@tld").ok is False
    assert validate(email(), 42).ok is False


def test_regex():
    s = regex(r"^[A-Z]{2}-\d+$", message="must look like AB-1234")
    assert validate(s, "AB-1234").ok is True
    assert validate(s, "ab-1234").ok is False


def test_predicate():
    s = predicate(lambda v: v % 2 == 0, "must be even")
    assert validate(s, 4).ok is True
    assert validate(s, 3).ok is False


# --- structural -------------------------------------------------------------

def test_list_of():
    s = list_of(integer())
    assert validate(s, [1, 2, 3]).ok is True
    assert validate(s, [1, "two"]).ok is False


def test_list_of_with_bounds():
    s = list_of(string(), min_length=1, max_length=2)
    assert validate(s, ["a"]).ok is True
    assert validate(s, []).ok is False
    assert validate(s, ["a", "b", "c"]).ok is False


def test_dict_of():
    s = dict_of(integer())
    assert validate(s, {"a": 1, "b": 2}).ok is True
    assert validate(s, {"a": 1, "b": "x"}).ok is False


# --- unknown fields ---------------------------------------------------------

def test_unknown_field_reported_by_default():
    s = dict_schema({"name": string()})
    r = validate(s, {"name": "Ada", "extra": 1})
    assert r.ok is False
    assert any(e.path == ("extra",) for e in r.errors)


def test_unknown_field_allowed_when_flag_set():
    s = dict_schema({"name": string()}, allow_extra=True)
    r = validate(s, {"name": "Ada", "extra": 1})
    assert r.ok is True


# --- conditionals -----------------------------------------------------------

def test_when_condition_applies_schema():
    # shipping address is required only if billing differs
    needs_shipping = lambda d: d.get("billing_same_as_shipping") is False
    s = dict_schema({
        "billing_same_as_shipping": boolean(),
        "shipping_address": when(needs_shipping, string()),
    })
    # billing same -> shipping not required
    assert validate(s, {"billing_same_as_shipping": True}).ok is True
    # billing differs -> shipping required
    r = validate(s, {"billing_same_as_shipping": False})
    assert r.ok is False
    assert any(e.path == ("shipping_address",) for e in r.errors)
    # billing differs and shipping provided -> ok
    assert validate(s, {
        "billing_same_as_shipping": False,
        "shipping_address": "123 Main St",
    }).ok is True


def test_when_with_otherwise():
    # when billing is same, shipping must be absent; when different, must be present
    needs_shipping = lambda d: d.get("billing_same_as_shipping") is False
    s = dict_schema({
        "billing_same_as_shipping": boolean(),
        "shipping_address": when(
            needs_shipping,
            then=string(),
            otherwise=string(allow_empty=True),  # present but may be empty
        ),
    })
    # billing same, shipping empty -> ok
    assert validate(s, {
        "billing_same_as_shipping": True,
        "shipping_address": "",
    }).ok is True
    # billing differs, shipping empty -> not ok
    r = validate(s, {
        "billing_same_as_shipping": False,
        "shipping_address": "",
    })
    assert r.ok is False


# --- combinators ------------------------------------------------------------

def test_any_of():
    s = any_(string(), integer())
    assert validate(s, "x").ok is True
    assert validate(s, 1).ok is True
    assert validate(s, 1.5).ok is False


def test_all_of():
    s = all_(string(), length(min=2))
    assert validate(s, "ab").ok is True
    assert validate(s, "a").ok is False
    assert validate(s, 1).ok is False  # not a string


# --- error formatting -------------------------------------------------------

def test_error_str_includes_path():
    s = dict_schema({"user": dict_schema({"email": email()})})
    r = validate(s, {"user": {"email": "bad"}})
    assert "user.email" in str(r.errors[0])


def test_errorlist_str_joins_lines():
    s = dict_schema({"a": string(), "b": string()})
    r = validate(s, {"a": "", "b": ""})
    text = str(r.errors)
    assert "\n" in text
    assert "a:" in text and "b:" in text


def test_errorlist_by_path_groups():
    s = dict_schema({
        "items": list_of(dict_schema({"qty": integer()})),
    })
    r = validate(s, {"items": [{"qty": "x"}, {"qty": "y"}]})
    grouped = r.errors.by_path()
    assert "items.0.qty" in grouped
    assert "items.1.qty" in grouped


def test_errorlist_bool_is_truthy_iff_nonempty():
    from formvalidate.errors import ErrorList, ValidationError
    empty = ErrorList()
    assert bool(empty) is False
    nonempty = ErrorList([ValidationError(path=("a",), message="bad")])
    assert bool(nonempty) is True


# --- deeply nested realistic-ish shape -------------------------------------

def test_signup_form_realistic():
    signup = dict_schema({
        "name": string(),
        "email": email(),
        "age": integer(),
        "role": one_of("admin", "user", "guest"),
        "address": optional(dict_schema({
            "street": string(),
            "zip": regex(r"^\d{5}$", message="must be a 5-digit zip"),
        })),
        "tags": list_of(string(), max_length=5),
    })

    # happy path
    assert validate(signup, {
        "name": "Ada",
        "email": "ada@example.com",
        "age": 36,
        "role": "admin",
        "tags": ["python", "math"],
    }).ok is True

    # everything wrong at once
    bad = {
        "name": "",
        "email": "nope",
        "age": "old",
        "role": "wizard",
        "address": {"street": "", "zip": "abc"},
        "tags": ["a", "b", "c", "d", "e", "f"],
    }
    r = validate(signup, bad)
    assert r.ok is False
    paths = {e.path for e in r.errors}
    assert ("name",) in paths
    assert ("email",) in paths
    assert ("age",) in paths
    assert ("role",) in paths
    assert ("address", "street") in paths
    assert ("address", "zip") in paths
    assert ("tags",) in paths
