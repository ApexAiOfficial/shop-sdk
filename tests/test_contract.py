"""The client must match contract/merchant-api-v1.json exactly."""

from __future__ import annotations

import re
import typing

import pytest
from pydantic import BaseModel

from apexai_shop import models
from apexai_shop._core import IDEMPOTENCY_HEADER, IDEMPOTENCY_KEY_PATTERN, confirmable
from apexai_shop._operations import OPERATIONS
from conftest import API_REVISION, SAMPLES

#: Error bodies become exceptions, not models.
ERROR_SCHEMAS = {"Error", "InvalidRequest", "WriteError"}
SDK_ONLY_MODELS = {"KeyImportBatch", "KeyImportSummary"}

def _operations(contract):
    for path, methods in contract["paths"].items():
        for method, op in methods.items():
            yield method.upper(), path, op


def _ref_name(schema):
    """The schema name a ``$ref`` (possibly inside ``anyOf`` with null) points to, or None."""
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    for option in schema.get("anyOf", []):
        if "$ref" in option:
            return option["$ref"].rsplit("/", 1)[-1]
    return None


def _body_schema_name(op):
    body = op.get("requestBody")
    if body is None:
        return None
    return _ref_name(body["content"]["application/json"]["schema"])


def _request_body_schemas(contract):
    """Schemas used only as request bodies: the operations table's body fields, not models."""
    names = {_body_schema_name(op) for _, _, op in _operations(contract)}
    return names - {None}


def _schemas(contract):
    excluded = ERROR_SCHEMAS | _request_body_schemas(contract)
    return {
        name: schema
        for name, schema in contract["components"]["schemas"].items()
        if name not in excluded
    }


def test_contract_is_version_1(contract):
    assert contract["info"]["version"] == "1"
    assert contract["servers"] == [{"url": "/api/merchant/v1"}]


def test_operation_table_matches_contract_paths(contract):
    in_contract = {(method, path) for method, path, _ in _operations(contract)}
    in_table = {(op.method, op.path) for op in OPERATIONS.values()}
    assert in_table == in_contract
    assert len(OPERATIONS) == len(in_contract) == 53


def test_operation_ids_match(contract):
    for method, path, op in _operations(contract):
        assert op["operationId"] in OPERATIONS, op["operationId"]
        assert OPERATIONS[op["operationId"]][:2] == (method, path)


def test_query_and_path_params_match(contract):
    for _, path, op in _operations(contract):
        params = op.get("parameters", [])
        query = {p["name"] for p in params if p["in"] == "query"}
        path_params = {p["name"] for p in params if p["in"] == "path"}
        table = OPERATIONS[op["operationId"]]
        assert set(table.query) == query, op["operationId"]
        assert len(table.query) == len(set(table.query))
        assert set(re.findall(r"{(\w+)}", table.path)) == path_params


def test_body_fields_match_request_body(contract):
    schemas = contract["components"]["schemas"]
    for method, _, op in _operations(contract):
        table = OPERATIONS[op["operationId"]]
        name = _body_schema_name(op)
        if name is None:
            assert "requestBody" not in op, op["operationId"]
            assert table.body == (), op["operationId"]
            continue
        assert set(table.body) == set(schemas[name]["properties"]), op["operationId"]
        assert len(table.body) == len(set(table.body))


def test_nullable_body_fields_are_optional(contract):
    """The client omits a ``None`` body field ("leave as it is"), which equals null only
    when the field is optional. A required nullable field would need an explicit null."""
    schemas = contract["components"]["schemas"]
    checked = 0
    for _, _, op in _operations(contract):
        name = _body_schema_name(op)
        if name is None:
            continue
        schema = schemas[name]
        for field, prop in schema["properties"].items():
            if _nullable(prop):
                checked += 1
                assert field not in schema.get("required", []), (op["operationId"], field)
    assert checked > 0


def test_operation_write_classification(contract):
    for method, _, op in _operations(contract):
        table = OPERATIONS[op["operationId"]]
        assert table.is_write == (method != "GET"), op["operationId"]
        if method == "GET":
            assert "requestBody" not in op, op["operationId"]
    assert {op.method for op in OPERATIONS.values()} == {"GET", "POST", "PATCH"}


def test_writes_take_the_idempotency_key_header(contract):
    for method, _, op in _operations(contract):
        headers = {p["name"]: p for p in op.get("parameters", []) if p["in"] == "header"}
        if method == "GET":
            assert IDEMPOTENCY_HEADER not in headers, op["operationId"]
            assert not OPERATIONS[op["operationId"]].uses_idempotency_key
            continue
        declared = IDEMPOTENCY_HEADER in headers
        assert OPERATIONS[op["operationId"]].uses_idempotency_key == declared
        if not declared:
            assert IDEMPOTENCY_HEADER not in headers
            continue
        header = headers[IDEMPOTENCY_HEADER]
        assert header["required"] is False
        patterns = [o["pattern"] for o in header["schema"]["anyOf"] if "pattern" in o]
        assert patterns == [IDEMPOTENCY_KEY_PATTERN.pattern], op["operationId"]
    assert {"stage_key_import"} == {
        op["operationId"]
        for method, _, op in _operations(contract)
        if method != "GET"
        and IDEMPOTENCY_HEADER
        not in {p["name"] for p in op.get("parameters", []) if p["in"] == "header"}
    }


def test_confirmable_writes_send_confirmation_in_the_body(contract):
    """Every two-step write (a ``Confirm`` body, or a 202 preview) takes ``confirmation``,
    and the client binds ``confirm()`` exactly for those."""
    two_step = set()
    for _, _, op in _operations(contract):
        preview_202 = "confirmation_required" in op["responses"].get("202", {}).get("description", "")
        if _body_schema_name(op) == "Confirm" or preview_202:
            two_step.add(op["operationId"])
    assert two_step, "no two-step write found: the selection matched nothing"
    for operation_id in two_step:
        assert "confirmation" in OPERATIONS[operation_id].body, operation_id
    assert {operation_id for operation_id in OPERATIONS if confirmable(operation_id)} == two_step


def test_write_answers_and_errors_are_documented(contract):
    for method, _, op in _operations(contract):
        if method == "GET":
            continue
        responses = op["responses"]
        answer = _ref_name(responses["200"]["content"]["application/json"]["schema"])
        assert issubclass(getattr(models, answer), models.WriteAnswer), op["operationId"]
        if "202" in responses:
            assert _ref_name(responses["202"]["content"]["application/json"]["schema"]) == answer
        for status in ("400", "403", "404", "409", "422", "423", "429", "503"):
            schema = responses[status]["content"]["application/json"]["schema"]
            assert _ref_name(schema) == "WriteError", (op["operationId"], status)


def test_every_schema_has_a_model(contract):
    for name in _schemas(contract):
        model = getattr(models, name, None)
        assert isinstance(model, type) and issubclass(model, BaseModel), name
        assert name in models.__all__


def test_no_model_without_a_schema(contract):
    names = set(_schemas(contract)) | SDK_ONLY_MODELS
    for name in models.__all__:
        assert name in names, name


@pytest.mark.parametrize("name", sorted(set(models.__all__) - SDK_ONLY_MODELS))
def test_model_fields_match_schema(contract, name):
    schema = contract["components"]["schemas"][name]
    model = getattr(models, name)
    assert set(model.model_fields) == set(schema["properties"])
    for field in schema.get("required", []):
        assert model.model_fields[field].is_required(), f"{name}.{field}"


def _nullable(prop) -> bool:
    if prop.get("type") == "null":
        return True
    return any(option.get("type") == "null" for option in prop.get("anyOf", []))


def _is_datetime(prop) -> bool:
    if prop.get("format") == "date-time":
        return True
    return any(option.get("format") == "date-time" for option in prop.get("anyOf", []))


@pytest.mark.parametrize("name", sorted(set(models.__all__) - SDK_ONLY_MODELS))
def test_model_nullability_and_datetimes_match_schema(contract, name):
    from datetime import datetime

    schema = contract["components"]["schemas"][name]
    model = getattr(models, name)
    hints = typing.get_type_hints(model)
    for field, prop in schema["properties"].items():
        args = typing.get_args(hints[field])
        allows_none = hints[field] is type(None) or type(None) in args
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


def _write_answer_names(contract):
    """The answer schema of every write, from its 200 response."""
    return {
        _ref_name(op["responses"]["200"]["content"]["application/json"]["schema"])
        for method, _, op in _operations(contract)
        if method != "GET"
    }


def test_write_answer_subclasses_only_type_the_result(contract):
    """Each operation's answer is the WriteAnswer envelope with a typed ``result``."""
    base = contract["components"]["schemas"]["WriteAnswer"]
    names = _write_answer_names(contract)
    assert names >= {
        "CategoryAnswer",
        "CategoryMoveAnswer",
        "CategoryDeleteAnswer",
        "KeyStageAnswer",
        "KeyImportAnswer",
        "KeysVoidAnswer",
        "HoldsReleaseAnswer",
        "TicketReplyAnswer",
        "TicketCloseAnswer",
        "DownloadsRestoreAnswer",
        "BroadcastAnswer",
        "BundleAnswer",
        "FileRequestAnswer",
        "RepriceAnswer",
        "VariantAnswer",
        "ReviewStateAnswer",
        "ReviewResponseAnswer",
        "CustomOrderAnswer",
        "MembershipPlanAnswer",
        "VersionLabelAnswer",
    }
    for name in names:
        schema = contract["components"]["schemas"][name]
        assert set(schema["properties"]) == set(base["properties"]), name
        assert schema["required"] == base["required"], name
        model = getattr(models, name)
        assert issubclass(model, models.WriteAnswer), name
        result_schema = _ref_name(schema["properties"]["result"])
        result_hint = typing.get_type_hints(model)["result"]
        assert getattr(models, result_schema) in typing.get_args(result_hint), name


def test_confirmable_writes_are_exactly_the_two_step_ones_by_name(contract):
    """Pins which writes preview first, so a contract change cannot move one silently."""
    assert {operation_id for operation_id in OPERATIONS if confirmable(operation_id)} == {
        "delete_category",
        "delete_item",
        "reprice_items",
        "update_coupon",
        "void_keys",
        "release_key_holds",
        "withhold_review",
        "clarify_custom_order",
        "reject_custom_order",
        "set_membership_price",
        "set_membership_active",
    }


def test_approval_only_writes_have_a_pending_202(contract):
    """Writes that can wait for the owner but never preview: a 202 that is only pending_approval."""
    approval_only = set()
    for method, _, op in _operations(contract):
        description = op["responses"].get("202", {}).get("description", "")
        if "pending_approval" in description and "confirmation_required" not in description:
            approval_only.add(op["operationId"])
    assert {"publish_review", "respond_review"} <= approval_only
    for operation_id in approval_only:
        assert not confirmable(operation_id), operation_id


def test_a44_reads_take_their_documented_filters(contract):
    assert OPERATIONS["list_reviews"].query == ("status", "item_id", "rating", "limit", "cursor")
    assert OPERATIONS["list_custom_orders"].query == ("status", "limit", "cursor")
    assert OPERATIONS["list_membership_plans"].query == ()
    statuses = {
        p["name"]: p["schema"]
        for p in contract["paths"]["/reviews"]["get"]["parameters"]
        if p["in"] == "query"
    }
    assert statuses["status"]["enum"] == ["queue", "public", "hidden", "removed", "all"]
    assert statuses["status"]["default"] == "queue"


def test_webhook_contract_has_the_same_revision_as_the_samples(contract):
    import json
    from conftest import CONTRACT_PATH

    webhooks = json.loads(
        (CONTRACT_PATH.parent / "merchant-webhooks-v1.json").read_text(encoding="utf-8")
    )
    assert webhooks["x-api-revision"] == SAMPLES["get_store"]["api_revision"] == API_REVISION


def test_item_version_label_is_optional_and_nullable(contract):
    for name in ("ItemDetail", "Bundle"):
        schema = contract["components"]["schemas"][name]
        assert "version_label" in schema["properties"]
        assert "version_label" not in schema.get("required", [])
        assert not models.ItemDetail.model_fields["version_label"].is_required()


# --- scopes: the docstrings must name what the contracts require ------------

#: The operations added in API revision 2026-11-05 (A4.4): each SDK method that
#: reaches one must say which scopes it needs.
A44_OPERATIONS = {
    "list_reviews",
    "get_review",
    "publish_review",
    "withhold_review",
    "respond_review",
    "list_custom_orders",
    "get_custom_order",
    "clarify_custom_order",
    "reject_custom_order",
    "list_membership_plans",
    "set_membership_price",
    "set_membership_active",
    "set_version_label",
}
_SCOPE = re.compile(r"``([a-z_]+:(?:read|write))``")
_NEEDS = re.compile(r"Needs\s+(.*?)(?:\.\s|\.$|;)", re.S)
_CLIENT_SOURCES = ("client.py", "async_client.py")


def _mcp_contract():
    import json
    from conftest import CONTRACT_PATH

    return json.loads(
        (CONTRACT_PATH.parent / "merchant-mcp-v1.json").read_text(encoding="utf-8")
    )


def _required_scopes(contract, mcp):
    """Operation id -> the scopes a token needs, from the two contracts.

    The MCP contract maps each tool (named like the operation) to its main
    scope; an operation that also needs ``pricing:write`` says so in its API
    description ("Needs pricing:write ...").
    """
    required = {}
    for _, _, op in _operations(contract):
        operation_id = op["operationId"]
        scope = mcp["scopes"][operation_id]
        needs = {scope} if scope else set()
        if re.search(r"\bNeeds pricing:write\b", op.get("description", "")):
            needs.add("pricing:write")
        required[operation_id] = needs
    return required


def _operation_ids(node):
    import ast

    return {
        c.value
        for c in ast.walk(node)
        if isinstance(c, ast.Constant) and isinstance(c.value, str) and c.value in OPERATIONS
    }


def _documented_methods():
    """(source, class, method, operation ids reached, scopes its docstring names or None).

    A method reaches the operations it names and those of the methods of its
    own class it calls (``reviews.queue`` pages through ``iter``,
    ``membership_plans.get`` reads ``list``).
    """
    import ast
    from pathlib import Path

    import apexai_shop

    package = Path(apexai_shop.__file__).resolve().parent
    for source in _CLIENT_SOURCES:
        tree = ast.parse((package / source).read_text(encoding="utf-8"))
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            methods = {
                n.name: n
                for n in cls.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            for name, method in methods.items():
                if name.startswith("_"):
                    continue
                operation_ids = _operation_ids(method)
                for call in ast.walk(method):
                    if (
                        isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and isinstance(call.func.value, ast.Name)
                        and call.func.value.id == "self"
                        and call.func.attr in methods
                    ):
                        operation_ids |= _operation_ids(methods[call.func.attr])
                if not operation_ids:
                    continue
                needs = _NEEDS.search(ast.get_docstring(method) or "")
                scopes = set(_SCOPE.findall(needs.group(1))) if needs else None
                yield source, cls.name, name, operation_ids, scopes


def test_mcp_contract_scopes_every_operation(contract):
    mcp = _mcp_contract()
    assert mcp["api_revision"] == API_REVISION
    assert set(mcp["scopes"]) == {tool["name"] for tool in mcp["tools"]} == set(OPERATIONS)
    required = _required_scopes(contract, mcp)
    # The scopes the A4.4 design settled, as the contracts state them.
    assert required["list_reviews"] == required["get_review"] == {"reviews:read"}
    assert required["respond_review"] == {"reviews:write"}
    assert required["list_custom_orders"] == {"custom_orders:read"}
    assert required["reject_custom_order"] == {"custom_orders:write"}
    assert required["list_membership_plans"] == {"catalog:read"}
    assert required["set_membership_active"] == required["set_version_label"] == {"catalog:write"}
    assert required["set_membership_price"] == {"catalog:write", "pricing:write"}


def test_docstring_scopes_match_the_contracts(contract):
    required = _required_scopes(contract, _mcp_contract())
    checked = set()
    for source, cls, name, operation_ids, scopes in _documented_methods():
        if scopes is None:
            continue
        expected = set().union(*(required[o] for o in operation_ids))
        assert scopes == expected, (source, cls, name, sorted(operation_ids))
        checked.add((source, frozenset(operation_ids)))
    assert checked  # the docstring pattern still finds something


def test_every_a44_method_names_its_scopes():
    seen = {source: set() for source in _CLIENT_SOURCES}
    for source, cls, name, operation_ids, scopes in _documented_methods():
        if operation_ids & A44_OPERATIONS:
            assert scopes, f"{source} {cls}.{name} does not say which scopes it needs"
            seen[source] |= operation_ids & A44_OPERATIONS
    assert seen == {source: A44_OPERATIONS for source in _CLIENT_SOURCES}
