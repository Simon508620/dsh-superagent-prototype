# -*- coding: utf-8 -*-
"""
作战计划构建模块（plan_builder.py）—— 阶段3 多维度评分后确认操作标的和作战计划
按用户已确认决策（悟空 11-09 20:04 拍板 ① 按推荐）：
  作战计划格式 = 组合+计划格式：
    { 操作标的清单[], 每标的分仓{买入区间/止损位/目标位/仓位占比},
      总仓位上限, 持有周期(超短1-3日), 止损规则(-5%硬止损) }
输入：scorer.score_pool 输出的综合评分 Top 候选 + 真实行情数据。
铁律：所有输入必须来自真实行情接口，严禁模拟数据。
接口：build_plan(scored, signals, params) -> dict 作战计划
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 默认作战参数（用户已确认 ③ 风控：单票25%/总仓80%；持有周期超短1-3日；止损-5%）
DEFAULT_PLAN_PARAMS = {
    "max_position": 0.25,        # 单票仓位上限（用户已确认）
    "max_total_position": 0.80,  # 总仓位上限（用户已确认）
    "holding_days": "1-3日",     # 超短持有周期
    "stop_loss_pct": -0.05,      # 硬止损 -5%
    "take_profit_pct": 0.10,     # 止盈 10%（策略文档默认 8-15% 区间取中）
    "buy_zone_pct": 0.02,        # 买入区间：信号价 ±2%
}


def _f(v, default=0.0):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def _alloc_positions(total_score: float, params: dict) -> float:
    """按综合分分配单票仓位占比（总仓位上限内，分高者仓重）。
    总分按「可得维度归一化」：data_completeness<1 时，总分先按可得权重还原到0-1标尺，
    避免诚实缺省维度把总分压到永远无法过门槛（不虚增，只归一化标尺）。
    """
    max_pos = _f(params.get("max_position", 0.25))
    # 综合分 0.5 起配，最高到 max_pos；归一化后用 adjusted_score
    adjusted = _normalize_score(total_score, params)
    if adjusted < 0.5:
        return 0.0
    ratio = _clamp((adjusted - 0.5) / 0.5, 0, 1)  # 0.5→0%, 1.0→100% of max_pos
    return round(max_pos * ratio, 4)


def _normalize_score(total_score: float, params: dict) -> float:
    """按可得维度权重和归一化总分到 0-1 标尺（诚实缺省维度不虚增、不拖垮）。"""
    completeness = _f(params.get("data_completeness", 1.0))
    if completeness <= 0:
        return total_score  # 无数据可得，保持原值（不会过门槛）
    # 还原：total_score 是「可得权重加权和」，除以可得权重和 → 0-1 标尺
    return _clamp(total_score / completeness, 0, 1)


def build_plan(scored: list[dict], signals: list[dict] | None = None,
               params: dict | None = None) -> dict:
    """汇总评分 + 5层信号 → 作战计划。
    scored: scorer.score_pool 输出（已按综合分降序）。
    signals: strategy.generate_signals 输出（5层共振确认的信号，可能为 hold）。
    params: 覆盖默认作战参数。
    返回作战计划 dict（格式按用户已确认）。
    """
    params = {**DEFAULT_PLAN_PARAMS, **(params or {})}
    signals = signals or []

    # 收集 5层确认的信号标的（有 code 且 action=buy）
    signal_codes = {s.get("code") for s in signals if s.get("action") == "buy" and s.get("code")}
    # 5层信号为空但评分可用时，退回按综合分 Top（诚实：仅当无信号确认时用评分兜底排序）
    use_signal_only = bool(signal_codes)

    max_total = _f(params.get("max_total_position", 0.80))
    max_pos = _f(params.get("max_position", 0.25))

    targets = []
    for s in scored:
        code = s.get("code")
        if use_signal_only and code not in signal_codes:
            continue  # 有5层信号确认时，只保留确认标的
        score = _f(s.get("total_score"))
        # 按可得维度归一化总分（诚实缺省维度不拖垮）
        completeness = _f(s.get("data_completeness", 1.0))
        adjusted = _normalize_score(score, {"data_completeness": completeness})
        if adjusted < 0.5:
            continue  # 归一化后综合分仍不足，不入作战计划

        price = _f(s.get("ref_price", 0))
        if price <= 0:
            price = _f((s.get("pool_row") or {}).get("price"))
        if price <= 0:
            continue  # 无真实价格，无法定买卖区间（诚实跳过）

        pos_ratio = _alloc_positions(score, {"data_completeness": completeness,
                                             "max_position": _f(params.get("max_position", 0.25))})
        if pos_ratio <= 0:
            continue

        buy_low = round(price * (1 - _f(params.get("buy_zone_pct", 0.02))), 2)
        buy_high = round(price * (1 + _f(params.get("buy_zone_pct", 0.02))), 2)
        stop_loss = round(price * (1 + _f(params.get("stop_loss_pct", -0.05))), 2)
        take_profit = round(price * (1 + _f(params.get("take_profit_pct", 0.10))), 2)

        targets.append({
            "code": code,
            "name": s.get("name", ""),
            "total_score": score,
            "score_detail": s.get("score_detail", {}),
            "position_ratio": pos_ratio,
            "buy_zone": {"low": buy_low, "high": buy_high},
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "plan_reason": f"综合分{score:.2f}（{s.get('weights', {}).get('technical')}技术/"
                           f"{s.get('weights', {}).get('capital')}资金/"
                           f"{s.get('weights', {}).get('theme')}题材/"
                           f"{s.get('weights', {}).get('message')}消息）",
        })

    # 总仓位占比：各标的分仓占比不超过 max_total
    total_alloc = sum(_f(t["position_ratio"]) for t in targets)
    if total_alloc > max_total:
        # 按比例缩放至总仓位上限（保持相对权重）
        scale = max_total / total_alloc if total_alloc > 0 else 0
        for t in targets:
            t["position_ratio"] = round(_f(t["position_ratio"]) * scale, 4)

    return {
        "plan_version": "1.0",
        "generated": True,
        "holding_days": params.get("holding_days", "1-3日"),
        "stop_loss_rule": {"pct": params.get("stop_loss_pct", -0.05)},
        "take_profit_rule": {"pct": params.get("take_profit_pct", 0.10)},
        "max_total_position": max_total,
        "max_single_position": max_pos,
        "targets": targets,
        "total_allocated": round(sum(_f(t["position_ratio"]) for t in targets), 4),
        "signal_confirmed": bool(signal_codes),
    }


if __name__ == "__main__":
    # 冒烟测试：用 scorer 输出构造（真实评分数据，仅演示结构）
    import csv
    from datetime import date as _date
    import data_source, scorer

    d = _date.today().isoformat()
    pool_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "data", f"super_short_pool_{d}.csv")
    rows = []
    with open(pool_path, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    print(f"=== plan_builder.py 冒烟测试（真实数据 {d}）===")
    strength_map, seat_map, catalyst_map = {}, {}, {}
    for r in rows[:10]:
        code = r["code"]
        try:
            strength_map[code] = data_source.fetch_stock_strength(code, d)
        except Exception:
            pass
        try:
            seat_map[code] = data_source.fetch_seat_and_fund(code, d)
        except Exception:
            pass
        try:
            catalyst_map[code] = data_source.fetch_catalyst_and_auction(code, d)
        except Exception:
            pass

    scored = scorer.score_pool(rows[:10], strength_map, seat_map, catalyst_map, top_n=10)
    # 给 scored 补 ref_price（真实价格）
    price_map = {r["code"]: _f(r.get("price")) for r in rows}
    for s in scored:
        s["ref_price"] = price_map.get(s["code"], 0)
        s["pool_row"] = next((r for r in rows if r["code"] == s["code"]), {})

    plan = build_plan(scored, signals=[])
    print(f"\n=== 作战计划（无5层信号，按综合分兜底）===")
    print(f"总仓位上限 {plan['max_total_position']}，已分配 {plan['total_allocated']}，"
          f"持有 {plan['holding_days']}，止损 {plan['stop_loss_rule']['pct']}")
    for t in plan["targets"][:5]:
        print(f"  {t['code']} {t['name']}: 分{t['position_ratio']:.0%} "
              f"买入{t['buy_zone']['low']}-{t['buy_zone']['high']} "
              f"止损{t['stop_loss']} 目标{t['take_profit']}")
    print("\n冒烟测试通过 (exit 0)")
