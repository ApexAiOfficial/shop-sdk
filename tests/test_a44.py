"""Reviews, custom orders, membership plans and version labels (contract revision 2026-11-05)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

import httpx
import pytest

from apexai_shop import (
    AsyncConfirmationRequired,
    AsyncPendingApproval,
    Completed,
    ConfirmationRequired,
    ConflictError,
    NotFoundError,
    NotPublishableError,
    PendingApproval,
    WrongStateError,
    models,
)
from conftest import PREFIX, SAMPLES, answer, make_async_shop, make_shop, route


def _body(request: httpx.Request) -> Optional[Dict[str, Any]]:
    return json.loads(request.content) if request.content else None


def _path(request: httpx.Request) -> str:
    return request.url.path[len(PREFIX):]


def _pending(reason: str = "contact_details") -> Dict[str, Any]:
    return answer(
        "pending_approval",
        approval={
            "id": 12,
            "status": "pending",
            "expires_at": "2026-11-05T12:15:00Z",
            "reasons": [reason],
        },
        next="Tell the user the store owner must tap Approve in Telegram.",
    )


def _preview(confirmation_id: str, summary: str, details: Dict[str, Any]) -> Dict[str, Any]:
    return answer(
        "confirmation_required",
        confirmation={"id": confirmation_id, "expires_at": "2026-11-05T12:05:00Z"},
        effect={"summary": summary, "details": details},
    )


def _two_step(operation_id: str, preview_body: Dict[str, Any]):
    """A handler answering a preview without ``confirmation`` and the sample with it."""
    calls: List[Optional[Dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _body(request)
        calls.append(body)
        if body is not None and "confirmation" in body:
            return httpx.Response(200, json=SAMPLES[operation_id])
        return httpx.Response(202, json=preview_body)

    return handler, calls


def _error(status: int, payload: Dict[str, Any]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload, headers={"x-request-id": "req_a44"})

    return handler


# --- reviews: reads ----------------------------------------------------------


def test_reviews_list_sends_filters_and_types_rows():
    shop, rec, _ = make_shop()
    page = shop.reviews.list(status="public", item_id=10, rating=4, limit=10, cursor="c1")

    assert isinstance(page, models.ReviewPage)
    assert page.untrusted_fields == ["data[].title", "data[].body"]
    row = page.data[0]
    assert isinstance(row, models.ReviewRow)
    assert (row.review_id, row.rating, row.publishable, row.published_at) == (300, 4, True, None)
    assert row.submitted_at is not None and row.submitted_at.tzinfo is not None
    request = rec.requests[0]
    assert (request.method, _path(request)) == ("GET", "/reviews")
    assert dict(request.url.params) == {
        "status": "public",
        "item_id": "10",
        "rating": "4",
        "limit": "10",
        "cursor": "c1",
    }


def test_reviews_list_sends_no_filters_by_default_so_the_server_default_queue_applies():
    shop, rec, _ = make_shop()
    shop.reviews.list()
    assert dict(rec.requests[0].url.params) == {}


def test_reviews_queue_and_iter_follow_cursors_with_the_first_page_filters():
    pages = {
        None: {"untrusted_fields": [], "data": [SAMPLES["list_reviews"]["data"][0]], "next_cursor": "n2"},
        "n2": {
            "untrusted_fields": [],
            "data": [dict(SAMPLES["list_reviews"]["data"][0], review_id=301)],
            "next_cursor": None,
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[request.url.params.get("cursor")])

    shop, rec, _ = make_shop(handler)
    ids = [row.review_id for row in shop.reviews.queue(item_id=10, limit=1)]
    assert ids == [300, 301]
    assert dict(rec.requests[0].url.params) == {"status": "queue", "item_id": "10", "limit": "1"}
    assert dict(rec.requests[1].url.params) == {"cursor": "n2"}

    shop, rec, _ = make_shop(handler)
    assert [row.review_id for row in shop.reviews.iter(status="all", rating=5)] == [300, 301]
    assert dict(rec.requests[0].url.params) == {"status": "all", "rating": "5"}


def test_reviews_get_types_response_history_and_aggregate():
    shop, rec, _ = make_shop()
    review = shop.reviews.get(300)

    assert isinstance(review, models.ReviewDetail)
    assert isinstance(review, models.ReviewRow)
    assert review.untrusted_fields == ["title", "body"]
    assert isinstance(review.response, models.ReviewResponseText)
    assert review.response.body == "Thank you!"
    assert isinstance(review.history[0], models.ReviewEvent)
    assert review.history[0].actor_type == "buyer"
    assert isinstance(review.aggregate, models.Rating)
    assert (review.aggregate.average, review.aggregate.count) == (5.0, 1)
    assert (rec.requests[0].method, _path(rec.requests[0])) == ("GET", "/reviews/300")


def test_rating_average_is_none_without_counted_reviews():
    rating = models.Rating.model_validate({"average": None, "count": 0})
    assert rating.average is None


# --- reviews: writes ---------------------------------------------------------


def test_reviews_publish_sends_an_empty_body_and_a_key():
    shop, rec, _ = make_shop()
    done = shop.reviews.publish(300, idempotency_key="publish-review-300-a")

    assert isinstance(done, Completed)
    assert isinstance(done.result, models.ReviewState)
    assert done.result.counted is True
    assert done.result.aggregate.count == 2
    request = rec.requests[0]
    assert (request.method, _path(request), _body(request)) == ("POST", "/reviews/300/publish", {})
    assert request.headers["idempotency-key"] == "publish-review-300-a"


def test_reviews_publish_with_contact_details_waits_for_the_owner():
    shop, _, _ = make_shop(lambda request: httpx.Response(202, json=_pending()))
    result = shop.reviews.publish(300)
    assert isinstance(result, PendingApproval)
    assert not isinstance(result, Completed)
    assert result.approval.reasons == ["contact_details"]


def test_reviews_publish_of_a_withdrawn_review_raises_not_publishable():
    payload = {
        "error": "not_publishable",
        "message": "Review #300 was withdrawn; it cannot be published.",
    }
    shop, _, _ = make_shop(_error(409, payload))
    with pytest.raises(NotPublishableError) as caught:
        shop.reviews.publish(300, idempotency_key="publish-review-300-b")
    assert isinstance(caught.value, ConflictError)
    assert caught.value.code == "not_publishable"
    assert caught.value.idempotency_key == "publish-review-300-b"


def test_reviews_withhold_counted_review_previews_then_confirms():
    handler, calls = _two_step(
        "withhold_review",
        _preview(
            "conf_withhold1",
            "Remove review #300: Guide's rating goes from 4.5 (2) to 5.0 (1).",
            {
                "review_id": 300,
                "action": "reject",
                "aggregate_before": {"average": 4.5, "count": 2},
                "aggregate_after": {"average": 5.0, "count": 1},
            },
        ),
    )
    shop, rec, _ = make_shop(handler)
    preview = shop.reviews.withhold(300, "reject")

    assert isinstance(preview, ConfirmationRequired)
    assert preview.effect.details["aggregate_after"] == {"average": 5.0, "count": 1}
    done = preview.confirm(idempotency_key="withhold-review-300-confirm")
    assert isinstance(done, Completed)
    assert isinstance(done.result, models.ReviewState)
    assert done.result.content_state == "removed"
    assert calls == [
        {"action": "reject"},
        {"action": "reject", "confirmation": "conf_withhold1"},
    ]
    assert rec.requests[1].headers["idempotency-key"] == "withhold-review-300-confirm"
    assert rec.requests[0].headers["idempotency-key"] != rec.requests[1].headers["idempotency-key"]


def test_reviews_withhold_an_uncounted_review_changes_at_once_and_takes_a_confirmation():
    shop, rec, _ = make_shop()
    done = shop.reviews.withhold(300, "hide")
    assert isinstance(done, Completed)
    assert _body(rec.requests[0]) == {"action": "hide"}

    shop, rec, _ = make_shop()
    shop.reviews.withhold(
        300,
        "reject",
        confirmation=models.ConfirmationInfo(id="conf_later", expires_at="2026-11-05T12:05:00Z"),
    )
    assert _body(rec.requests[0]) == {"action": "reject", "confirmation": "conf_later"}


def test_reviews_withhold_of_a_draft_raises_wrong_state_with_current():
    payload = {
        "error": "wrong_state",
        "message": "Review #300 is a draft.",
        "current": {"content_state": "draft"},
        "current_status": None,
    }
    shop, _, _ = make_shop(_error(409, payload))
    with pytest.raises(WrongStateError) as caught:
        shop.reviews.withhold(300, "hide")
    assert caught.value.current == {"content_state": "draft"}
    assert caught.value.current_status is None
    assert caught.value.request_id == "req_a44"


def test_reviews_respond_returns_the_length_never_the_text():
    shop, rec, _ = make_shop()
    done = shop.reviews.respond(300, "Thank you!")

    assert isinstance(done, Completed)
    assert isinstance(done.result, models.ReviewResponseResult)
    assert (done.result.created, done.result.characters) == (True, 10)
    assert not hasattr(done.result, "body")
    assert (_path(rec.requests[0]), _body(rec.requests[0])) == (
        "/reviews/300/response",
        {"body": "Thank you!"},
    )

    shop, _, _ = make_shop(lambda request: httpx.Response(202, json=_pending()))
    assert isinstance(shop.reviews.respond(300, "Write me at t.me/example"), PendingApproval)


def test_reviews_noop_and_replayed_answers_are_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=dict(SAMPLES["respond_review"], noop=True, replayed=True))

    shop, _, _ = make_shop(handler)
    done = shop.reviews.respond(300, "Thank you!")
    assert isinstance(done, Completed)
    assert done.noop and done.replayed


# --- custom orders -----------------------------------------------------------


def test_custom_orders_list_iter_and_get():
    shop, rec, _ = make_shop()
    page = shop.custom_orders.list(status="CLARIFICATION_RECEIVED", limit=5, cursor="c9")
    rows = list(shop.custom_orders.iter(status="all"))
    detail = shop.custom_orders.get(400)

    assert isinstance(page, models.CustomOrderPage)
    assert isinstance(page.data[0], models.CustomOrderRow)
    assert page.data[0].actions == ["clarify", "reject"]
    assert page.data[0].quote is None
    assert [row.request_id for row in rows] == [400]
    assert isinstance(detail, models.CustomOrderDetail)
    assert detail.description == "A square logo for my shop."
    assert isinstance(detail.notice, models.CustomOrderNotice)
    assert detail.notice.attempts == 0
    assert dict(rec.requests[0].url.params) == {
        "status": "CLARIFICATION_RECEIVED",
        "limit": "5",
        "cursor": "c9",
    }
    assert dict(rec.requests[1].url.params) == {"status": "all"}
    assert [(r.method, _path(r)) for r in rec.requests] == [
        ("GET", "/custom-orders"),
        ("GET", "/custom-orders"),
        ("GET", "/custom-orders/400"),
    ]


def test_custom_order_quote_is_typed():
    row = models.CustomOrderRow.model_validate(
        dict(
            SAMPLES["list_custom_orders"]["data"][0],
            quote={"amount_stars": 700, "status": "SENT", "expires_at": None},
        )
    )
    assert isinstance(row.quote, models.CustomOrderQuote)
    assert row.quote.amount_stars == 700


def test_custom_orders_clarify_previews_then_messages_the_buyer():
    handler, calls = _two_step(
        "clarify_custom_order",
        _preview(
            "conf_clarify1",
            "Ask the buyer of custom order 400: they are messaged at once.",
            {"request_id": 400, "status_before": "PENDING", "replaces_answer": False},
        ),
    )
    shop, rec, _ = make_shop(handler)
    preview = shop.custom_orders.clarify(400, "Which colours do you want?")

    assert isinstance(preview, ConfirmationRequired)
    assert preview.effect.details["replaces_answer"] is False
    done = preview.confirm()
    assert isinstance(done, Completed)
    assert isinstance(done.result, models.CustomOrderState)
    assert (done.result.status, done.result.notified) == ("CLARIFICATION_REQUESTED", True)
    assert calls == [
        {"question": "Which colours do you want?"},
        {"question": "Which colours do you want?", "confirmation": "conf_clarify1"},
    ]
    assert [_path(r) for r in rec.requests] == ["/custom-orders/400/clarify"] * 2


def test_custom_orders_reject_previews_then_tells_the_buyer():
    handler, calls = _two_step(
        "reject_custom_order",
        _preview(
            "conf_reject1",
            "Reject custom order 400: the buyer is told at once; this cannot be undone.",
            {"request_id": 400, "status_before": "PENDING", "quote": None},
        ),
    )
    shop, _, _ = make_shop(handler)
    preview = shop.custom_orders.reject(400, "We do not take logo work.")
    assert isinstance(preview, ConfirmationRequired)
    done = preview.confirm()
    assert isinstance(done, Completed)
    assert (done.result.status, done.result.notified) == ("REJECTED", False)
    assert calls[1] == {"reason": "We do not take logo work.", "confirmation": "conf_reject1"}


def test_custom_orders_with_contact_details_wait_for_the_owner():
    shop, _, _ = make_shop(lambda request: httpx.Response(202, json=_pending()))
    assert isinstance(shop.custom_orders.clarify(400, "Mail me at a@b.example"), PendingApproval)
    assert isinstance(shop.custom_orders.reject(400, "See t.me/example for why"), PendingApproval)


def test_custom_orders_wrong_state_carries_the_current_status():
    payload = {
        "error": "wrong_state",
        "message": "Custom order 400 is REJECTED.",
        "current": None,
        "current_status": "REJECTED",
    }
    shop, _, _ = make_shop(_error(409, payload))
    with pytest.raises(WrongStateError) as caught:
        shop.custom_orders.clarify(400, "Which colours do you want?", idempotency_key="clarify-400-abcdefgh")
    assert caught.value.current_status == "REJECTED"
    assert caught.value.current is None
    assert caught.value.idempotency_key == "clarify-400-abcdefgh"


def test_other_409_codes_stay_plain_conflicts_with_current_status():
    payload = {"error": "brand_new_code", "message": "Something newer.", "current_status": "X"}
    shop, _, _ = make_shop(_error(409, payload))
    with pytest.raises(ConflictError) as caught:
        shop.custom_orders.reject(400, "We do not take logo work.")
    assert type(caught.value) is ConflictError
    assert caught.value.current_status == "X"


# --- membership plans --------------------------------------------------------


def test_membership_plans_list_and_get():
    shop, rec, _ = make_shop()
    plans = shop.membership_plans.list()
    plan = shop.membership_plans.get(5)

    assert isinstance(plans, models.MembershipPlanList)
    assert isinstance(plan, models.MembershipPlanRow)
    assert (plan.price_stars, plan.live_members, plan.members_only_items) == (250, 12, 3)
    assert [(r.method, _path(r), dict(r.url.params)) for r in rec.requests] == [
        ("GET", "/membership-plans", {}),
        ("GET", "/membership-plans", {}),
    ]
    with pytest.raises(NotFoundError) as caught:
        shop.membership_plans.get(6)
    assert caught.value.code == "not_found"


def test_membership_price_previews_then_confirms():
    handler, calls = _two_step(
        "set_membership_price",
        _preview(
            "conf_price1",
            "New joins and rejoins pay 300 Stars every 30 days (was 250); 12 live members keep their locked price.",
            {"price_before": 250, "price_after": 300, "live_members": 12},
        ),
    )
    shop, rec, _ = make_shop(handler)
    preview = shop.membership_plans.set_price(5, 300)

    assert isinstance(preview, ConfirmationRequired)
    assert preview.effect.details["live_members"] == 12
    done = preview.confirm()
    assert isinstance(done, Completed)
    assert isinstance(done.result, models.MembershipPlanRow)
    assert done.result.price_stars == 300
    assert calls == [{"price_stars": 300}, {"price_stars": 300, "confirmation": "conf_price1"}]
    assert [_path(r) for r in rec.requests] == ["/membership-plans/5/price"] * 2


def test_membership_deep_price_cut_waits_for_the_owner():
    shop, _, _ = make_shop(lambda request: httpx.Response(202, json=_pending("deep_price_cut")))
    result = shop.membership_plans.set_price(5, 50)
    assert isinstance(result, PendingApproval)
    assert result.approval.reasons == ["deep_price_cut"]


def test_membership_off_previews_and_on_is_at_once():
    handler, calls = _two_step(
        "set_membership_active",
        _preview(
            "conf_off1",
            "Non-members can no longer join, and the 3 members-only items in Guides can no longer be bought by non-members.",
            {"plan_id": 5, "category_id": 1, "live_members": 12, "members_only_items": 3},
        ),
    )
    shop, _, _ = make_shop(handler)
    preview = shop.membership_plans.set_active(5, False)
    assert isinstance(preview, ConfirmationRequired)
    done = preview.confirm()
    assert isinstance(done, Completed)
    assert done.result.active is False
    assert calls == [{"active": False}, {"active": False, "confirmation": "conf_off1"}]

    shop, rec, _ = make_shop()
    on = shop.membership_plans.set_active(5, True)
    assert isinstance(on, Completed)
    assert _body(rec.requests[0]) == {"active": True}


# --- version labels ----------------------------------------------------------


def test_items_get_reads_the_version_label_and_it_defaults_to_none():
    shop, _, _ = make_shop()
    assert shop.items.get(10).version_label == "2.1"
    payload = dict(SAMPLES["get_item"])
    del payload["version_label"]
    assert models.ItemDetail.model_validate(payload).version_label is None


def test_items_set_version_label_sets_and_clears():
    shop, rec, _ = make_shop()
    done = shop.items.set_version_label(10, "2.2")
    cleared = shop.items.set_version_label(10, "")

    assert isinstance(done, Completed)
    assert isinstance(done.result, models.VersionLabel)
    assert done.result.version_label == "2.2"
    assert [(r.method, _path(r), _body(r)) for r in rec.requests] == [
        ("POST", "/items/10/version-label", {"label": "2.2"}),
        ("POST", "/items/10/version-label", {"label": ""}),
    ]
    assert isinstance(cleared, Completed)
    assert all("idempotency-key" in r.headers for r in rec.requests)


# --- retries -----------------------------------------------------------------


def test_a_buyer_message_write_retries_with_the_same_key_after_a_lost_answer():
    attempts: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.ReadTimeout("lost")
        return route(request)

    shop, _, sleeps = make_shop(handler)
    done = shop.custom_orders.clarify(400, "Which colours do you want?")
    assert isinstance(done, Completed)
    assert len(attempts) == 2
    assert attempts[0].headers["idempotency-key"] == attempts[1].headers["idempotency-key"]
    assert len(sleeps) == 1


# --- async parity ------------------------------------------------------------


def test_async_reads_have_parity():
    async def scenario():
        shop, rec, _ = make_async_shop()
        async with shop:
            page = await shop.reviews.list(status="all")
            queued = [row async for row in shop.reviews.queue()]
            every = [row async for row in shop.reviews.iter(item_id=10)]
            review = await shop.reviews.get(300)
            orders = await shop.custom_orders.list()
            order_rows = [row async for row in shop.custom_orders.iter()]
            order = await shop.custom_orders.get(400)
            plans = await shop.membership_plans.list()
            plan = await shop.membership_plans.get(5)
            with pytest.raises(NotFoundError):
                await shop.membership_plans.get(99)
            item = await shop.items.get(10)
        return rec, page, queued, every, review, orders, order_rows, order, plans, plan, item

    rec, page, queued, every, review, orders, order_rows, order, plans, plan, item = asyncio.run(
        scenario()
    )
    assert isinstance(page, models.ReviewPage)
    assert [row.review_id for row in queued] == [300]
    assert [row.review_id for row in every] == [300]
    assert isinstance(review, models.ReviewDetail)
    assert isinstance(orders, models.CustomOrderPage)
    assert [row.request_id for row in order_rows] == [400]
    assert isinstance(order, models.CustomOrderDetail)
    assert isinstance(plans, models.MembershipPlanList)
    assert plan.plan_id == 5
    assert item.version_label == "2.1"
    assert dict(rec.requests[1].url.params) == {"status": "queue"}
    assert dict(rec.requests[2].url.params) == {"item_id": "10"}


def test_async_direct_writes_have_parity():
    async def scenario():
        shop, rec, _ = make_async_shop()
        async with shop:
            results = [
                await shop.reviews.publish(300),
                await shop.reviews.withhold(300, "hide"),
                await shop.reviews.respond(300, "Thank you!"),
                await shop.custom_orders.clarify(400, "Which colours do you want?"),
                await shop.custom_orders.reject(400, "We do not take logo work."),
                await shop.membership_plans.set_price(5, 300),
                await shop.membership_plans.set_active(5, True),
                await shop.items.set_version_label(10, "2.2"),
            ]
        return rec, results

    rec, results = asyncio.run(scenario())
    assert all(isinstance(result, Completed) for result in results)
    assert [type(result.result) for result in results] == [
        models.ReviewState,
        models.ReviewState,
        models.ReviewResponseResult,
        models.CustomOrderState,
        models.CustomOrderState,
        models.MembershipPlanRow,
        models.MembershipPlanRow,
        models.VersionLabel,
    ]
    assert [(r.method, _path(r)) for r in rec.requests] == [
        ("POST", "/reviews/300/publish"),
        ("POST", "/reviews/300/withhold"),
        ("POST", "/reviews/300/response"),
        ("POST", "/custom-orders/400/clarify"),
        ("POST", "/custom-orders/400/reject"),
        ("POST", "/membership-plans/5/price"),
        ("POST", "/membership-plans/5/active"),
        ("POST", "/items/10/version-label"),
    ]
    assert all("idempotency-key" in r.headers for r in rec.requests)


@pytest.mark.parametrize(
    "operation_id, call, first_body",
    [
        ("withhold_review", lambda shop: shop.reviews.withhold(300, "reject"), {"action": "reject"}),
        (
            "clarify_custom_order",
            lambda shop: shop.custom_orders.clarify(400, "Which colours do you want?"),
            {"question": "Which colours do you want?"},
        ),
        (
            "reject_custom_order",
            lambda shop: shop.custom_orders.reject(400, "We do not take logo work."),
            {"reason": "We do not take logo work."},
        ),
        ("set_membership_price", lambda shop: shop.membership_plans.set_price(5, 300), {"price_stars": 300}),
        ("set_membership_active", lambda shop: shop.membership_plans.set_active(5, False), {"active": False}),
    ],
)
def test_async_two_step_writes_preview_then_confirm(operation_id, call, first_body):
    handler, calls = _two_step(
        operation_id, _preview("conf_async1", "What will happen.", {"example": True})
    )

    async def scenario():
        shop, _, _ = make_async_shop(handler)
        async with shop:
            preview = await call(shop)
            assert isinstance(preview, AsyncConfirmationRequired)
            return await preview.confirm()

    done = asyncio.run(scenario())
    assert isinstance(done, Completed)
    assert calls == [first_body, dict(first_body, confirmation="conf_async1")]


@pytest.mark.parametrize(
    "call",
    [
        lambda shop: shop.reviews.publish(300),
        lambda shop: shop.reviews.respond(300, "Write me at t.me/example"),
        lambda shop: shop.custom_orders.clarify(400, "Mail me at a@b.example"),
        lambda shop: shop.custom_orders.reject(400, "See t.me/example for why"),
        lambda shop: shop.membership_plans.set_price(5, 50),
    ],
)
def test_async_approval_writes_wait_for_the_owner(call):
    approved = dict(SAMPLES["get_approval"], status="approved", decided_at="2026-11-05T12:01:00Z")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=approved)
        return httpx.Response(202, json=_pending())

    async def scenario():
        shop, rec, sleeps = make_async_shop(handler)
        async with shop:
            pending = await call(shop)
            assert isinstance(pending, AsyncPendingApproval)
            final = await pending.wait(timeout=10, poll=1)
        return rec, sleeps, final

    rec, sleeps, final = asyncio.run(scenario())
    assert final.status == "approved"
    assert sleeps == []
    assert [(r.method, _path(r)) for r in rec.requests][-1] == ("GET", "/approvals/12")


def test_async_wrong_state_and_not_publishable():
    async def scenario():
        shop, _, _ = make_async_shop(
            _error(409, {"error": "wrong_state", "message": "No.", "current_status": "REJECTED"})
        )
        async with shop:
            with pytest.raises(WrongStateError) as wrong:
                await shop.custom_orders.reject(400, "We do not take logo work.")
        shop, _, _ = make_async_shop(_error(409, {"error": "not_publishable", "message": "No."}))
        async with shop:
            with pytest.raises(NotPublishableError):
                await shop.reviews.publish(300)
        return wrong.value

    error = asyncio.run(scenario())
    assert error.current_status == "REJECTED"
