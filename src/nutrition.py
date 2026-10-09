"""解析麦当劳 MCP 返回的餐品营养表。

list-nutrition-foods 返回的不是 JSON 数组，而是一段紧凑的表格文本：

    [160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
      猪柳麦满分,null,1288,308,16,16,24,781,213
      中薯条,null,1210,289,4,12,38,165,18
      ...

本模块把它还原成结构化记录，并提供名称模糊匹配。
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Any

HEADER_RE = re.compile(r"^\[(\d+)\]\{([^}]+)\}\s*:?\s*$", re.MULTILINE)
TABLE_MARK_RE = re.compile(r"\[\d+\]\{[^}]+\}:")

ALIASES: dict[str, str] = {
    "巨无霸": "巨无霸",
    "麦辣鸡腿堡": "麦辣鸡腿汉堡",
    "麦辣鸡腿汉堡": "麦辣鸡腿汉堡",
    "板烧": "板烧鸡腿堡",
    "板烧鸡腿堡": "板烧鸡腿堡",
    "麦香鸡": "麦香鸡",
    "麦香鱼": "麦香鱼",
    "吉士汉堡": "吉士汉堡包",
    "吉士汉堡包": "吉士汉堡包",
    "双吉": "双层吉士汉堡",
    "双层吉士汉堡": "双层吉士汉堡",
    "中薯": "中薯条",
    "中薯条": "中薯条",
    "大薯": "大薯条",
    "大薯条": "大薯条",
    "小薯": "小薯条",
    "小薯条": "小薯条",
    "可乐": "可乐中杯",
    "可乐中杯": "可乐中杯",
    "中可乐": "可乐中杯",
    "麦乐鸡": "麦乐鸡5块",
    "麦乐鸡5块": "麦乐鸡5块",
    "麦旋风": "奥利奥麦旋风",
    "奥利奥麦旋风": "奥利奥麦旋风",
    "圆筒": "圆筒冰淇淋",
    "圆筒冰淇淋": "圆筒冰淇淋",
    "香芋派": "香芋派",
    "菠萝派": "菠萝派",
}


GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("burger", ("麦满分", "汉堡", "堡", "麦香鸡", "麦香鱼", "巨无霸", "营养卷", "卷")),
    ("fried", ("麦乐鸡", "鸡翅", "翅", "鸡球", "脆汁鸡", "鸡排", "鸡块", "鸡柳")),
    ("side", ("薯条", "薯饼", "玉米", "油条", "苹果片", "香肠", "沙拉")),
    ("dessert", ("新地", "麦旋风", "麦旋酷", "冰淇淋", "圆筒", "圣代", "派", "拉明顿", "奶冻", "爆珠")),
    ("coffee", ("咖", "美式", "奶", "玛奇朵", "卡布奇诺", "抹茶", "阿芙佳朵", "雪冰", "浓浓")),
    ("drink", ("可乐", "雪碧", "果汁", "茶", "美汁源", "豆浆", "朱古力", "水", "怡泉", "纯悦")),
    ("porridge", ("粥",)),
]

GROUP_LABELS = {
    "burger": "汉堡 / 卷",
    "fried": "炸物",
    "side": "配餐",
    "dessert": "甜品",
    "coffee": "咖啡 / 奶饮",
    "drink": "饮料",
    "porridge": "早餐咸口",
    "other": "其他",
}


def group_of(name: str) -> str:
    """按名称粗分品类，用于把替换建议限制在同一品类内。"""
    for group, keywords in GROUPS:
        for keyword in keywords:
            if keyword in name:
                return group
    return "other"


@dataclass
class Food:
    """单个餐品的营养记录。"""

    name: str
    energy_kj: float = 0.0
    energy_kcal: float = 0.0
    protein: float = 0.0
    fat: float = 0.0
    carbohydrate: float = 0.0
    sodium: float = 0.0
    calcium: float = 0.0

    def scaled(self, quantity: float) -> "Food":
        return Food(
            name=self.name,
            energy_kj=self.energy_kj * quantity,
            energy_kcal=self.energy_kcal * quantity,
            protein=self.protein * quantity,
            fat=self.fat * quantity,
            carbohydrate=self.carbohydrate * quantity,
            sodium=self.sodium * quantity,
            calcium=self.calcium * quantity,
        )


@dataclass
class NutritionTable:
    foods: list[Food] = field(default_factory=list)
    _index: dict[str, Food] = field(default_factory=dict, repr=False)

    def __len__(self) -> int:
        return len(self.foods)

    def __iter__(self):
        return iter(self.foods)

    def build_index(self) -> None:
        self._index = {food.name: food for food in self.foods}

    def find(self, name: str) -> Food | None:
        """按名称找一个餐品，支持别名与包含匹配。"""
        key = (name or "").strip()
        if not key:
            return None
        key = ALIASES.get(key, key)

        if key in self._index:
            return self._index[key]

        normalized = re.sub(r"[\s\-_（）()]+", "", key)
        for food in self.foods:
            if re.sub(r"[\s\-_（）()]+", "", food.name) == normalized:
                return food

        for food in self.foods:
            if normalized and normalized in food.name:
                return food
        for food in self.foods:
            if normalized and food.name in normalized:
                return food
        return None

    def search(self, keyword: str, limit: int = 8) -> list[Food]:
        key = re.sub(r"[\s\-_（）()]+", "", keyword or "")
        if not key:
            return []
        return [f for f in self.foods if key in re.sub(r"[\s\-_（）()]+", "", f.name)][:limit]

    def suggest_replacements(self, target: Food, limit: int = 3) -> list[Food]:
        """在同一品类内找热量更低、蛋白质更高的替代品。

        只做同品类替换 —— 把汉堡换成炸鸡翅这种事没有参考价值。
        品类由 group_of() 按名称关键词判定，判定不出来的（other）不瞎给建议。
        """
        target_group = group_of(target.name)
        if target_group == "other":
            return []

        candidates = [
            food
            for food in self.foods
            if food.name != target.name
            and group_of(food.name) == target_group
            and food.energy_kcal > 0
            and food.energy_kcal < target.energy_kcal * 0.85
            and food.protein >= target.protein * 0.6
        ]
        candidates.sort(key=lambda food: (-food.protein, food.energy_kcal))
        return candidates[:limit]


def _to_float(value: str) -> float:
    text = (value or "").strip()
    if not text or text.lower() in {"null", "none", "-"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def extract_table_text(payload: Any) -> str:
    """从 MCP 的各种包装里取出营养表文本。

    list-nutrition-foods 的本体是一段带表头标记的紧凑文本：

        [160]{productName,nutritionDescription,energyKj,...}:
          猪柳麦满分,null,1288,308,16,16,24,781,213

    它可能被包在 structuredContent.data 里，也可能躺在 content[0].text 中，
    而且 content[0].text 常常是同一段数据的「字段说明 + 原文」拼接，
    直接取第一个字符串会拿到说明文字。所以这里按表头特征做广度搜索，
    找到真正含表头的那个字符串。

    另外注意：表格本体不能先 json.dumps 再解析 —— 那样换行会被转义成
    字面量 \\n，按行切分就失效了。
    """
    queue: list[Any] = [payload]
    visited = 0
    fallback = ""
    while queue and visited < 300:
        visited += 1
        node = queue.pop(0)
        if isinstance(node, str):
            if TABLE_MARK_RE.search(node):
                return node
            if not fallback and node.strip():
                fallback = node
        elif isinstance(node, dict):
            queue.extend(node.values())
        elif isinstance(node, list):
            queue.extend(node)

    return fallback


def parse_nutrition_table(payload: str) -> NutritionTable:
    """把 list-nutrition-foods 的文本体解析成 NutritionTable。

    payload 既可以是完整的响应文本，也可以是 data 字段本身。
    """
    if not isinstance(payload, str) or not payload.strip():
        return NutritionTable()

    table = NutritionTable()
    columns: list[str] = []
    remaining = 0

    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        header = HEADER_RE.match(line)
        if header:
            remaining = int(header.group(1))
            columns = [c.strip() for c in header.group(2).split(",")]
            continue

        if not columns:
            continue
        if remaining <= 0:
            continue

        row = next(csv.reader(io.StringIO(line)), [])
        if len(row) < 2:
            row = line.split(",")
        if not row:
            continue

        record = dict(zip(columns, row))
        name = (record.get("productName") or "").strip()
        if not name:
            continue

        table.foods.append(
            Food(
                name=name,
                energy_kj=_to_float(record.get("energyKj", "")),
                energy_kcal=_to_float(record.get("energyKcal", "")),
                protein=_to_float(record.get("protein", "")),
                fat=_to_float(record.get("fat", "")),
                carbohydrate=_to_float(record.get("carbohydrate", "")),
                sodium=_to_float(record.get("sodium", "")),
                calcium=_to_float(record.get("calcium", "")),
            )
        )
        remaining -= 1

    table.build_index()
    return table
