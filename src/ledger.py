"""把麦当劳历史订单账本化：花了多少钱、吃了多少次、哪些单品在重复。

数据来自 order-list（近 90 天的到店 + 外送订单，不含商城订单）。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")
CANCELLED_MARKERS = ("取消", "已关闭", "退款")


@dataclass
class OrderLine:
    name: str
    quantity: int = 1
    code: str = ""


@dataclass
class Order:
    order_id: str
    created_at: datetime | None
    store_code: str
    store_name: str
    status: str
    amount: float
    lines: list[OrderLine] = field(default_factory=list)

    @property
    def is_cancelled(self) -> bool:
        return any(marker in self.status for marker in CANCELLED_MARKERS)

    def items(self) -> Iterable[str]:
        for line in self.lines:
            for _ in range(max(1, line.quantity)):
                yield line.name


@dataclass
class Ledger:
    orders: list[Order] = field(default_factory=list)
    now: datetime | None = None

    # ------------------------------------------------------------------ 构造

    @classmethod
    def from_payload(cls, payload: Any, now: datetime | None = None) -> "Ledger":
        ledger = cls(now=now)
        for raw in _iter_orders(payload):
            order = _parse_order(raw)
            if order is not None:
                ledger.orders.append(order)
        ledger.orders.sort(key=lambda o: o.created_at or datetime.min, reverse=True)
        return ledger

    # ------------------------------------------------------------------ 统计

    def valid(self) -> list[Order]:
        return [order for order in self.orders if not order.is_cancelled]

    def since(self, days: int) -> list[Order]:
        """取最近 N 天的有效订单。"""
        base = self.now or datetime.now()
        threshold = base - timedelta(days=days)
        return [
            order
            for order in self.valid()
            if order.created_at is None or order.created_at >= threshold
        ]

    def total_spend(self, days: int | None = None) -> float:
        pool = self.valid() if days is None else self.since(days)
        return round(sum(order.amount for order in pool), 2)

    def total_orders(self, days: int | None = None) -> int:
        pool = self.valid() if days is None else self.since(days)
        return len(pool)

    def item_counter(self, days: int | None = None) -> Counter[str]:
        pool = self.valid() if days is None else self.since(days)
        counter: Counter[str] = Counter()
        for order in pool:
            counter.update(order.items())
        return counter

    def repeat_counts(self, days: int = 7) -> Counter[str]:
        return self.item_counter(days)

    def average_daily_kcal(self, kcal_lookup, days: int = 14) -> float:
        """按订单里的单品名称查营养表，估算近期日均摄入热量。"""
        pool = self.since(days)
        if not pool:
            return 0.0
        total = 0.0
        for order in pool:
            for name in order.items():
                food = kcal_lookup(name)
                if food is not None:
                    total += food.energy_kcal
        span = max(1, days)
        return round(total / span, 1)

    def default_store(self) -> tuple[str, str]:
        """从最近一笔有效订单里挖出默认门店，作为点餐链路的起点。

        query-nearby-stores 依赖定位，冷启动时往往拿不到门店；
        但 order-list 会带上历史门店的 storeCode，可直接复用。
        """
        for order in self.valid():
            if order.store_code:
                return order.store_code, order.store_name
        for order in self.orders:
            if order.store_code:
                return order.store_code, order.store_name
        return "", ""

    def summary(self, days: int | None = None) -> dict[str, Any]:
        pool = self.valid() if days is None else self.since(days)
        counter = self.item_counter(days)
        top = counter.most_common(5)
        spend = round(sum(o.amount for o in pool), 2)
        return {
            "orders": len(pool),
            "spend": spend,
            "avg_order": round(spend / len(pool), 2) if pool else 0.0,
            "top_items": [{"name": name, "count": count} for name, count in top],
            "first_order": pool[-1].created_at.strftime("%Y-%m-%d") if pool and pool[-1].created_at else None,
            "last_order": pool[0].created_at.strftime("%Y-%m-%d") if pool and pool[0].created_at else None,
        }


# ---------------------------------------------------------------------- 解析


def _iter_orders(payload: Any) -> list[dict]:
    """从各种嵌套形态里捞出订单数组。"""
    current = payload
    for _ in range(4):
        if isinstance(current, list):
            return [item for item in current if isinstance(item, dict)]
        if isinstance(current, dict):
            for key in ("list", "orders", "orderList", "records", "data", "result"):
                value = current.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
                if isinstance(value, dict):
                    current = value
                    break
            else:
                return []
        else:
            return []
    return []


def _parse_order(raw: dict) -> Order | None:
    order_id = str(raw.get("orderId") or raw.get("id") or "").strip()
    if not order_id:
        return None

    return Order(
        order_id=order_id,
        created_at=_parse_time(raw.get("createTime") or raw.get("orderTime") or raw.get("createDate")),
        store_code=str(raw.get("storeCode") or "").strip(),
        store_name=str(raw.get("storeName") or "").strip(),
        status=str(raw.get("orderStatus") or raw.get("status") or "").strip(),
        amount=_parse_amount(raw.get("realTotalAmount") or raw.get("totalAmount") or raw.get("amount")),
        lines=_parse_lines(raw.get("orderProductList") or raw.get("products") or []),
    )


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value or "").strip().replace("/", "-").replace("T", " ")
    if not text:
        return None
    if "." in text and " " in text:
        text = text.split(".")[0]
    for fmt in DATETIME_FORMATS:
        try:
            return datetime.strptime(text[:19] if len(text) > 19 else text, fmt)
        except ValueError:
            continue
    return None


def _parse_amount(value: Any) -> float:
    try:
        return float(str(value).replace("元", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _parse_lines(raw_lines: Any) -> list[OrderLine]:
    lines: list[OrderLine] = []
    if not isinstance(raw_lines, list):
        return lines
    for item in raw_lines:
        if not isinstance(item, dict):
            continue
        name = str(item.get("productName") or item.get("name") or "").strip()
        if not name:
            continue
        try:
            quantity = int(item.get("quantity") or 1)
        except (TypeError, ValueError):
            quantity = 1
        lines.append(
            OrderLine(
                name=name,
                quantity=max(1, quantity),
                code=str(item.get("productCode") or item.get("code") or ""),
            )
        )
    return lines
