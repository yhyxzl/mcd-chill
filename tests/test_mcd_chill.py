"""麦麦冷静期的单元测试。

全部是纯函数测试，不联网、不需要 MCP Token：

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ledger import Ledger  # noqa: E402
from mcd_mcp import McdMcpClient, McpError, as_list, unwrap  # noqa: E402
from nutrition import (  # noqa: E402
    extract_table_text,
    group_of,
    parse_nutrition_table,
)
from report import render_html  # noqa: E402
from verdict import build_verdict  # noqa: E402

# list-nutrition-foods 的真实返回形态：不是 JSON，是一段带表头的紧凑文本
NUTRITION_TEXT = """[6]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
  巨无霸,null,2146,513,27,26,42,961,171
  中薯条,null,1210,289,4,12,38,165,18
  小薯条,null,877,210,3,9,28,120,13
  可乐中杯,null,616,147,0,0,36,0,10
  麦香鱼,null,1359,325,16,13,35,556,99
  双层吉士汉堡,null,1796,429,27,22,31,1017,232
"""

ORDER_PAYLOAD = {
    "success": True,
    "code": 200,
    "data": {
        "list": [
            {
                "orderId": "A1",
                "createTime": "2026-10-08 12:30:00",
                "storeCode": "STORE-A",
                "storeName": "麦当劳测试门店",
                "orderStatus": "已完成",
                "realTotalAmount": "42.5",
                "orderProductList": [
                    {"productName": "巨无霸", "quantity": 1},
                    {"productName": "中薯条", "quantity": 2},
                ],
            },
            {
                "orderId": "A2",
                "createTime": "2026-10-09 19:00:00",
                "storeCode": "STORE-A",
                "storeName": "麦当劳测试门店",
                "orderStatus": "订单已取消",
                "realTotalAmount": "99.0",
                "orderProductList": [{"productName": "巨无霸", "quantity": 1}],
            },
            {
                "orderId": "A3",
                "createTime": "2026-10-09 20:00:00",
                "storeCode": "STORE-A",
                "storeName": "麦当劳测试门店",
                "orderStatus": "已完成",
                "realTotalAmount": "36.0",
                "orderProductList": [{"productName": "巨无霸", "quantity": 2}],
            },
        ]
    },
}

NOW = datetime(2026, 10, 9, 20, 30)


class TestNutritionParsing(unittest.TestCase):
    def test_parses_every_row(self):
        table = parse_nutrition_table(NUTRITION_TEXT)
        self.assertEqual(len(table), 6)
        self.assertEqual(table.foods[0].name, "巨无霸")

    def test_numeric_fields(self):
        table = parse_nutrition_table(NUTRITION_TEXT)
        bigmac = table.find("巨无霸")
        self.assertIsNotNone(bigmac)
        self.assertEqual(bigmac.energy_kcal, 513)
        self.assertEqual(bigmac.protein, 27)
        self.assertEqual(bigmac.sodium, 961)

    def test_null_becomes_zero(self):
        table = parse_nutrition_table(NUTRITION_TEXT)
        # nutritionDescription 是 null，不应污染任何数值字段
        self.assertEqual(table.find("中薯条").fat, 12)

    def test_aliases_and_fuzzy(self):
        table = parse_nutrition_table(NUTRITION_TEXT)
        self.assertEqual(table.find("中薯").name, "中薯条")
        self.assertEqual(table.find("双吉").name, "双层吉士汉堡")
        self.assertEqual(table.find("可乐").name, "可乐中杯")
        self.assertIsNone(table.find("不存在的餐品"))

    def test_empty_input(self):
        self.assertEqual(len(parse_nutrition_table("")), 0)
        self.assertEqual(len(parse_nutrition_table("随便一段文字")), 0)

    def test_extract_from_nested_mcp_result(self):
        """content[0].text 是字段说明长文本，必须优先取到 structuredContent.data。"""
        payload = {
            "content": [{"type": "text", "text": "# API Response Information\n说明文字"}],
            "isError": False,
            "structuredContent": {"success": True, "data": NUTRITION_TEXT},
        }
        text = extract_table_text(payload)
        self.assertIn("[6]{productName", text)
        self.assertEqual(len(parse_nutrition_table(text)), 6)

    def test_extract_falls_back_to_plain_string(self):
        self.assertEqual(extract_table_text(NUTRITION_TEXT), NUTRITION_TEXT)


class TestGroups(unittest.TestCase):
    def test_key_items_are_grouped(self):
        cases = {
            "巨无霸": "burger",
            "麦辣鸡腿汉堡": "burger",
            "板烧鸡腿堡": "burger",
            "麦乐鸡5块": "fried",
            "薄皮焦香V翅": "fried",
            "中薯条": "side",
            "奥利奥麦旋风": "dessert",
            "可乐中杯": "drink",
            "大杯鲜萃咖啡": "coffee",
            "皮蛋鸡肉粥": "porridge",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(group_of(name), expected)

    def test_replacement_stays_in_same_group(self):
        """回归：曾经出现「巨无霸 → 炸鸡翅」这种跨品类建议。"""
        table = parse_nutrition_table(NUTRITION_TEXT)
        picks = table.suggest_replacements(table.find("巨无霸"))
        self.assertTrue(picks, "应该能在同品类里找到更轻的汉堡")
        for food in picks:
            self.assertEqual(group_of(food.name), "burger")
            self.assertLess(food.energy_kcal, 513)

    def test_unknown_group_yields_no_advice(self):
        table = parse_nutrition_table(
            "[1]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:\n"
            "  神秘新品,null,2000,500,10,10,10,10,10"
        )
        self.assertEqual(table.suggest_replacements(table.find("神秘新品")), [])


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger.from_payload(ORDER_PAYLOAD, now=NOW)

    def test_cancelled_orders_are_excluded(self):
        self.assertEqual(len(self.ledger.orders), 3)
        self.assertEqual(len(self.ledger.valid()), 2)

    def test_spend_and_average(self):
        self.assertAlmostEqual(self.ledger.total_spend(), 78.5)
        self.assertEqual(self.ledger.total_orders(), 2)

    def test_repeat_counter_expands_quantity(self):
        counter = self.ledger.repeat_counts(7)
        # A1 里 1 个巨无霸 + A3 里 2 个巨无霸；A1 里 2 份中薯条
        self.assertEqual(counter["巨无霸"], 3)
        self.assertEqual(counter["中薯条"], 2)

    def test_default_store_falls_back_to_any_order(self):
        empty = Ledger.from_payload({"data": {"list": []}}, now=NOW)
        self.assertEqual(empty.default_store(), ("", ""))

        only_cancelled = Ledger.from_payload(
            {"data": {"list": [ORDER_PAYLOAD["data"]["list"][1]]}}, now=NOW
        )
        # 有效订单为空时，仍应从已取消订单里挖出门店 code 兜底
        self.assertEqual(only_cancelled.default_store()[0], "STORE-A")

    def test_summary_shape(self):
        summary = self.ledger.summary(None)
        self.assertEqual(summary["orders"], 2)
        self.assertAlmostEqual(summary["avg_order"], 39.25)
        self.assertEqual(summary["top_items"][0]["name"], "巨无霸")


class TestVerdict(unittest.TestCase):
    def setUp(self):
        self.table = parse_nutrition_table(NUTRITION_TEXT)
        self.ledger = Ledger.from_payload(ORDER_PAYLOAD, now=NOW)

    def cart(self, *names):
        return [(self.table.find(name), 1) for name in names]

    def test_empty_cart(self):
        result = build_verdict([], self.table, self.ledger)
        self.assertEqual(result.score, 0)
        self.assertEqual(result.headline, "购物车是空的")

    def test_totals_are_summed(self):
        result = build_verdict(self.cart("巨无霸", "中薯条", "可乐中杯"), self.table, self.ledger)
        self.assertEqual(result.totals["kcal"], 949)
        self.assertEqual(result.totals["sodium"], 1126)

    def test_light_meal_scores_green(self):
        result = build_verdict(
            self.cart("麦香鱼"),
            self.table,
            Ledger.from_payload({}, now=NOW),
            hour=12,
            coupon_available=True,
        )
        self.assertEqual(result.level, "green")
        self.assertEqual(result.headline, "可以吃")

    def test_heavy_late_night_meal_scores_red(self):
        cart = [(self.table.find("巨无霸"), 1), (self.table.find("双层吉士汉堡"), 1)]
        result = build_verdict(cart, self.table, self.ledger, hour=1, coupon_available=False)
        self.assertEqual(result.level, "red")
        self.assertGreaterEqual(result.score, 8)

    def test_repeat_check_uses_ledger(self):
        result = build_verdict(self.cart("巨无霸"), self.table, self.ledger, hour=12)
        repeat = next(c for c in result.checks if c.key == "repeat")
        self.assertGreater(repeat.penalty, 0)
        self.assertIn("3 次", repeat.detail)

    def test_cold_start_without_history_is_honest(self):
        result = build_verdict(
            self.cart("巨无霸"), self.table, Ledger.from_payload({}, now=NOW), hour=12
        )
        repeat = next(c for c in result.checks if c.key == "repeat")
        self.assertEqual(repeat.penalty, 0)
        self.assertIn("没有历史订单", repeat.detail)

    def test_advice_only_when_not_green(self):
        light = build_verdict(
            self.cart("麦香鱼"),
            self.table,
            Ledger.from_payload({}, now=NOW),
            hour=12,
            coupon_available=True,
        )
        self.assertEqual(light.replacements, [])


class TestTransport(unittest.TestCase):
    def test_decode_plain_json(self):
        raw = '{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'
        message = McdMcpClient._decode(raw, "application/json")
        self.assertEqual(message["result"]["ok"], True)

    def test_decode_sse_takes_last_message(self):
        raw = (
            "event: message\n"
            'data: {"jsonrpc":"2.0","id":1,"result":{"step":1}}\n\n'
            "event: message\n"
            'data: {"jsonrpc":"2.0","id":1,"result":{"step":2}}\n\n'
        )
        message = McdMcpClient._decode(raw, "text/event-stream")
        self.assertEqual(message["result"]["step"], 2)

    def test_decode_requires_token(self):
        with self.assertRaises(McpError):
            McdMcpClient(token="")

    def test_unwrap_strips_common_wrappers(self):
        self.assertEqual(unwrap({"data": {"list": []}}), {"list": []})
        self.assertEqual(unwrap([1, 2]), [1, 2])

    def test_as_list_finds_nested_array(self):
        self.assertEqual(as_list({"data": {"list": [{"a": 1}]}}, "list"), [{"a": 1}])
        self.assertEqual(as_list({"nothing": 1}), [])


class TestReport(unittest.TestCase):
    def test_html_is_self_contained_and_masked(self):
        table = parse_nutrition_table(NUTRITION_TEXT)
        cart = [(table.find("巨无霸"), 1), (table.find("中薯条"), 1)]
        result = build_verdict(cart, table, Ledger.from_payload({}, now=NOW), hour=12)

        html = render_html(result, ["巨无霸", "中薯条"], "麦当劳（门店信息已脱敏）")

        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("巨无霸", html)
        self.assertIn("门店信息已脱敏", html)
        # 单文件卡片不允许依赖任何外部资源
        self.assertNotIn("<script", html)
        self.assertNotIn("http://", html.replace("http://www.w3.org", ""))
        self.assertNotIn("cdn", html.lower())

    def test_html_escapes_untrusted_text(self):
        table = parse_nutrition_table(NUTRITION_TEXT)
        cart = [(table.find("巨无霸"), 1)]
        result = build_verdict(cart, table, Ledger.from_payload({}, now=NOW), hour=12)
        result.checks[0].detail = "<script>alert(1)</script>"
        html = render_html(result, ["巨无霸"])
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
