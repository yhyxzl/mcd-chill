"""联调探针：把只读工具的真实响应落盘，用于校准字段解析逻辑。

用法：
    set MCD_MCP_TOKEN=xxx
    python scripts/probe.py            # 全部只读探针
    python scripts/probe.py order-list # 只跑指定工具
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mcd_mcp import McdMcpClient, McpError  # noqa: E402

OUT_DIR = ROOT / "probe_out"

PROBES: list[tuple[str, dict]] = [
    ("now-time-info", {}),
    ("query-my-account", {}),
    ("order-list", {}),
    ("query-my-coupons", {"page": 1, "pageSize": 20}),
    ("available-coupons", {}),
    ("list-nutrition-foods", {}),
    ("campaign-calendar", {}),
    ("query-nearby-stores", {"beType": 1, "searchType": 1, "city": "汕尾"}),
]


def main() -> int:
    wanted = {arg for arg in sys.argv[1:] if not arg.startswith("-")}
    OUT_DIR.mkdir(exist_ok=True)
    client = McdMcpClient()

    info = client.initialize()
    print(f"MCP 握手成功: {json.dumps(info.get('serverInfo', {}), ensure_ascii=False)}")
    print("-" * 60)

    failures = 0
    for name, args in PROBES:
        if wanted and name not in wanted:
            continue
        try:
            result = client.call_tool(name, args)
        except McpError as exc:
            failures += 1
            print(f"[FAIL] {name}: {exc}")
            continue

        path = OUT_DIR / f"{name}.json"
        path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        text = McdMcpClient._text_of(result)
        preview = text.replace("\n", " ")[:220]
        print(f"[ OK ] {name} -> {path.name} ({len(text)} chars)")
        print(f"       {preview}")

    print("-" * 60)
    print(f"完成，失败 {failures} 项。原始响应见 {OUT_DIR}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(2)
