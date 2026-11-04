"""Licence-key stock reads, writes, batching and secrecy, sync and async."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from apexai_shop import (
    Completed,
    ConfirmationRequired,
    ConfirmKeyDeliveryError,
    BadRequestError,
    ShopError,
    models,
)
from conftest import PREFIX, SAMPLES, answer, make_async_shop, make_shop


SECRET = "KEY-secret-should-never-appear"


def _body(request: httpx.Request):
    return json.loads(request.content) if request.content else {}


def _path(request: httpx.Request) -> str:
    return request.url.path[len(PREFIX):]


def _preview(kind: str):
    noun = "keys" if kind == "void" else "holds"
    return answer(
        "confirmation_required",
        confirmation={"id": f"conf_{kind}123", "expires_at": "2026-11-02T12:05:00Z"},
        effect={"summary": f"Change {noun}.", "details": {noun: 2}},
    )


def _import_handler(*, fail_stage: int | None = None):
    stages = 0
    staged_counts = {}
    available = 5

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal stages, available
        path = _path(request)
        if request.method == "GET":
            return httpx.Response(200, json=SAMPLES["get_item_stock"])
        if path.endswith("/stage"):
            stages += 1
            if fail_stage == stages:
                return httpx.Response(
                    400,
                    json={"error": "nothing_to_stock", "message": f"Refused {SECRET}"},
                )
            lines = _body(request)["keys_text"].splitlines()
            stage_id = f"stage_{stages}"
            staged_counts[stage_id] = len(lines)
            result = dict(SAMPLES["stage_key_import"]["result"])
            result.update(
                staged_import={"id": stage_id, "expires_at": "2026-11-02T12:15:00Z"},
                keys=len(lines),
                would_add=len(lines),
                available_now=available,
                available_after=available + len(lines),
            )
            return httpx.Response(200, json=answer(result=result))
        stage_id = _body(request)["staged_import"]
        added = staged_counts[stage_id]
        available += added
        result = dict(SAMPLES["commit_key_import"]["result"])
        result.update(added=added, available=available)
        return httpx.Response(200, json=answer(result=result))

    return handler


def test_item_stock_and_direct_import_requests_are_typed():
    shop, rec, _ = make_shop()
    detail = shop.stock.item(10)
    staged = shop.stock.stage(10, f"{SECRET}\nKEY-2")
    committed = shop.stock.commit(10, staged.result.staged_import.id)

    assert isinstance(detail, models.StockDetail)
    assert detail.levels.available == 5
    assert [(r.method, _path(r)) for r in rec.requests] == [
        ("GET", "/items/10/stock"),
        ("POST", "/items/10/keys/stage"),
        ("POST", "/items/10/keys/commit"),
    ]
    assert _body(rec.requests[1]) == {"keys_text": f"{SECRET}\nKEY-2"}
    assert "idempotency-key" not in rec.requests[1].headers
    assert _body(rec.requests[2]) == {
        "staged_import": "stage_abc123",
        "enable_key_delivery": False,
    }
    assert isinstance(staged, Completed)
    assert isinstance(staged.result, models.KeyStage)
    assert isinstance(committed.result, models.KeyImport)


@pytest.mark.parametrize(
    ("method", "operation", "sample", "result_type"),
    [
        ("void", "void", "void_keys", models.KeysVoided),
        ("release", "release", "release_key_holds", models.HoldsReleased),
    ],
)
def test_destructive_stock_operations_are_two_step(method, operation, sample, result_type):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_body(request))
        if not calls[-1]:
            return httpx.Response(202, json=_preview(operation))
        return httpx.Response(200, json=SAMPLES[sample])

    shop, _, _ = make_shop(handler)
    preview = getattr(shop.stock, method)(10)
    assert isinstance(preview, ConfirmationRequired)
    done = preview.confirm()
    assert calls == [{}, {"confirmation": f"conf_{operation}123"}]
    assert isinstance(done.result, result_type)


def test_commit_requires_explicit_key_delivery_opt_in_and_stage_remains_usable():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(_body(request))
        if not bodies[-1]["enable_key_delivery"]:
            return httpx.Response(
                409,
                json={
                    "error": "confirm_key_delivery",
                    "message": "Explicit opt-in required.",
                },
            )
        return httpx.Response(200, json=SAMPLES["commit_key_import"])

    shop, _, _ = make_shop(handler)
    with pytest.raises(ConfirmKeyDeliveryError) as info:
        shop.stock.commit(10, "stage_abc123")
    assert "enable_key_delivery=True" in str(info.value)
    done = shop.stock.commit(10, "stage_abc123", enable_key_delivery=True)
    assert bodies == [
        {"staged_import": "stage_abc123", "enable_key_delivery": False},
        {"staged_import": "stage_abc123", "enable_key_delivery": True},
    ]
    assert isinstance(done, Completed)


def test_import_surfaces_key_delivery_opt_in_without_enabling_it():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _body(request)
        bodies.append(body)
        if _path(request).endswith("/stage"):
            return httpx.Response(200, json=SAMPLES["stage_key_import"])
        return httpx.Response(
            409,
            json={"error": "confirm_key_delivery", "message": "Explicit opt-in required."},
        )

    shop, _, _ = make_shop(handler)
    with pytest.raises(ConfirmKeyDeliveryError) as info:
        shop.stock.import_keys(10, text="K-1\nK-2")
    assert info.value.batches_committed == 0
    assert bodies[-1]["enable_key_delivery"] is False


def test_import_batches_at_exactly_one_thousand_keys():
    text = "\n".join(f"K-{n}" for n in range(1000))
    shop, rec, _ = make_shop(_import_handler())
    summary = shop.stock.import_keys(10, text=text)

    stages = [r for r in rec.requests if _path(r).endswith("/stage")]
    assert len(stages) == 1
    assert len(_body(stages[0])["keys_text"].splitlines()) == 1000
    assert summary.total_keys == summary.total_added == 1000
    assert len(summary.batches) == 1


def test_import_splits_after_one_thousand_keys_and_accepts_a_utf8_file(tmp_path):
    path = tmp_path / "keys.txt"
    path.write_text("\n".join(f"K-{n}" for n in range(1001)), encoding="utf-8")
    shop, rec, _ = make_shop(_import_handler())
    summary = shop.stock.import_keys(10, path=path)

    stages = [r for r in rec.requests if _path(r).endswith("/stage")]
    assert [len(_body(r)["keys_text"].splitlines()) for r in stages] == [1000, 1]
    assert summary.total_keys == 1001
    assert [batch.batch for batch in summary.batches] == [1, 2]


def test_import_character_limit_allows_exactly_sixty_thousand_characters():
    lines = ["K" * 59 for _ in range(1000)]
    lines[0] += "K"
    text = "\n".join(lines)
    assert len(text) == 60_000
    shop, rec, _ = make_shop(_import_handler())
    shop.stock.import_keys(10, text=text)
    stages = [r for r in rec.requests if _path(r).endswith("/stage")]
    assert len(stages) == 1
    assert len(stages[0].content) < 60 * 1024


def test_import_splits_escaped_text_to_keep_every_json_body_under_sixty_kib():
    text = "\n".join(('"' * 50) + str(n) for n in range(1000))
    assert len(text) < 60_000
    shop, rec, _ = make_shop(_import_handler())
    summary = shop.stock.import_keys(10, text=text)
    stages = [r for r in rec.requests if _path(r).endswith("/stage")]
    assert len(stages) > 1
    assert all(len(request.content) < 60 * 1024 for request in stages)
    assert summary.total_keys == 1000


def test_partial_failure_reports_committed_batches_without_revealing_keys():
    shop, _, _ = make_shop(_import_handler(fail_stage=2))
    text = "\n".join([SECRET, *(f"K-{n}" for n in range(1000))])
    with pytest.raises(BadRequestError) as info:
        shop.stock.import_keys(10, text=text)
    error = info.value
    assert error.batches_committed == 1
    assert "after 1 committed batch" in str(error)
    assert SECRET not in str(error)
    assert SECRET not in repr(error)


def test_keys_never_appear_in_logs_result_or_error_representations(caplog):
    caplog.set_level(logging.DEBUG)
    shop, _, _ = make_shop(_import_handler())
    summary = shop.stock.import_keys(10, text=SECRET)
    assert SECRET not in repr(summary)
    assert SECRET not in str(summary)
    assert SECRET not in caplog.text

    direct, _, _ = make_shop()
    staged = direct.stock.stage(10, SECRET)
    committed = direct.stock.commit(10, staged.result.staged_import.id)
    for result in (staged, committed):
        assert SECRET not in repr(result)
        assert SECRET not in str(result)

    refusing, _, _ = make_shop(_import_handler(fail_stage=1))
    with pytest.raises(BadRequestError) as info:
        refusing.stock.stage(10, SECRET)
    assert SECRET not in str(info.value)
    assert SECRET not in repr(info.value)
    assert SECRET not in caplog.text


@pytest.mark.parametrize("code", ["no_keys", "too_many_keys", "nothing_to_stock"])
def test_staging_documented_refusals_keep_the_code_but_redact_the_message(code):
    shop, _, _ = make_shop(
        lambda request: httpx.Response(
            400, json={"error": code, "message": f"Refused {SECRET}"}
        )
    )
    with pytest.raises(BadRequestError) as info:
        shop.stock.stage(10, SECRET)
    assert info.value.code == code
    assert SECRET not in str(info.value)


def test_staging_request_too_large_is_reported_without_key_text():
    shop, _, _ = make_shop(
        lambda request: httpx.Response(
            413, json={"error": "request_too_large", "message": f"Too large: {SECRET}"}
        )
    )
    with pytest.raises(ShopError) as info:
        shop.stock.stage(10, SECRET)
    assert info.value.code == "request_too_large"
    assert SECRET not in str(info.value)


def test_staging_is_not_retried_after_an_ambiguous_transport_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("answer lost", request=request)

    shop, rec, _ = make_shop(handler, max_retries=2)
    with pytest.raises(ShopError) as info:
        shop.stock.stage(10, SECRET)
    assert len(rec.requests) == 1
    assert info.value.idempotency_key is None
    assert SECRET not in str(info.value)


def test_import_input_is_unambiguous_and_secret_free_on_local_errors():
    shop, _, _ = make_shop()
    with pytest.raises(TypeError, match="exactly one"):
        shop.stock.import_keys(10)
    with pytest.raises(TypeError, match="exactly one"):
        shop.stock.import_keys(10, text=SECRET, path="keys.txt")
    oversized = SECRET + ("X" * 60_000)
    with pytest.raises(ValueError) as info:
        shop.stock.import_keys(10, text=oversized)
    assert SECRET not in str(info.value)
    assert SECRET not in repr(info.value)


def test_async_stock_operations_and_import_have_parity():
    async def scenario():
        shop, rec, _ = make_async_shop(_import_handler())
        detail = await shop.stock.item(10)
        staged = await shop.stock.stage(10, "K-1\nK-2")
        committed = await shop.stock.commit(10, staged.result.staged_import.id)
        summary = await shop.stock.import_keys(10, text="K-3\nK-4")
        return rec, detail, staged, committed, summary

    rec, detail, staged, committed, summary = asyncio.run(scenario())
    assert isinstance(detail, models.StockDetail)
    assert isinstance(staged.result, models.KeyStage)
    assert isinstance(committed.result, models.KeyImport)
    assert summary.total_added == 2
    assert len(rec.requests) == 5


@pytest.mark.parametrize(
    ("method", "operation", "sample", "attribute", "expected"),
    [
        ("void", "void", "void_keys", "voided", 5),
        ("release", "release", "release_key_holds", "released", 2),
    ],
)
def test_async_destructive_confirmation_is_awaited(
    method, operation, sample, attribute, expected
):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_body(request))
        if not calls[-1]:
            return httpx.Response(202, json=_preview(operation))
        return httpx.Response(200, json=SAMPLES[sample])

    async def scenario():
        shop, _, _ = make_async_shop(handler)
        preview = await getattr(shop.stock, method)(10)
        return await preview.confirm()

    done = asyncio.run(scenario())
    assert calls == [{}, {"confirmation": f"conf_{operation}123"}]
    assert getattr(done.result, attribute) == expected
