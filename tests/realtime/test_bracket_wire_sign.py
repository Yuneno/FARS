"""Deterministic wire-sign tests for bracket ticks (RT-9 live run 4).

The gateway expects bracket ticks SIGNED relative to entry: long (BUY)
sends SL ``-ticks`` / TP ``+ticks``; short (SELL) sends SL ``+ticks`` /
TP ``-ticks``. ``BracketConfig`` itself stays an unsigned distance.
Mock transport only, no network.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from src.realtime.connectors.projectx import JsonTransport
from src.realtime.orders.practice_client import (
    ORDER_SIDE_BUY,
    ORDER_SIDE_SELL,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_MARKET,
    ORDER_TYPE_STOP,
    ORDER_TYPE_TRAILING_STOP,
    BracketConfig,
    PracticeOrderClient,
)

TEST_ACCOUNT_ID = 27765990
TEST_CONTRACT_ID = "CON_MNQ_202612"


class RecordingTransport(JsonTransport):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        endpoint = url.split("api.topstepx.com")[-1] if "api.topstepx.com" in url else url
        self.calls.append((endpoint, dict(payload)))
        if endpoint == "/api/Order/place":
            return {"orderId": 88880001, "success": True, "errorMessage": None}
        return {"success": True}


def _place(side: int) -> dict[str, Any]:
    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_ID,),
    )
    client.place_order(
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        order_type=ORDER_TYPE_MARKET,
        side=side,
        size=1,
        stop_loss_bracket=BracketConfig(ticks=40, order_type=ORDER_TYPE_STOP),
        take_profit_bracket=BracketConfig(ticks=80, order_type=ORDER_TYPE_LIMIT),
    )
    assert len(transport.calls) == 1
    endpoint, payload = transport.calls[0]
    assert endpoint == "/api/Order/place"
    return payload


def test_buy_brackets_signed_sl_negative_tp_positive() -> None:
    payload = _place(ORDER_SIDE_BUY)
    assert payload["stopLossBracket"] == {"ticks": -40, "type": ORDER_TYPE_STOP}
    assert payload["takeProfitBracket"] == {"ticks": 80, "type": ORDER_TYPE_LIMIT}


def test_sell_brackets_signed_sl_positive_tp_negative() -> None:
    payload = _place(ORDER_SIDE_SELL)
    assert payload["stopLossBracket"] == {"ticks": 40, "type": ORDER_TYPE_STOP}
    assert payload["takeProfitBracket"] == {"ticks": -80, "type": ORDER_TYPE_LIMIT}


def test_bracket_absolute_distances_preserved() -> None:
    for side in (ORDER_SIDE_BUY, ORDER_SIDE_SELL):
        payload = _place(side)
        assert abs(payload["stopLossBracket"]["ticks"]) == 40
        assert abs(payload["takeProfitBracket"]["ticks"]) == 80


def test_trailing_stop_leg_follows_stop_loss_sign() -> None:
    transport = RecordingTransport()
    client = PracticeOrderClient(
        token_provider="dummy-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_ID,),
    )
    client.place_order(
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        order_type=ORDER_TYPE_MARKET,
        side=ORDER_SIDE_BUY,
        size=1,
        stop_loss_bracket=BracketConfig(ticks=40, order_type=ORDER_TYPE_TRAILING_STOP),
    )
    _, payload = transport.calls[0]
    assert payload["stopLossBracket"] == {"ticks": -40, "type": ORDER_TYPE_TRAILING_STOP}


@pytest.mark.parametrize("bad_ticks", [0, -1, -40])
def test_bracket_config_rejects_non_positive_ticks(bad_ticks: int) -> None:
    with pytest.raises(ValueError, match="bracket ticks must be positive"):
        BracketConfig(ticks=bad_ticks, order_type=ORDER_TYPE_STOP)


def test_bracket_config_to_payload_stays_unsigned() -> None:
    assert BracketConfig(ticks=40, order_type=ORDER_TYPE_STOP).to_payload() == {
        "ticks": 40,
        "type": ORDER_TYPE_STOP,
    }
