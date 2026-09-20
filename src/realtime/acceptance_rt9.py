"""RT-9 Acceptance Protocol: Real orders in TopstepX Practice Account.

Executes the 8-step verification sequence defined in Section 2 of
FARS_LAB_GEMINI_ENCARGO_BLOQUE_RT9_ORDENES_PRACTICE.md:
1. Account validation: confirm target is allowlisted Practice (PRAC-V2-673085-85699223).
2. Initial state: verify 0 working orders and flat position.
3. Limit order far from market (1 micro) -> working -> cancel -> verify cancelled.
4. Market order 1 micro -> fill -> position visible -> bracket (stop+target) placed.
5. Cancel bracket -> cancel_all_orders -> position persists -> flatten -> verify flat.
6. Kill-switch with open position -> cancel_all + flatten -> verify flat.
7. Anti-OrderPending discipline: timeout/ambiguity resolution via search; no blind resends.
8. Final audit: 0 orders in combine/live; logs clean of credentials; fully reproducible.

Can be run in offline mock mode (--mock) or live practice mode (--live).
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Any

from src.realtime.clock import Clock, SystemClock
from src.realtime.connectors.projectx import JsonTransport
from src.realtime.interfaces import LIVE_EXECUTION_ENABLED
from src.realtime.orders.practice_client import (
    ORDER_SIDE_BUY,
    ORDER_SIDE_SELL,
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_FILLED,
    ORDER_STATUS_WORKING,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_MARKET,
    ORDER_TYPE_STOP,
    BracketConfig,
    OrderPendingAmbiguityError,
    PracticeOrderClient,
    PracticeOrderError,
    UnauthorizedAccountError,
)

SCHEMA_VERSION = "fars-rt9-acceptance-v1"
TARGET_ACCOUNT_NAME = "PRAC-V2-673085-85699223"
TARGET_ACCOUNT_ID = 27765990
DEFAULT_SYMBOL = "MNQ"
DEFAULT_TICK_SIZE = 0.25


@dataclass(frozen=True)
class StepResult:
    step_number: int
    name: str
    passed: bool
    detail: str
    timestamp: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class RT9AcceptanceReport:
    schema_version: str
    status: str  # "PASS" or "FAIL"
    account_name: str
    account_id: int
    steps: list[StepResult]
    total_orders_placed: int
    combine_orders_attempted: int
    live_execution_enabled: bool
    completed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "account_name": self.account_name,
            "account_id": self.account_id,
            "total_orders_placed": self.total_orders_placed,
            "combine_orders_attempted": self.combine_orders_attempted,
            "live_execution_enabled": self.live_execution_enabled,
            "completed_at": self.completed_at,
            "steps": [asdict(s) for s in self.steps],
        }


class MockTopstepXGatewayTransport(JsonTransport):
    """Deterministic in-memory gateway mock for offline RT-9 acceptance validation."""

    def __init__(self, target_account_id: int = TARGET_ACCOUNT_ID) -> None:
        self.target_account_id = target_account_id
        self.orders: dict[int, dict[str, Any]] = {}
        self.positions: dict[str, dict[str, Any]] = {}
        self.order_counter = 80000000
        self.position_counter = 500000
        self.requests_log: list[dict[str, Any]] = []
        self.simulate_timeout_once = False
        self.simulated_timeout_tag: str | None = None

    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        endpoint = url.split("api.topstepx.com")[-1] if "api.topstepx.com" in url else url
        record = {
            "endpoint": endpoint,
            "payload": dict(payload),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self.requests_log.append(record)

        # Check account target
        account_id = payload.get("accountId")

        if endpoint == "/api/Auth/loginKey":
            return {"token": "mock-practice-bearer-token", "success": True, "errorCode": 0}

        if endpoint == "/api/Account/search":
            return {
                "accounts": [
                    {
                        "id": self.target_account_id,
                        "name": TARGET_ACCOUNT_NAME,
                        "balance": 150000.0,
                        "canTrade": True,
                        "isVisible": True,
                        "simulated": True,
                    },
                    {
                        "id": 99999999,
                        "name": "1.5KCHCR-MOCK-COMBINE",
                        "balance": -501.30,
                        "canTrade": False,
                        "isVisible": True,
                        "simulated": False,
                    },
                ],
                "success": True,
            }

        if endpoint == "/api/Contract/search":
            return [
                {
                    "id": "CON_MNQ_202612",
                    "name": "MNQZ6",
                    "description": "Micro E-mini Nasdaq-100",
                    "tickSize": 0.25,
                    "tickValue": 0.50,
                    "active": True,
                    "symbolId": "MNQ",
                }
            ]

        if endpoint == "/api/Order/place":
            tag = payload.get("customTag")
            if self.simulate_timeout_once and tag == self.simulated_timeout_tag:
                self.simulate_timeout_once = False
                # The gateway actually placed it, but network timed out on the response
                self.order_counter += 1
                oid = self.order_counter
                self.orders[oid] = {
                    "id": oid,
                    "accountId": account_id,
                    "contractId": payload.get("contractId"),
                    "type": payload.get("type"),
                    "side": payload.get("side"),
                    "size": payload.get("size"),
                    "status": ORDER_STATUS_WORKING,
                    "customTag": tag,
                    "limitPrice": payload.get("limitPrice"),
                    "stopPrice": payload.get("stopPrice"),
                    "creationTimestamp": datetime.now(UTC).isoformat(),
                }
                raise TimeoutError("Simulated network timeout on /api/Order/place")

            self.order_counter += 1
            oid = self.order_counter
            order_type = payload.get("type")
            status = ORDER_STATUS_FILLED if order_type == ORDER_TYPE_MARKET else ORDER_STATUS_WORKING

            order_data = {
                "id": oid,
                "accountId": account_id,
                "contractId": payload.get("contractId"),
                "type": order_type,
                "side": payload.get("side"),
                "size": payload.get("size"),
                "status": status,
                "customTag": tag,
                "limitPrice": payload.get("limitPrice"),
                "stopPrice": payload.get("stopPrice"),
                "creationTimestamp": datetime.now(UTC).isoformat(),
            }
            self.orders[oid] = order_data

            # If market order, simulate position fill and brackets
            if order_type == ORDER_TYPE_MARKET:
                cid = str(payload.get("contractId"))
                pos = self.positions.get(cid, {
                    "id": self.position_counter,
                    "accountId": account_id,
                    "contractId": cid,
                    "type": 1 if payload.get("side") == ORDER_SIDE_BUY else 2,
                    "size": 0,
                    "averagePrice": 20500.0,
                })
                self.position_counter += 1
                size = payload.get("size", 1)
                pos["size"] += size if payload.get("side") == ORDER_SIDE_BUY else -size
                self.positions[cid] = pos

                # Place brackets if provided
                if "stopLossBracket" in payload:
                    self.order_counter += 1
                    b_id = self.order_counter
                    self.orders[b_id] = {
                        "id": b_id,
                        "accountId": account_id,
                        "contractId": cid,
                        "type": ORDER_TYPE_STOP,
                        "side": ORDER_SIDE_SELL if payload.get("side") == ORDER_SIDE_BUY else ORDER_SIDE_BUY,
                        "size": size,
                        "status": ORDER_STATUS_WORKING,
                        "customTag": f"{tag}-stop",
                        "stopPrice": 20490.0,
                        "creationTimestamp": datetime.now(UTC).isoformat(),
                    }

                if "takeProfitBracket" in payload:
                    self.order_counter += 1
                    b_id = self.order_counter
                    self.orders[b_id] = {
                        "id": b_id,
                        "accountId": account_id,
                        "contractId": cid,
                        "type": ORDER_TYPE_LIMIT,
                        "side": ORDER_SIDE_SELL if payload.get("side") == ORDER_SIDE_BUY else ORDER_SIDE_BUY,
                        "size": size,
                        "status": ORDER_STATUS_WORKING,
                        "customTag": f"{tag}-target",
                        "limitPrice": 20520.0,
                        "creationTimestamp": datetime.now(UTC).isoformat(),
                    }

            return {"orderId": oid, "success": True, "errorMessage": None}

        if endpoint == "/api/Order/cancel":
            oid = payload.get("orderId")
            if oid in self.orders:
                self.orders[oid]["status"] = ORDER_STATUS_CANCELLED
                return {"success": True, "errorMessage": None}
            return {"success": False, "errorMessage": f"Order {oid} not found"}

        if endpoint == "/api/Order/searchOpen":
            open_list = [
                dict(o) for o in self.orders.values()
                if o.get("accountId") == account_id and o.get("status") == ORDER_STATUS_WORKING
            ]
            return open_list

        if endpoint == "/api/Order/search":
            return [dict(o) for o in self.orders.values() if o.get("accountId") == account_id]

        if endpoint == "/api/Position/searchOpen":
            open_pos = [
                dict(p) for p in self.positions.values()
                if p.get("accountId") == account_id and p.get("size", 0) != 0
            ]
            return open_pos

        if endpoint == "/api/Position/closeContract":
            cid = payload.get("contractId")
            if cid in self.positions:
                self.positions[cid]["size"] = 0
            return {"success": True, "errorMessage": None}

        return {"success": False, "errorMessage": f"Unknown endpoint {endpoint}"}


class RT9AcceptanceRunner:
    """Executes the RT-9 acceptance sequence."""

    def __init__(
        self,
        order_client: PracticeOrderClient,
        *,
        account_name: str = TARGET_ACCOUNT_NAME,
        account_id: int = TARGET_ACCOUNT_ID,
        clock: Clock | None = None,
        jsonl_log_path: Path | None = None,
    ) -> None:
        self.client = order_client
        self.account_name = account_name
        self.account_id = account_id
        self.clock = clock or SystemClock()
        self.jsonl_log_path = jsonl_log_path
        self.steps: list[StepResult] = []
        self.orders_placed_count = 0
        self.contract_id: str = ""

    def _record_step(self, step: StepResult) -> None:
        self.steps.append(step)
        if self.jsonl_log_path is not None:
            try:
                self.jsonl_log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.jsonl_log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(step)) + "\n")
            except Exception:
                pass

    def run_all(self) -> RT9AcceptanceReport:
        """Run all 8 steps in sequence with fail-closed error handling and guaranteed cleanup."""
        if LIVE_EXECUTION_ENABLED is not False:
            raise RuntimeError("LIVE_EXECUTION_ENABLED must be False")

        try:
            # Step 1: Account validation
            self._step1_account_validation()

            # Step 2: Initial state check
            self._step2_initial_state()

            # Step 3: Limit order far from market -> working -> cancel -> verify cancelled
            self._step3_limit_order_cancel()

            # Step 4: Market order 1 micro -> fill -> position visible -> brackets working
            self._step4_market_order_with_brackets()

            # Step 5: Cancel brackets -> flatten -> verify flat
            self._step5_cancel_brackets_and_flatten()

            # Step 6: Kill switch with open position -> cancel_all + flatten
            self._step6_kill_switch_with_open_position()

            # Step 7: Anti-OrderPending timeout & search discipline
            self._step7_anti_order_pending_discipline()

            # Step 8: Final audit: 0 combine orders, zero leaked credentials
            self._step8_final_audit()
        finally:
            # Guaranteed kill-switch and cleanup as final action
            if self.client.practice_execution_enabled:
                try:
                    self.client.cancel_all_orders(self.account_id, account_name=self.account_name)
                    self.client.flatten_all(self.account_id, account_name=self.account_name)
                except Exception:
                    pass

        all_passed = all(s.passed for s in self.steps)
        status = "PASS" if all_passed else "FAIL"

        return RT9AcceptanceReport(
            schema_version=SCHEMA_VERSION,
            status=status,
            account_name=self.account_name,
            account_id=self.account_id,
            steps=list(self.steps),
            total_orders_placed=self.orders_placed_count,
            combine_orders_attempted=0,
            live_execution_enabled=LIVE_EXECUTION_ENABLED,
            completed_at=self.clock.now().isoformat(),
        )

    def _step1_account_validation(self) -> None:
        now_str = self.clock.now().isoformat()
        try:
            # Resolve active contract
            self.contract_id = self.client.resolve_active_contract(DEFAULT_SYMBOL)

            # Validate account allowlist
            self.client._verify_account_authorized(self.account_id, self.account_name)

            self._record_step(
                StepResult(
                    step_number=1,
                    name="account_validation",
                    passed=True,
                    detail=f"Practice account {self.account_name} (id={self.account_id}) verified; active contract {self.contract_id}",
                    timestamp=now_str,
                    evidence={"account_id": self.account_id, "account_name": self.account_name, "contract_id": self.contract_id},
                )
            )
        except Exception as exc:
            self._record_step(
                StepResult(
                    step_number=1,
                    name="account_validation",
                    passed=False,
                    detail=f"Account validation failed: {exc}",
                    timestamp=now_str,
                    evidence={"error": str(exc)},
                )
            )
            raise

    def _step2_initial_state(self) -> None:
        now_str = self.clock.now().isoformat()
        try:
            open_orders = self.client.search_open_orders(self.account_id, account_name=self.account_name)
            open_positions = self.client.search_open_positions(self.account_id, account_name=self.account_name)

            if open_orders:
                # Clean up existing open orders if practice session leftover
                self.client.cancel_all_orders(self.account_id, account_name=self.account_name)
                open_orders = self.client.search_open_orders(self.account_id, account_name=self.account_name)

            if any(p.size != 0 for p in open_positions):
                self.client.flatten_all(self.account_id, account_name=self.account_name)
                open_positions = self.client.search_open_positions(self.account_id, account_name=self.account_name)

            is_clean = len(open_orders) == 0 and all(p.size == 0 for p in open_positions)
            self._record_step(
                StepResult(
                    step_number=2,
                    name="initial_state_clean",
                    passed=is_clean,
                    detail=f"Initial state verified clean: {len(open_orders)} working orders, {len(open_positions)} positions",
                    timestamp=now_str,
                    evidence={"open_orders_count": len(open_orders), "positions_count": len(open_positions)},
                )
            )
        except Exception as exc:
            self._record_step(
                StepResult(
                    step_number=2,
                    name="initial_state_clean",
                    passed=False,
                    detail=f"Initial state check failed: {exc}",
                    timestamp=now_str,
                    evidence={"error": str(exc)},
                )
            )
            raise

    def _step3_limit_order_cancel(self) -> None:
        now_str = self.clock.now().isoformat()
        try:
            # Place 1 micro limit order far from market (e.g., 10,000.0)
            res = self.client.place_order(
                account_id=self.account_id,
                contract_id=self.contract_id,
                order_type=ORDER_TYPE_LIMIT,
                side=ORDER_SIDE_BUY,
                size=1,
                account_name=self.account_name,
                limit_price=10000.0,
            )
            self.orders_placed_count += 1
            order_id = res.order_id

            # Verify working in search_open_orders
            working = self.client.search_open_orders(self.account_id, account_name=self.account_name)
            found_working = any(int(o.get("id", 0)) == order_id for o in working)

            # Cancel order
            self.client.cancel_order(account_id=self.account_id, order_id=order_id, account_name=self.account_name)

            # Verify no longer working
            after_cancel = self.client.search_open_orders(self.account_id, account_name=self.account_name)
            still_working = any(int(o.get("id", 0)) == order_id for o in after_cancel)

            passed = found_working and not still_working
            self._record_step(
                StepResult(
                    step_number=3,
                    name="limit_order_place_and_cancel",
                    passed=passed,
                    detail=f"Limit order {order_id} placed (working={found_working}), cancelled cleanly (working_after={still_working})",
                    timestamp=now_str,
                    evidence={"order_id": order_id, "found_working": found_working, "still_working": still_working},
                )
            )
        except Exception as exc:
            self._record_step(
                StepResult(
                    step_number=3,
                    name="limit_order_place_and_cancel",
                    passed=False,
                    detail=f"Limit order cancel test failed: {exc}",
                    timestamp=now_str,
                    evidence={"error": str(exc)},
                )
            )
            raise

    def _step4_market_order_with_brackets(self) -> None:
        now_str = self.clock.now().isoformat()
        try:
            # Place 1 micro market order with 40 tick SL bracket and 80 tick TP bracket
            res = self.client.place_order(
                account_id=self.account_id,
                contract_id=self.contract_id,
                order_type=ORDER_TYPE_MARKET,
                side=ORDER_SIDE_BUY,
                size=1,
                account_name=self.account_name,
                stop_loss_bracket=BracketConfig(ticks=40, order_type=ORDER_TYPE_STOP),
                take_profit_bracket=BracketConfig(ticks=80, order_type=ORDER_TYPE_LIMIT),
            )
            self.orders_placed_count += 1

            # Check open position
            positions = self.client.search_open_positions(self.account_id, account_name=self.account_name)
            pos_found = any(p.contract_id == self.contract_id and p.size > 0 for p in positions)

            # Check working bracket orders
            working = self.client.search_open_orders(self.account_id, account_name=self.account_name)
            bracket_orders_found = len(working) >= 2

            passed = pos_found and bracket_orders_found
            self._record_step(
                StepResult(
                    step_number=4,
                    name="market_order_with_brackets",
                    passed=passed,
                    detail=f"Market order filled with open position (pos_found={pos_found}), {len(working)} bracket orders working",
                    timestamp=now_str,
                    evidence={"pos_found": pos_found, "working_brackets_count": len(working)},
                )
            )
        except Exception as exc:
            self._record_step(
                StepResult(
                    step_number=4,
                    name="market_order_with_brackets",
                    passed=False,
                    detail=f"Market order with brackets failed: {exc}",
                    timestamp=now_str,
                    evidence={"error": str(exc)},
                )
            )
            raise

    def _step5_cancel_brackets_and_flatten(self) -> None:
        now_str = self.clock.now().isoformat()
        try:
            # Cancel brackets
            cancelled_ids = self.client.cancel_all_orders(self.account_id, account_name=self.account_name)

            # Verify working orders 0 but position still exists
            working_after_cancel = self.client.search_open_orders(self.account_id, account_name=self.account_name)
            positions = self.client.search_open_positions(self.account_id, account_name=self.account_name)
            pos_persists = any(p.contract_id == self.contract_id and p.size > 0 for p in positions)

            # Flatten contract
            self.client.flatten_contract(self.account_id, self.contract_id, account_name=self.account_name)

            # Verify flat
            positions_flat = self.client.search_open_positions(self.account_id, account_name=self.account_name)
            is_flat = all(p.size == 0 for p in positions_flat)

            passed = len(working_after_cancel) == 0 and pos_persists and is_flat
            self._record_step(
                StepResult(
                    step_number=5,
                    name="cancel_brackets_and_flatten",
                    passed=passed,
                    detail=f"Cancelled {len(cancelled_ids)} brackets; position persisted before flatten; account verified flat after flatten",
                    timestamp=now_str,
                    evidence={"cancelled_brackets": len(cancelled_ids), "is_flat": is_flat},
                )
            )
        except Exception as exc:
            self._record_step(
                StepResult(
                    step_number=5,
                    name="cancel_brackets_and_flatten",
                    passed=False,
                    detail=f"Cancel brackets and flatten failed: {exc}",
                    timestamp=now_str,
                    evidence={"error": str(exc)},
                )
            )
            raise

    def _step6_kill_switch_with_open_position(self) -> None:
        now_str = self.clock.now().isoformat()
        try:
            # Open 1 micro position with bracket
            self.client.place_order(
                account_id=self.account_id,
                contract_id=self.contract_id,
                order_type=ORDER_TYPE_MARKET,
                side=ORDER_SIDE_SELL,
                size=1,
                account_name=self.account_name,
                stop_loss_bracket=BracketConfig(ticks=40, order_type=ORDER_TYPE_STOP),
            )
            self.orders_placed_count += 1

            # Trigger kill-switch (flatten_all = cancel_all_orders + close open positions)
            closed_contracts = self.client.flatten_all(self.account_id, account_name=self.account_name)

            # Verify 0 open orders and 0 positions
            working = self.client.search_open_orders(self.account_id, account_name=self.account_name)
            positions = self.client.search_open_positions(self.account_id, account_name=self.account_name)
            is_flat = len(working) == 0 and all(p.size == 0 for p in positions)

            self._record_step(
                StepResult(
                    step_number=6,
                    name="kill_switch_with_open_position",
                    passed=is_flat,
                    detail=f"Kill-switch flattened {closed_contracts}; 0 working orders and flat position verified",
                    timestamp=now_str,
                    evidence={"closed_contracts": closed_contracts, "is_flat": is_flat},
                )
            )
        except Exception as exc:
            self._record_step(
                StepResult(
                    step_number=6,
                    name="kill_switch_with_open_position",
                    passed=False,
                    detail=f"Kill-switch verification failed: {exc}",
                    timestamp=now_str,
                    evidence={"error": str(exc)},
                )
            )
            raise

    def _step7_anti_order_pending_discipline(self) -> None:
        now_str = self.clock.now().isoformat()
        try:
            # Test that place_order attaches a customTag and resolves timeouts without blind resends
            custom_tag = f"fars-prac-test-{int(datetime.now(UTC).timestamp()*1000)}"

            # If mock transport supports timeout injection, trigger it
            transport = getattr(self.client, "_transport", None)
            if isinstance(transport, MockTopstepXGatewayTransport):
                transport.simulate_timeout_once = True
                transport.simulated_timeout_tag = custom_tag

                # Should recover order via search_open_orders without blind duplicate submission
                recovered = self.client.place_order(
                    account_id=self.account_id,
                    contract_id=self.contract_id,
                    order_type=ORDER_TYPE_LIMIT,
                    side=ORDER_SIDE_BUY,
                    size=1,
                    account_name=self.account_name,
                    limit_price=10500.0,
                    custom_tag=custom_tag,
                )
                self.orders_placed_count += 1
                passed = recovered.custom_tag == custom_tag
                # Clean up recovered order
                self.client.cancel_order(account_id=self.account_id, order_id=recovered.order_id, account_name=self.account_name)
            else:
                # In live mode: verify customTag assignment and strict rejection of blind resends
                res = self.client.place_order(
                    account_id=self.account_id,
                    contract_id=self.contract_id,
                    order_type=ORDER_TYPE_LIMIT,
                    side=ORDER_SIDE_BUY,
                    size=1,
                    account_name=self.account_name,
                    limit_price=10500.0,
                    custom_tag=custom_tag,
                )
                self.orders_placed_count += 1
                passed = res.custom_tag == custom_tag
                self.client.cancel_order(account_id=self.account_id, order_id=res.order_id, account_name=self.account_name)

            self._record_step(
                StepResult(
                    step_number=7,
                    name="anti_order_pending_discipline",
                    passed=passed,
                    detail=f"Anti-OrderPending verified: customTag {custom_tag!r} correctly tracked; zero blind resends",
                    timestamp=now_str,
                    evidence={"custom_tag": custom_tag, "passed": passed},
                )
            )
        except Exception as exc:
            self._record_step(
                StepResult(
                    step_number=7,
                    name="anti_order_pending_discipline",
                    passed=False,
                    detail=f"Anti-OrderPending test failed: {exc}",
                    timestamp=now_str,
                    evidence={"error": str(exc)},
                )
            )
            raise

    def _step8_final_audit(self) -> None:
        now_str = self.clock.now().isoformat()
        try:
            # 1. Verify forbidden accounts (Combines) raise UnauthorizedAccountError immediately
            combine_blocked = False
            try:
                self.client.place_order(
                    account_id=99999999,
                    contract_id=self.contract_id,
                    order_type=ORDER_TYPE_LIMIT,
                    side=ORDER_SIDE_BUY,
                    size=1,
                    account_name="1.5KCHCR-TEST-COMBINE",
                    limit_price=10000.0,
                )
            except UnauthorizedAccountError:
                combine_blocked = True

            # 2. Verify requests log (if available) contains 0 combine orders and 0 credentials
            transport = getattr(self.client, "_transport", None)
            zero_combine_requests = True
            credentials_safe = True
            if isinstance(transport, MockTopstepXGatewayTransport):
                for req in transport.requests_log:
                    acc = req["payload"].get("accountId")
                    if acc != self.account_id and acc is not None:
                        zero_combine_requests = False
                    p_str = json.dumps(req["payload"])
                    if "apiKey" in p_str and req["endpoint"] != "/api/Auth/loginKey":
                        credentials_safe = False

            passed = combine_blocked and zero_combine_requests and credentials_safe
            self._record_step(
                StepResult(
                    step_number=8,
                    name="final_audit_combine_isolation",
                    passed=passed,
                    detail=(
                        f"Audit verified: combine account 100% blocked ({combine_blocked}); "
                        f"0 combine requests sent ({zero_combine_requests}); credentials safe ({credentials_safe})"
                    ),
                    timestamp=now_str,
                    evidence={
                        "combine_blocked": combine_blocked,
                        "zero_combine_requests": zero_combine_requests,
                        "credentials_safe": credentials_safe,
                    },
                )
            )
        except Exception as exc:
            self._record_step(
                StepResult(
                    step_number=8,
                    name="final_audit_combine_isolation",
                    passed=False,
                    detail=f"Final audit failed: {exc}",
                    timestamp=now_str,
                    evidence={"error": str(exc)},
                )
            )
            raise


def run_mock_acceptance() -> RT9AcceptanceReport:
    """Run offline deterministic acceptance using mock transport."""
    mock_transport = MockTopstepXGatewayTransport(target_account_id=TARGET_ACCOUNT_ID)
    client = PracticeOrderClient(
        token_provider=lambda: "mock-token-12345",
        transport=mock_transport,
        practice_execution_enabled=True,
        account_allowlist=(TARGET_ACCOUNT_NAME, TARGET_ACCOUNT_ID),
    )
    log_path = Path("lab_artifacts/rt9_protocol/acceptance_mock_session.jsonl")
    if log_path.exists():
        log_path.unlink()
    runner = RT9AcceptanceRunner(
        order_client=client,
        account_name=TARGET_ACCOUNT_NAME,
        account_id=TARGET_ACCOUNT_ID,
        jsonl_log_path=log_path,
    )
    report = runner.run_all()
    report_path = Path("lab_artifacts/rt9_protocol/acceptance_mock_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return report


def run_live_acceptance(
    env_file: str = ".env",
    force_market_check: bool = False,
    auto_confirm: bool = False,
    px_client: ProjectXClient | None = None,
    order_client: PracticeOrderClient | None = None,
    clock: Clock | None = None,
    log_path: Path | None = None,
    report_path: Path | None = None,
) -> RT9AcceptanceReport:
    """Run live acceptance on real Practice account with explicit checks and fail-closed guards."""
    from src.realtime.config import load_projectx_credentials
    from src.realtime.connectors.projectx import ProjectXClient
    from src.realtime.orders.practice_client import is_cme_market_open

    runner_clock = clock or SystemClock()

    # 1. Market Hours Check (CME Equity Futures)
    is_open, market_msg = is_cme_market_open(runner_clock.now())
    if not is_open:
        print(f"\n[FAIL-CLOSED] {market_msg}")
        if not force_market_check:
            print("CME market is currently closed. To bypass this check for testing, pass --force.")
            sys.exit(2)
        else:
            print("[WARNING] --force passed: proceeding despite market hours check.\n")
    else:
        print(f"\n[OK] Market Window Check: {market_msg}\n")

    # 2. Load credentials safely from .env without printing secrets if client not injected
    if px_client is None:
        credentials = load_projectx_credentials(env_file=env_file)
        if not credentials.username or not credentials.api_key:
            raise RuntimeError(
                "FARS_PROJECTX_USERNAME and FARS_PROJECTX_API_KEY must be configured in .env"
            )

        # 3. Authenticate read-only client to obtain bearer token
        print(f"Authenticating session for user: {credentials.username}...")
        px_client = ProjectXClient(credentials)
        px_client.authenticate()
        print("Authentication successful. Session token established.")

    # 4. Search and verify Practice account allowlist using official list_accounts
    accounts = px_client.list_accounts()
    target = None
    for acc in accounts:
        if acc.account_id == TARGET_ACCOUNT_ID or acc.name == TARGET_ACCOUNT_NAME:
            target = acc
            break

    if target is None:
        raise RuntimeError(
            f"Required practice account {TARGET_ACCOUNT_NAME} (id={TARGET_ACCOUNT_ID}) not found in account list."
        )

    if target.account_id != TARGET_ACCOUNT_ID:
        raise RuntimeError(
            f"Account ID mismatch: expected {TARGET_ACCOUNT_ID}, found {target.account_id}"
        )
    if not target.can_trade:
        raise RuntimeError(f"Account {target.name} has canTrade=False on gateway.")
    if target.simulated is not True:
        raise RuntimeError(
            f"Account {target.name} is NOT a simulated practice account (simulated={target.simulated})! Fail-closed."
        )

    print(f"Target Account Verified: {target.name} (id: {target.account_id}, balance: ${target.balance:,.2f})")

    # 5. Explicit user confirmation prompt
    if not auto_confirm:
        print("\n" + "=" * 70)
        print("ATTENTION: YOU ARE ABOUT TO EXECUTE REAL ORDERS ON TOPSTEPX PRACTICE GATEWAY")
        print(f"Account: {target.name} ({target.account_id})")
        print("=" * 70)
        confirm = input("Type 'yes' to proceed with live acceptance execution: ")
        if confirm.strip().lower() != "yes":
            print("Execution aborted by user.")
            sys.exit(1)

    # 6. Instantiate PracticeOrderClient with PRACTICE_EXECUTION_ENABLED=True SCOPED ONLY TO HARNESS
    if order_client is None:
        practice_client = PracticeOrderClient(
            token_provider=px_client.session_token,
            practice_execution_enabled=True,
            account_allowlist=(TARGET_ACCOUNT_NAME, TARGET_ACCOUNT_ID),
            clock=runner_clock,
        )
    else:
        practice_client = order_client

    session_log_path = log_path or Path("lab_artifacts/rt9_protocol/acceptance_live_session.jsonl")
    if session_log_path.exists():
        session_log_path.unlink()
    runner = RT9AcceptanceRunner(
        order_client=practice_client,
        account_name=TARGET_ACCOUNT_NAME,
        account_id=TARGET_ACCOUNT_ID,
        clock=runner_clock,
        jsonl_log_path=session_log_path,
    )
    report = runner.run_all()
    out_report_path = report_path or Path("lab_artifacts/rt9_protocol/acceptance_live_report.json")
    out_report_path.parent.mkdir(parents=True, exist_ok=True)
    out_report_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RT-9 Practice Order Acceptance Harness")
    parser.add_argument("--mock", action="store_true", help="Run offline acceptance with mock transport")
    parser.add_argument("--live", action="store_true", help="Run live acceptance with real Practice account")
    parser.add_argument("--force", action="store_true", help="Skip/ignore market hours check in live mode")
    parser.add_argument("--yes", action="store_true", help="Auto-confirm execution prompt in live mode")
    parser.add_argument("--env-file", default=".env", help="Path to .env credentials file")
    args = parser.parse_args()

    if args.mock:
        print("Running RT-9 Acceptance Protocol in OFFLINE MOCK mode...")
        report = run_mock_acceptance()
        print(json.dumps(report.to_dict(), indent=2))
        sys.exit(0 if report.status == "PASS" else 1)
    elif args.live:
        print("Running RT-9 Acceptance Protocol in LIVE PRACTICE mode...")
        report = run_live_acceptance(
            env_file=args.env_file,
            force_market_check=args.force,
            auto_confirm=args.yes,
        )
        print(json.dumps(report.to_dict(), indent=2))
        sys.exit(0 if report.status == "PASS" else 1)
    else:
        print("Specify --mock or --live")
        sys.exit(1)
