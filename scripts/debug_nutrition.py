import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nutrition import extract_table_text, group_of, parse_nutrition_table  # noqa: E402

raw = json.loads((ROOT / "probe_out" / "list-nutrition-foods.json").read_text(encoding="utf-8"))
text = extract_table_text(raw)
print("structuredContent type:", type(raw.get("structuredContent")))
print("提取文本长度:", len(text))
print("前 160 字符 repr:", repr(text[:160]))
print()

table = parse_nutrition_table(text)
print("解析条目数:", len(table))
print("品类分布:", Counter(group_of(f.name) for f in table))
print()

target = table.find("巨无霸")
print("目标:", target)
if target:
    pool = table.suggest_replacements(target)
    print("替换建议:")
    for f in pool:
        print(f"  {f.name} | {f.energy_kcal:.0f} kcal | P{f.protein:.0f} | {group_of(f.name)}")
