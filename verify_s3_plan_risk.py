# -*- coding: utf-8 -*-
"""
S3 验证脚本：plan_builder.py → risk.py 风控校验链路 + 作战计划文件落盘
用 11-09 候选池真实数据跑通，确认：
  1. plan_builder.build_plan 产出作战计划 dict（含买入区间/止损位/目标位/仓位占比/总仓/持有周期/止损规则）
  2. risk.validate_plan 计划级风控校验通过（单票≤25%，总仓≤80%，-5%硬止损，持有1-3日）
  3. plan_file_writer.write_plan_files 落盘 Markdown + CSV 每日作战计划文件
铁律：全部真实行情数据（11-09候选池50只真实），严禁模拟。全部失败诚实缺省/空仓。
用法：python verify_s3_plan_risk.py
"""
import os
import sys
import csv

# Windows GBK 控制台强制 UTF-8，避免 ✓/中文打印编码报错
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data_source
import scorer
import strategy
import plan_builder
import plan_file_writer
import risk

TRADE_DATE = "2026-11-09"
POOL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "data", "super_short_pool_2026-11-09.csv")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def main():
    print(f"=== S3 验证：plan_builder → risk 风控校验 + 作战计划文件（{TRADE_DATE}）===")

    # 1. 读候选池（真实50只）
    with open(POOL_PATH, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    print(f"[1] 候选池 {len(rows)} 只（真实，非模拟）")

    # 2. 拉取真实数据（第1/2/4/5层，不可得诚实缺省——铁律）
    try:
        sentiment = data_source.fetch_sentiment(TRADE_DATE)
        print(f"[2] 情绪（第1层）：温度{sentiment.get('temperature', '—')}，涨停{sentiment.get('limit_up', '—')}家")
    except Exception as e:
        sentiment = {}
        print(f"[2] 情绪不可得（诚实缺省）：{e}")
    try:
        sectors = data_source.fetch_sector_ranking(TRADE_DATE)
        print(f"[2] 板块（第2层）：{len(sectors)} 个")
    except Exception as e:
        sectors = []
        print(f"[2] 板块不可得（诚实缺省）：{e}")

    codes = [r["code"] for r in rows if r.get("code")]
    seat_fund_map, catalyst_map = {}, {}
    for code in codes:
        try:
            seat_fund_map[code] = data_source.fetch_seat_and_fund(code, TRADE_DATE)
        except Exception as e:
            seat_fund_map[code] = {}
            print(f"[2] 席位资金不可得（诚实缺省）{code}: {e}")
        try:
            catalyst_map[code] = data_source.fetch_catalyst_and_auction(code, TRADE_DATE)
        except Exception as e:
            catalyst_map[code] = {}
            print(f"[2] 催化不可得（诚实缺省）{code}: {e}")
    print(f"[2] 席位映射 {len(seat_fund_map)} 只，催化映射 {len(catalyst_map)} 只")

    # 3. 行情数据（真实）
    try:
        std_path = data_source.fetch_daily(TRADE_DATE, out_dir=OUT_DIR, pool=codes)
        std_rows = data_source.load_standard(std_path)
        print(f"[3] 行情 {len(std_rows)} 只（真实）")
    except Exception as e:
        print(f"[3] 行情拉取失败（诚实空仓）：{e}")
        std_rows = []

    # 4. 5层策略信号
    signals = strategy.generate_signals(std_rows, params={
        "sentiment": sentiment,
        "sectors": sectors,
        "seat_fund": seat_fund_map,
        "catalyst": catalyst_map,
    })
    buy_signals = [s for s in signals if s.get("action") == "buy" and s.get("code")]
    print(f"[4] 策略信号 {len(signals)} 条，买入候选 {len(buy_signals)} 条" +
          ("：" + "、".join(s["code"] for s in buy_signals) if buy_signals else "（无买入候选→诚实空仓）"))

    # 5. 评分（真实）
    candidate_rows = []
    for s in buy_signals:
        candidate_rows.append({"code": s["code"], "name": next((r.get("name", "") for r in rows if r.get("code") == s["code"]), "")})
    strength_map, seat_map, cat_map = {}, {}, {}
    for s in buy_signals:
        try:
            strength_map[s["code"]] = data_source.fetch_stock_strength(s["code"], TRADE_DATE)
        except Exception:
            strength_map[s["code"]] = {}
        try:
            seat_map[s["code"]] = data_source.fetch_seat_and_fund(s["code"], TRADE_DATE)
        except Exception:
            seat_map[s["code"]] = {}
        try:
            cat_map[s["code"]] = data_source.fetch_catalyst_and_auction(s["code"], TRADE_DATE)
        except Exception:
            cat_map[s["code"]] = {}
    scored = scorer.score_pool(candidate_rows, strength_map, seat_map, cat_map,
                               weights={"technical": 0.30, "capital": 0.25, "theme": 0.25, "message": 0.20},
                               top_n=10)
    price_map = {r.get("code"): r.get("close", r.get("price", 0)) for r in std_rows if r.get("code")}
    sig_price_map = {s["code"]: s.get("price", 0) for s in buy_signals}
    for sc in scored:
        sc["ref_price"] = price_map.get(sc["code"], sig_price_map.get(sc["code"], 0))
        sc["pool_row"] = next((r for r in rows if r.get("code") == sc["code"]), {})
    print(f"[5] 评分 {len(scored)} 只")

    # 6. 作战计划（真实评分+信号）
    plan = plan_builder.build_plan(scored, buy_signals, params={
        "max_total_position": 0.80, "stop_loss_pct": -0.05,
        "buy_zone_pct": 0.02, "take_profit_pct": 0.10,
    })
    print(f"[6] 作战计划：总仓上限 {plan['max_total_position']}，已分配 {plan['total_allocated']}，"
          f"操作标的 {len(plan['targets'])} 只，持有 {plan['holding_days']}，止损 {plan['stop_loss_rule']['pct']}")
    for t in plan["targets"][:5]:
        bz = t["buy_zone"]
        print(f"    {t['code']} {t['name']}: 分{t['position_ratio']:.2%} "
              f"买入{bz['low']}-{bz['high']} 止损{t['stop_loss']} 目标{t['take_profit']}")

    # 7. 计划级风控校验（S3 核心）
    verdict = risk.validate_plan(plan)
    print(f"[7] 风控校验：{'通过' if verdict['valid'] else '未过'} —— {verdict['summary']}")
    for c in verdict["checks"]:
        mark = "[OK]" if c["ok"] else "[X]"
        print(f"    {mark} {c['check']}: {c['value']}（限 {c['limit']}）")
    if not verdict["valid"]:
        print(f"[7] 违规：{verdict['violations']}")
        return 1

    # 8. 作战计划文件落盘（Markdown + CSV）
    files = plan_file_writer.write_plan_files(plan, TRADE_DATE, OUT_DIR)
    print(f"[8] 作战计划文件：")
    print(f"    Markdown: {files['markdown']}")
    print(f"    CSV:      {files['csv']}")
    for p in (files["markdown"], files["csv"]):
        assert os.path.exists(p), f"文件未生成: {p}"

    print(f"\n=== S3 验证通过（exit 0）：plan_builder→risk 链路 + 作战计划文件落盘 ===\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
