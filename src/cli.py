#!/usr/bin/env python
"""麦麦冷静期 · 命令行入口。

用法示例：
    export MCD_MCP_TOKEN=<你的 Token>
    python src/cli.py verdict 巨无霸 中薯条 可乐中杯
    python src/cli.py ledger
    python src/cli.py menu --store <门店编号>
    python src/cli.py lookup 巨无霸

不带任何子命令时默认走 verdict，直接把参数当餐品名：
    python src/cli.py 巨无霸 中薯条
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ledger import Ledger  # noqa: E402
from mcd_mcp import McdMcpClient, McpError  # noqa: E402
from nutrition import (  # noqa: E402
    Food,
    NutritionTable,
    extract_table_text,
    parse_nutrition_table,
)
from report import render_html  # noqa: E402
from verdict import DEFAULT_KCAL_BUDGET, build_verdict  # noqa: E402

LEVEL_STYLE = {
    "green": ("\033[42;30m", "可以吃"),
    "yellow": ("\033[43;30m", "换个搭配更稳"),
    "red": ("\033[41;97m", "再想想"),
}
LEVEL_BADGE = {"green": "[ OK ]", "yellow": "[WARN]", "red": "[STOP]"}

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RED = "\033[31m"


def enable_ansi() -> None:
    if os.name == "nt":
        os.system("")


def use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty() or os.environ.get("FORCE_COLOR") == "1"


def paint(text: str, code: str, colored: bool) -> str:
    return f"{code}{text}{RESET}" if colored else text


def bar(width: int = 58, char: str = "-") -> str:
    return char * width


def store_display(store_code: str, store_name: str, share: bool) -> str:
    """门店展示文本。

    --share 用于截图/分享场景：门店名会暴露你常去的位置，
    所以只保留品牌，把门店名和编号一并抹掉。
    """
    if not store_code:
        return ""
    if share:
        return "麦当劳（门店信息已脱敏）"
    return f"{store_name}（{store_code}）" if store_name else store_code


# --------------------------------------------------------------------- 数据层


def load_market_data(client: McdMcpClient) -> dict:
    """一次性拉齐冷启动所需的公共数据。"""
    data: dict = {}

    try:
        payload = client.call("list-nutrition-foods")
        data["nutrition"] = parse_nutrition_table(extract_table_text(payload))
    except McpError as exc:
        print(f"[WARN] 营养库拉取失败：{exc}", file=sys.stderr)
        data["nutrition"] = NutritionTable()

    try:
        data["orders_raw"] = client.call("order-list")
    except McpError:
        data["orders_raw"] = {}

    try:
        now_info = client.call("now-time-info")
        data["now_info"] = now_info
    except McpError:
        data["now_info"] = {}

    try:
        data["coupons"] = client.call("query-my-coupons", {"page": 1, "pageSize": 200})
    except McpError:
        data["coupons"] = {}

    return data


def coupon_count(coupons) -> int:
    current = coupons
    for _ in range(4):
        if isinstance(current, list):
            return len(current)
        if isinstance(current, dict):
            for key in ("list", "coupons", "data", "result"):
                if key in current:
                    current = current[key]
                    break
            else:
                return 0
        else:
            return 0
    return 0


def resolve_now_hour(now_info) -> int | None:
    """从 now-time-info 里读出小时；拿不到就回退本机时间。"""
    text = now_info if isinstance(now_info, str) else json.dumps(now_info, ensure_ascii=False)
    import re

    match = re.search(r"(\d{2}):(\d{2})", text or "")
    if match:
        hour = int(match.group(1))
        if 0 <= hour <= 23:
            return hour
    from datetime import datetime

    return datetime.now().hour


# --------------------------------------------------------------------- 渲染


def render_verdict(result, cart_labels: list[str], store_label: str, colored: bool) -> None:
    badge = LEVEL_BADGE.get(result.level, "[????]")
    color = {"green": CYAN, "yellow": YELLOW, "red": RED}.get(result.level, "")

    print()
    print(paint("=" * 58, DIM, colored))
    print(paint("  麦麦冷静期", BOLD, colored) + paint("   下单前 30 秒，先读一下你的账", DIM, colored))
    if store_label:
        print(paint(f"  门店：{store_label}", DIM, colored))
    print(paint("=" * 58, DIM, colored))
    print()

    print(f"  {paint('购物车', BOLD, colored)}  {'  '.join(cart_labels)}")
    totals = result.totals
    print(
        f"  {paint('合  计', BOLD, colored)}  "
        f"{totals.get('kcal', 0):.0f} kcal · 蛋白 {totals.get('protein', 0):.0f} g · "
        f"脂肪 {totals.get('fat', 0):.0f} g · 碳水 {totals.get('carb', 0):.0f} g · "
        f"钠 {totals.get('sodium', 0):.0f} mg"
    )
    print()

    verdict_line = f"{badge}  {result.headline}"
    print(f"  {paint('判  决', BOLD, colored)}  {paint(verdict_line, color + BOLD, colored)}"
          f"    {paint(f'风险分 {result.score} / 15', DIM, colored)}")
    print(f"          {paint(result.advice, DIM, colored)}")
    print()

    for index, check in enumerate(result.checks, start=1):
        mark = "  " if check.penalty == 0 else paint(f"+{check.penalty}", YELLOW if check.penalty < 3 else RED, colored)
        print(f"  {index}. {check.label:<6} {mark:>5}   {check.detail}")
        if check.hint:
            print(f"     {paint('-> ' + check.hint, DIM, colored)}")

    if result.replacements:
        print()
        print(f"  {paint('替换建议', BOLD, colored)}")
        for item in result.replacements:
            saved = paint("省 %d kcal" % item["saved_kcal"], CYAN, colored)
            print(
                f"    {item['from']} -> {item['to']}   "
                f"{saved}（蛋白 {item['protein']} g）"
            )

    print()
    print(paint("-" * 58, DIM, colored))
    print(paint("  数据来自麦当劳 MCP · 输出仅供参考，不构成营养或医疗建议", DIM, colored))
    print()


def render_ledger(ledger: Ledger, colored: bool) -> None:
    print()
    print(paint("=" * 58, DIM, colored))
    print(paint("  麦麦账本", BOLD, colored))
    print(paint("=" * 58, DIM, colored))
    print()

    if not ledger.orders:
        print("  还没有历史订单。")
        print()
        return

    skipped = len(ledger.orders) - len(ledger.valid())

    for label, days in (("最近 7 天", 7), ("最近 30 天", 30), ("全部", None)):
        summary = ledger.summary(days)
        print(
            f"  {paint(label.ljust(10), BOLD, colored)}  "
            f"{summary['orders']:>3} 单 · 共 {summary['spend']:>7.2f} 元 · "
            f"单均 {summary['avg_order']:>6.2f} 元"
        )

    summary = ledger.summary(None)
    if skipped:
        print()
        print(f"  {paint(f'另有 {skipped} 笔已取消订单未计入', DIM, colored)}")
    if summary["top_items"]:
        print()
        print(f"  {paint('最常点', BOLD, colored)}")
        for item in summary["top_items"]:
            print(f"    {item['name']:<22} {item['count']} 次")

    repeats = ledger.repeat_counts(7)
    hot = [(name, count) for name, count in repeats.most_common(3) if count >= 2]
    if hot:
        print()
        print(f"  {paint('近 7 天复购提醒', BOLD, colored)}")
        for name, count in hot:
            print(f"    {name} 已经点了 {count} 次")

    print()


# --------------------------------------------------------------------- 子命令


def cmd_verdict(args: argparse.Namespace) -> int:
    colored = use_color()
    client = McdMcpClient()
    data = load_market_data(client)
    table: NutritionTable = data["nutrition"]

    if not len(table):
        print("营养库为空，无法体检。", file=sys.stderr)
        return 1

    cart: list[tuple[Food, int]] = []
    missing: list[str] = []
    for index, name in enumerate(args.items):
        quantity = args.qty[index] if index < len(args.qty) else 1
        food = table.find(name)
        if food is None:
            missing.append(name)
            continue
        cart.append((food, quantity))

    if missing:
        print(f"营养库里找不到：{'、'.join(missing)}", file=sys.stderr)
        for name in missing:
            near = table.search(name, limit=5)
            if near:
                print(f"  「{name}」是不是想按：{'、'.join(f.name for f in near)}", file=sys.stderr)

    if not cart:
        print("没有可体检的餐品。", file=sys.stderr)
        return 1

    ledger = Ledger.from_payload(data["orders_raw"])
    hour = resolve_now_hour(data["now_info"])
    has_coupon = coupon_count(data["coupons"]) > 0

    result = build_verdict(
        cart=cart,
        table=table,
        ledger=ledger,
        kcal_budget=args.budget,
        hour=hour,
        coupon_available=has_coupon,
    )

    store_code, store_name = ledger.default_store()
    store_label = store_display(store_code, store_name, args.share)

    if args.json:
        payload = result.as_dict()
        payload["store"] = {"code": store_code, "name": store_name} if not args.share else None
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    labels = [f"{food.name} x{qty}" if qty > 1 else food.name for food, qty in cart]
    render_verdict(result, labels, store_label, colored)

    if args.html:
        out = Path(args.html)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            render_html(result, labels, store_label, args.budget),
            encoding="utf-8",
        )
        print(f"已生成分享卡片：{out.resolve()}")
        if args.open:
            import webbrowser

            webbrowser.open(out.resolve().as_uri())
        print()

    if args.calculate_price and store_code:
        try:
            price = client.call(
                "calculate-price",
                {
                    "storeCode": store_code,
                    "orderType": 1,
                    "beType": 1,
                    "items": [
                        {"productCode": food.name, "quantity": qty} for food, qty in cart
                    ],
                },
            )
            print(json.dumps(price, ensure_ascii=False, indent=2)[:800])
        except McpError as exc:
            print(f"[WARN] 算价失败：{exc}", file=sys.stderr)

    return 0


def cmd_ledger(args: argparse.Namespace) -> int:
    client = McdMcpClient()
    raw = client.call("order-list")
    ledger = Ledger.from_payload(raw)
    if args.json:
        print(json.dumps(ledger.summary(args.days), ensure_ascii=False, indent=2))
        return 0
    render_ledger(ledger, use_color())
    return 0


def cmd_menu(args: argparse.Namespace) -> int:
    client = McdMcpClient()
    payload = client.call(
        "query-meals",
        {"storeCode": args.store, "orderType": args.order_type, "beType": args.be_type},
    )
    categories = payload.get("categories", []) if isinstance(payload, dict) else []
    print(json.dumps(categories, ensure_ascii=False, indent=2) if args.json else "")
    if not args.json:
        for category in categories:
            print(f"\n[{category.get('name', '未分类')}]")
            for meal in category.get("meals", []):
                tags = "、".join(meal.get("tags") or [])
                print(f"  {meal.get('code'):<12} {tags}")
    return 0


def cmd_lookup(args: argparse.Namespace) -> int:
    client = McdMcpClient()
    payload = client.call("list-nutrition-foods")
    table = parse_nutrition_table(extract_table_text(payload))
    colored = use_color()

    for keyword in args.keyword:
        print()
        print(paint(f"== {keyword} ==", BOLD, colored))
        exact = table.find(keyword)
        if exact is not None:
            print(
                f"  {exact.name:<22} {exact.energy_kcal:>5.0f} kcal · "
                f"蛋白 {exact.protein:>4.0f} g · 脂肪 {exact.fat:>4.0f} g · "
                f"钠 {exact.sodium:>5.0f} mg"
            )
        for food in table.search(keyword) if args.fuzzy else []:
            if exact is not None and food.name == exact.name:
                continue
            print(
                f"  {food.name:<22} {food.energy_kcal:>5.0f} kcal · "
                f"蛋白 {food.protein:>4.0f} g · 脂肪 {food.fat:>4.0f} g · "
                f"钠 {food.sodium:>5.0f} mg"
            )
    print()
    return 0


# --------------------------------------------------------------------- 入口


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcd-chill",
        description="麦麦冷静期 —— 下单前的 30 秒体检",
    )
    sub = parser.add_subparsers(dest="command")

    p_verdict = sub.add_parser("verdict", help="对一单做冷静期体检（默认命令）")
    p_verdict.add_argument("items", nargs="+", help="餐品名，空格分隔")
    p_verdict.add_argument("--qty", type=int, action="append", default=[], help="对应数量，可重复")
    p_verdict.add_argument("--budget", type=float, default=DEFAULT_KCAL_BUDGET, help="每日热量预算 kcal")
    p_verdict.add_argument("--json", action="store_true", help="输出 JSON")
    p_verdict.add_argument("--calculate-price", action="store_true", help="额外调用 MCP 算真实价格")
    p_verdict.add_argument("--share", action="store_true", help="隐藏门店名与编号，截图分享时用")
    p_verdict.add_argument("--html", metavar="PATH", help="额外生成一张可分享的 HTML 卡片")
    p_verdict.add_argument("--open", action="store_true", help="生成 HTML 后用浏览器打开")
    p_verdict.set_defaults(func=cmd_verdict)

    p_ledger = sub.add_parser("ledger", help="查看消费与复购账本")
    p_ledger.add_argument("--days", type=int, default=None, help="只看最近 N 天")
    p_ledger.add_argument("--json", action="store_true")
    p_ledger.set_defaults(func=cmd_ledger)

    p_menu = sub.add_parser("menu", help="查询门店在售餐品")
    p_menu.add_argument("--store", required=True, help="门店编码 storeCode")
    p_menu.add_argument("--order-type", type=int, default=1, dest="order_type", help="1 到店 / 2 外送")
    p_menu.add_argument("--be-type", type=int, default=1, dest="be_type", help="1 到店 / 5 得来速 / 6 团餐")
    p_menu.add_argument("--json", action="store_true")
    p_menu.set_defaults(func=cmd_menu)

    p_lookup = sub.add_parser("lookup", help="查营养库")
    p_lookup.add_argument("keyword", nargs="+")
    p_lookup.add_argument("--fuzzy", action="store_true", help="同时列出模糊匹配结果")
    p_lookup.set_defaults(func=cmd_lookup)

    return parser


def main(argv: list[str] | None = None) -> int:
    enable_ansi()
    argv = list(sys.argv[1:] if argv is None else argv)
    known = {"verdict", "ledger", "menu", "lookup", "-h", "--help"}
    if argv and argv[0] not in known and not argv[0].startswith("-"):
        argv = ["verdict", *argv]
    elif not argv:
        build_parser().print_help()
        return 0

    args = build_parser().parse_args(argv)
    if not getattr(args, "func", None):
        build_parser().print_help()
        return 0

    try:
        return args.func(args)
    except McpError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
