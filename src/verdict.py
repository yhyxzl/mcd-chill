"""冷静期判决引擎。

把「这一单到底该不该下」拆成 5 项独立体检，各自扣分后汇总成一个判决。
刻意不使用任何 LLM：规则完全确定、可复现、可解释，跑一次只要几百毫秒。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ledger import Ledger
from nutrition import Food, NutritionTable

DEFAULT_KCAL_BUDGET = 2000.0
MEAL_KCAL_SHARE = 0.40
SODIUM_DAILY_LIMIT_MG = 2000.0
MEAL_SODIUM_SHARE = 0.45
FAT_MEAL_LIMIT_G = 35.0

LEVELS = {
    "green": ("可以吃", "这单没什么毛病，去吧"),
    "yellow": ("换个搭配更稳", "有 1-2 项超标，换一下更舒服"),
    "red": ("再想想", "这单的账不太好看，建议改"),
}


@dataclass
class Check:
    key: str
    label: str
    penalty: int
    detail: str
    hint: str = ""


@dataclass
class Verdict:
    score: int
    level: str
    headline: str
    advice: str
    checks: list[Check] = field(default_factory=list)
    totals: dict = field(default_factory=dict)
    replacements: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "score": self.score,
            "level": self.level,
            "headline": self.headline,
            "advice": self.advice,
            "totals": self.totals,
            "checks": [c.__dict__ for c in self.checks],
            "replacements": self.replacements,
        }


def _band(value: float, thresholds: list[tuple[float, int]], over_penalty: int) -> int:
    for limit, penalty in thresholds:
        if value <= limit:
            return penalty
    return over_penalty


def build_verdict(
    cart: list[tuple[Food, int]],
    table: NutritionTable,
    ledger: Ledger | None = None,
    kcal_budget: float = DEFAULT_KCAL_BUDGET,
    hour: int | None = None,
    coupon_available: bool = False,
) -> Verdict:
    """对购物车做 5 项体检并给出判决。"""
    checks: list[Check] = []

    if not cart:
        return Verdict(
            score=0,
            level="green",
            headline="购物车是空的",
            advice="先告诉我要吃什么，我再来做体检",
        )

    totals = {
        "kcal": round(sum(f.energy_kcal * q for f, q in cart), 1),
        "protein": round(sum(f.protein * q for f, q in cart), 1),
        "fat": round(sum(f.fat * q for f, q in cart), 1),
        "carb": round(sum(f.carbohydrate * q for f, q in cart), 1),
        "sodium": round(sum(f.sodium * q for f, q in cart), 1),
        "items": sum(q for _, q in cart),
    }

    # ---------------------------------------------------------- 1. 热量体检
    share = totals["kcal"] / kcal_budget if kcal_budget else 0.0
    kcal_penalty = _band(
        share,
        [(0.40, 0), (0.55, 1), (0.70, 2)],
        3,
    )
    checks.append(
        Check(
            key="kcal",
            label="热量体检",
            penalty=kcal_penalty,
            detail=(
                f"这单 {totals['kcal']:.0f} kcal，占 {kcal_budget:.0f} kcal 日预算的 "
                f"{share * 100:.0f}%（单餐合理线 {MEAL_KCAL_SHARE * 100:.0f}%）"
            ),
            hint="把中薯换小薯，或饮料换无糖可乐，能砍掉约 120 kcal" if kcal_penalty else "",
        )
    )

    # ---------------------------------------------------------- 2. 钠体检
    sodium_penalty = _band(
        totals["sodium"],
        [(600, 0), (900, 1), (1300, 2)],
        3,
    )
    checks.append(
        Check(
            key="sodium",
            label="钠体检",
            penalty=sodium_penalty,
            detail=(
                f"钠 {totals['sodium']:.0f} mg，占每日建议上限 "
                f"{SODIUM_DAILY_LIMIT_MG:.0f} mg 的 "
                f"{totals['sodium'] / SODIUM_DAILY_LIMIT_MG * 100:.0f}%"
            ),
            hint="酱料少放、把腌制品换成原味肉饼能明显降钠" if sodium_penalty else "",
        )
    )

    # ---------------------------------------------------------- 3. 脂肪体检
    fat_penalty = _band(
        totals["fat"],
        [(25, 0), (FAT_MEAL_LIMIT_G, 1)],
        3,
    )
    checks.append(
        Check(
            key="fat",
            label="脂肪体检",
            penalty=fat_penalty,
            detail=f"脂肪 {totals['fat']:.0f} g（单餐参考线 {FAT_MEAL_LIMIT_G:.0f} g）",
            hint="油炸类换成烤制/板烧类，脂肪能降三成" if fat_penalty else "",
        )
    )

    # ---------------------------------------------------------- 4. 重复体检
    repeat_penalty = 0
    repeat_detail = "没有历史订单，暂时无法判断你的复购习惯"
    repeat_hint = ""
    if ledger is not None and ledger.valid():
        counter = ledger.repeat_counts(7)
        worst_name, worst_count = "", 0
        for food, quantity in cart:
            count = counter.get(food.name, 0)
            if count > worst_count:
                worst_name, worst_count = food.name, count
        if worst_count:
            repeat_penalty = _band(worst_count, [(1, 0), (2, 1), (3, 2)], 3)
            repeat_detail = f"近 7 天你已经点了 {worst_count} 次「{worst_name}」"
            if repeat_penalty:
                repeat_hint = "换一个同价位没吃过的，味觉也需要新鲜感"
        else:
            repeat_detail = "近 7 天没有重复点过购物车里的单品"
    checks.append(
        Check(
            key="repeat",
            label="重复体检",
            penalty=repeat_penalty,
            detail=repeat_detail,
            hint=repeat_hint,
        )
    )

    # ---------------------------------------------------------- 5. 时段/券体检
    context_penalty = 0
    context_notes: list[str] = []
    if hour is not None:
        if hour >= 23 or hour < 5:
            context_penalty += 2
            context_notes.append(f"现在是 {hour:02d} 点，深夜进食")
        elif 14 <= hour < 17:
            context_notes.append(f"现在是 {hour:02d} 点，下午茶时段")
    if not coupon_available:
        context_penalty += 1
        context_notes.append("这单没有可用券")
    else:
        context_notes.append("有可用券")
    checks.append(
        Check(
            key="context",
            label="情境体检",
            penalty=min(context_penalty, 3),
            detail="；".join(context_notes) or "无额外情境风险",
            hint="下单前去麦麦省一键领券，常能再省几块" if not coupon_available else "",
        )
    )

    # ---------------------------------------------------------- 汇总判决
    score = sum(c.penalty for c in checks)
    if score <= 3:
        level = "green"
    elif score <= 7:
        level = "yellow"
    else:
        level = "red"
    headline, advice = LEVELS[level]

    replacements: list[dict] = []
    if level != "green":
        heaviest = max(cart, key=lambda pair: pair[0].energy_kcal * pair[1])[0]
        for candidate in table.suggest_replacements(heaviest):
            saved = heaviest.energy_kcal - candidate.energy_kcal
            if saved <= 20:
                continue
            replacements.append(
                {
                    "from": heaviest.name,
                    "to": candidate.name,
                    "saved_kcal": round(saved),
                    "protein": round(candidate.protein),
                }
            )
        replacements = replacements[:3]

    return Verdict(
        score=score,
        level=level,
        headline=headline,
        advice=advice,
        checks=checks,
        totals=totals,
        replacements=replacements,
    )
