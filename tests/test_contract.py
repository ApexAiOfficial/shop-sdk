"""The client must match contract/merchant-api-v1.json exactly."""

from __future__ import annotations

import re
import typing

import pytest
from pydantic import BaseModel

from apexai_shop import models
from apexai_shop._operations import OPERATIONS
from conftest import SAMPLES

EXCLUDED_SCHEMAS = {"Error", "InvalidRequest"}


def _operations(contract):
    for path, methods in contract["paths"].items():
        for method, op in methods.items():
            yield method.upper(), path, op


def _schemas(contract):
    return {
        name: schema
        for name, schema in contract["components"]["schemas"].items()
        if name not in EXCLUDED_SCHEMAS
    }


def test_contract_is_version_1(contract):
    assert contract["info"]["version"] == "1"
    assert contract["servers"] == [{"url": "/api/merchant/v1"}]


def test_operation_table_matches_contract_paths(contract):
    in_contract = {(method, path) for method, path, _ in _operations(contract)}
    in_table = {(method, path) for method, path, _ in OPERATIONS.values()}
    assert in_table == in_contract
    assert len(OPERATIONS) == len(in_contract) == 10


def test_operation_ids_match(contract):
    for method, path, op in _operations(contract):
        assert op["operationId"] in OPERATIONS, op["operationId"]
        assert OPERATIONS[op["operationId"]][:2] == (method, path)


def test_query_and_path_params_match(contract):
    for _, path, op in _operations(contract):
        params = op.get("parameters", [])
        query = {p["name"] for p in params if p["in"] == "query"}
        path_params = {p["name"] for p in params if p["in"] == "path"}
        _, table_path, table_query = OPERATIONS[op["operationId"]]
        assert set(table_query) == query, op["operationId"]
        assert len(table_query) == len(set(table_query))
        assert set(re.findall(r"{(\w+)}", table_path)) == path_params


def test_every_v01_operation_is_a_get():
    assert {method for method, _, _ in OPERATIONS.values()} == {"GET"}


def test_every_schema_has_a_model(contract):
    for name in _schemas(contract):
        model = getattr(models, name, None)
        assert isinstance(model, type) and issubclass(model, BaseModel), name
        assert name in models.__all__


def test_no_model_without_a_schema(contract):
    names = set(_schemas(contract))
    for name in models.__all__:
        assert name in names, name


@pytest.mark.parametrize("name", sorted(set(models.__all__)))
def test_model_fields_match_schema(contract, name):
    schema = contract["components"]["schemas"][name]
    model = getattr(models, name)
    assert set(model.model_fields) == set(schema["properties"])
    for field in schema.get("required", []):
        assert model.model_fields[field].is_required(), f"{name}.{field}"


def _nullable(prop) -> bool:
    return any(option.get("type") == "null" for option in prop.get("anyOf", []))


def _is_datetime(prop) -> bool:
    if prop.get("format") == "date-time":
        return True
    return any(option.get("format") == "date-time" for option in prop.get("anyOf", []))


@pytest.mark.parametrize("name", sorted(set(models.__all__)))
def test_model_nullability_and_datetimes_match_schema(contract, name):
    from datetime import datetime

    schema = contract["components"]["schemas"][name]
    model = getattr(models, name)
    hints = typing.get_type_hints(model)
    for field, prop in schema["properties"].items():
        args = typing.get_args(hints[field])
        allows_none = type(None) in args
        assert allows_none == _nullable(prop), f"{name}.{field}"
        if _is_datetime(prop):
            assert hints[field] is datetime or datetime in args, f"{name}.{field}"


def test_models_ignore_unknown_fields_and_values():
    payload = dict(SAMPLES["list_items"]["data"][0], status="archived", new_field=1)
    item = models.ItemSummary.model_validate(payload)
    assert item.status == "archived"
    assert not hasattr(item, "new_field")


def test_test_samples_cover_every_operation_and_validate(contract):
    """The fixtures the other tests use are valid 200 bodies for their operation."""
    assert set(SAMPLES) == set(OPERATIONS)
    for _, _, op in _operations(contract):
        ref = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        model = getattr(models, ref.rsplit("/", 1)[-1])
        model.model_validate(SAMPLES[op["operationId"]])
