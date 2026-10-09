"""把判决结果渲染成一张可截图分享的 HTML 卡片。

设计目标只有一个：让别人在信息流里扫一眼就明白这个工具在干嘛。
所以用麦当劳品牌色（红 #DA291C / 黄 #FFC72C）+ 大字号风险分，
不使用任何外部资源（无 CDN、无外链字体），单文件可直接打开。
"""

from __future__ import annotations

from datetime import datetime
from html import escape

PALETTE = {
    "green": {"bg": "#E1F5EE", "fg": "#0F6E56", "bar": "#1D9E75", "label": "可以吃"},
    "yellow": {"bg": "#FAEEDA", "fg": "#854F0B", "bar": "#EF9F27", "label": "换个搭配更稳"},
    "red": {"bg": "#FCEBEB", "fg": "#A32D2D", "bar": "#E24B4A", "label": "再想想"},
}

MC_RED = "#DA291C"
MC_YELLOW = "#FFC72C"


def _metrics(totals: dict) -> list[tuple[str, str]]:
    return [
        ("热量", f"{totals.get('kcal', 0):.0f} kcal"),
        ("蛋白质", f"{totals.get('protein', 0):.0f} g"),
        ("脂肪", f"{totals.get('fat', 0):.0f} g"),
        ("碳水", f"{totals.get('carb', 0):.0f} g"),
        ("钠", f"{totals.get('sodium', 0):.0f} mg"),
    ]


def _check_rows(result) -> str:
    rows: list[str] = []
    for index, check in enumerate(result.checks, start=1):
        if check.penalty == 0:
            badge_bg, badge_fg, badge = "#E1F5EE", "#0F6E56", "达标"
        elif check.penalty >= 3:
            badge_bg, badge_fg, badge = "#FCEBEB", "#A32D2D", f"+{check.penalty}"
        else:
            badge_bg, badge_fg, badge = "#FAEEDA", "#854F0B", f"+{check.penalty}"

        hint = (
            f'<div class="hint">{escape(check.hint)}</div>' if check.hint else ""
        )
        rows.append(
            f"""
        <div class="check">
          <div class="check-no">{index}</div>
          <div class="check-body">
            <div class="check-head">
              <span class="check-label">{escape(check.label)}</span>
              <span class="badge" style="background:{badge_bg};color:{badge_fg}">{badge}</span>
            </div>
            <div class="check-detail">{escape(check.detail)}</div>
            {hint}
          </div>
        </div>"""
        )
    return "".join(rows)


def _replacement_rows(result) -> str:
    if not result.replacements:
        return ""
    items = "".join(
        f"""
        <div class="swap">
          <span class="swap-from">{escape(item["from"])}</span>
          <span class="swap-arrow">→</span>
          <span class="swap-to">{escape(item["to"])}</span>
          <span class="swap-save">省 {item["saved_kcal"]} kcal</span>
        </div>"""
        for item in result.replacements
    )
    return f'<div class="section-title">替换建议</div>{items}'


def render_html(
    result,
    cart_labels: list[str],
    store_label: str = "",
    budget: float = 2000.0,
    generated_at: str | None = None,
) -> str:
    """生成完整 HTML 文档（含 <html> 外壳，可直接存成 .html 打开）。"""
    style = PALETTE.get(result.level, PALETTE["yellow"])
    stamp = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    totals = result.totals or {}
    share = min(100.0, totals.get("kcal", 0) / budget * 100) if budget else 0.0

    metrics = "".join(
        f'<div class="metric"><div class="metric-k">{escape(key)}</div>'
        f'<div class="metric-v">{escape(value)}</div></div>'
        for key, value in _metrics(totals)
    )
    store_line = (
        f'<div class="store">{escape(store_label)}</div>' if store_label else ""
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>麦麦冷静期 · {escape(style["label"])}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #F1EFE8;
    font-family: "PingFang SC", "Microsoft YaHei", "Helvetica Neue", system-ui, sans-serif;
    color: #2C2C2A;
    padding: 32px 20px;
    display: flex;
    justify-content: center;
  }}
  .card {{
    width: 720px;
    background: #FFFFFF;
    border-radius: 20px;
    overflow: hidden;
    border: 1px solid #E2E0D8;
  }}
  .band {{ height: 8px; background: {MC_RED}; }}
  .head {{ padding: 26px 32px 20px; }}
  .brand {{ font-size: 15px; font-weight: 500; color: {MC_RED}; letter-spacing: .04em; }}
  .title {{ font-size: 27px; font-weight: 500; margin-top: 6px; }}
  .sub {{ font-size: 14px; color: #888780; margin-top: 6px; }}
  .store {{ font-size: 13px; color: #888780; margin-top: 8px; }}

  .verdict {{
    margin: 0 32px 24px;
    background: {style["bg"]};
    border-radius: 14px;
    padding: 20px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
  }}
  .verdict-main {{ font-size: 24px; font-weight: 500; color: {style["fg"]}; }}
  .verdict-advice {{ font-size: 14px; color: {style["fg"]}; opacity: .85; margin-top: 6px; }}
  .score {{ text-align: right; flex-shrink: 0; }}
  .score-v {{ font-size: 34px; font-weight: 500; color: {style["fg"]}; line-height: 1; }}
  .score-k {{ font-size: 12px; color: {style["fg"]}; opacity: .75; margin-top: 4px; }}
  .score-bar {{ width: 110px; height: 6px; background: rgba(0,0,0,.08); border-radius: 3px; margin-top: 8px; }}
  .score-fill {{ height: 6px; background: {style["bar"]}; border-radius: 3px; }}

  .section-title {{
    font-size: 13px; font-weight: 500; color: #888780;
    padding: 0 32px; margin-bottom: 10px; letter-spacing: .06em;
  }}
  .cart {{ padding: 0 32px 18px; }}
  .cart-items {{ font-size: 18px; font-weight: 500; }}
  .metrics {{
    display: grid; grid-template-columns: repeat(5, 1fr);
    gap: 8px; margin-top: 14px;
  }}
  .metric {{ background: #FAFAF9; border-radius: 10px; padding: 10px 12px; }}
  .metric-k {{ font-size: 12px; color: #888780; }}
  .metric-v {{ font-size: 16px; font-weight: 500; margin-top: 3px; }}
  .kcal-note {{ font-size: 13px; color: #888780; margin-top: 12px; }}

  .checks {{ padding: 0 32px 8px; }}
  .check {{ display: flex; gap: 14px; padding: 13px 0; border-top: 1px solid #F1EFE8; }}
  .check-no {{
    width: 22px; height: 22px; border-radius: 50%; flex-shrink: 0;
    background: #F1EFE8; color: #5F5E5A;
    font-size: 12px; display: flex; align-items: center; justify-content: center;
    margin-top: 2px;
  }}
  .check-body {{ flex: 1; min-width: 0; }}
  .check-head {{ display: flex; align-items: center; gap: 10px; }}
  .check-label {{ font-size: 15px; font-weight: 500; }}
  .badge {{ font-size: 12px; padding: 2px 9px; border-radius: 999px; }}
  .check-detail {{ font-size: 13px; color: #5F5E5A; margin-top: 5px; line-height: 1.6; }}
  .hint {{ font-size: 13px; color: #888780; margin-top: 4px; }}

  .swaps {{ padding: 20px 32px 4px; }}
  .swap {{
    display: flex; align-items: center; gap: 10px;
    padding: 11px 0; border-top: 1px solid #F1EFE8; font-size: 14px;
  }}
  .swap-from {{ color: #888780; }}
  .swap-arrow {{ color: {MC_RED}; }}
  .swap-to {{ font-weight: 500; flex: 1; }}
  .swap-save {{ font-size: 13px; color: #0F6E56; background: #E1F5EE; padding: 2px 10px; border-radius: 999px; }}

  .foot {{
    margin-top: 22px; padding: 18px 32px 24px;
    border-top: 1px solid #F1EFE8;
    font-size: 12px; color: #888780; line-height: 1.7;
  }}
  .stamp {{ float: right; }}
</style>
</head>
<body>
  <div class="card">
    <div class="band"></div>
    <div class="head">
      <div class="brand">麦当劳 MCP · 麦麦冷静期</div>
      <div class="title">下单前 30 秒，先读一下你的账</div>
      <div class="sub">风险分越低越放心，由确定性规则引擎计算，非 AI 生成</div>
      {store_line}
    </div>

    <div class="verdict">
      <div>
        <div class="verdict-main">{escape(style["label"])}</div>
        <div class="verdict-advice">{escape(result.advice)}</div>
      </div>
      <div class="score">
        <div class="score-v">{result.score}<span style="font-size:16px">/15</span></div>
        <div class="score-k">风险分</div>
        <div class="score-bar"><div class="score-fill" style="width:{min(100, result.score / 15 * 100):.0f}%"></div></div>
      </div>
    </div>

    <div class="section-title">这一单</div>
    <div class="cart">
      <div class="cart-items">{escape("　".join(cart_labels))}</div>
      <div class="metrics">{metrics}</div>
      <div class="kcal-note">占 {budget:.0f} kcal 每日预算的 {share:.0f}%，单餐合理线 40%</div>
    </div>

    <div class="section-title">五项体检</div>
    <div class="checks">{_check_rows(result)}</div>

    {f'<div class="swaps">{_replacement_rows(result)}</div>' if result.replacements else ""}

    <div class="foot">
      <span class="stamp">{escape(stamp)}</span>
      数据来自麦当劳中国 MCP 的只读工具 · 输出仅供参考，不构成营养或医疗建议<br>
      参赛作品，非麦当劳官方产品 · github.com/yhyxzl/mcd-chill
    </div>
  </div>
</body>
</html>
"""
