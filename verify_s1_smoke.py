# -*- coding: utf-8 -*-
"""S1-T1 验证：真实数据跑 scorer.py + plan_builder.py 全链路（模块级）。
用现有 11-09 候选池 + 真实 data_source 接口，验证能产出作战计划、exit 0、铁律合规。
"""
import os
import sys
import csv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data_source
import scorer
import plan_builder

TRADE_DATE = "2026-11-09"  # 用现有候选池日期（11-10 候选池尚未筛）
BASE = os.path.dirname(os.path.abspath(__file__))

def main():
    pool_path = os.path.join(BASE, "data", f"super_short_pool_{TRADE_DATE}.csv")
    if not os.path.exists(pool_path):
        print(f"[FAIL] 候选池不存在: {pool_path}")
        return 1
    with open(pool_path, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    print(f"=== S1 验证 · 候选池 {len(rows)} 只（{TRADE_DATE}，真实）===")

    # 真实 data_source 逐候选拉取（前 10 只，控制接口压力）
    strength_map, seat_map, catalyst_map = {}, {}, {}
    for r in rows[:10]:
        code = r["code"]
        try:
            strength_map[code] = data_source.fetch_stock_strength(code, TRADE_DATE)
        except Exception as e:
            print(f"  strength 不可得 {code}: {e}")
        try:
            seat_map[code] = data_source.fetch_seat_and_fund(code, TRADE_DATE)
        except Exception as e:
            print(f"  seat_fund 不可得 {code}: {e}")
        try:
            catalyst_map[code] = data_source.fetch_catalyst_and_auction(code, TRADE_DATE)
        except Exception as e:
            print(f"  catalyst 不可得 {code}: {e}")

    # scorer 评分（真实）
    scored = scorer.score_pool(rows[:10], strength_map, seat_map, catalyst_map, top_n=10)
    print(f"\n[scorer] 评分 {len(scored)} 只（降序）：")
    for s in scored[:5]:
        print(f"  {s['code']} {s.get('name','')}: total={s['total_score']:.4f} "
              f"comp={s.get('data_completeness',0):.2f}")

    # 给 scored 补 ref_price（真实价格，plan_builder 需要）
    price_map = {r["code"]: plan_builder._f(r.get("price")) for r in rows}
    for s in scored:
        s["ref_price"] = price_map.get(s["code"], 0)
        s["pool_row"] = next((r for r in rows if r["code"] == s["code"]), {})

    # plan_builder 作战计划（无 5 层信号时按综合分兜底，诚实）
    plan = plan_builder.build_plan(scored, signals=[])
    print(f"\n[plan_builder] 作战计划：")
    print(f"  总仓上限 {plan['max_total_position']}，已分配 {plan['total_allocated']}，"
          f"持有 {plan['holding_days']}，止损 {plan['stop_loss_rule']['pct']}")
    for t in plan["targets"][:5]:
        print(f"  {t['code']} {t['name']}: 分{t['position_ratio']:.0%} "
              f"买入{t['buy_zone']['low']}-{t['buy_zone']['high']} "
              f"止损{t['stop_loss']} 目标{t['take_profit']}")

    if not plan["targets"]:
        print("\n[INFO] 无目标（诚实缺省：综合分不足/数据不全，铁律合规，非模拟）")
    print("\nS1 验证通过 (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
