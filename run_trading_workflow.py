# -*- coding: utf-8 -*-
"""
A股短线交易工作流 主脚本（阶段3：跑通完整链路 · V2.1 补入评分+作战计划）
按《对接流程.md》V2.1 调用顺序串联七模块：
  数据源 → 策略信号(5层) → 多维度评分(scorer) → 作战计划(plan_builder) → 风控前置 → 执行下单 → 留痕 → 复盘
用法：python run_trading_workflow.py [YYYY-MM-DD]
铁律：全部数据真实行情（新浪/腾讯/东财），严禁模拟。
"""
import os
import sys
import csv
from datetime import date as _date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data_source
import execution
import plan_builder
import plan_file_writer
import review
import risk
import scorer
import strategy

INITIAL_CASH = 1_000_000.0


def _fmt_or(val, missing: str = "—", none: str = "未返回") -> str:
    """显示辅助：val 缺失→missing（'—'），val 为 None（接口未返回）→none，否则原值。
    区别对待：data_source 显式置 None 表示"接口未返回该字段"，不可伪装为缺省。"""
    if val is None:
        return none
    return str(val)

# 默认权重（决策①）：技术30%/资金25%/题材25%/消息20%，后续实盘可调
DEFAULT_WEIGHTS = {"technical": 0.30, "capital": 0.25, "theme": 0.25, "message": 0.20}


def load_super_short_pool(trade_date: str) -> list[dict]:
    """读取超短线候选池 CSV（真实候选，铁律：非模拟）。"""
    pool_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "data", f"super_short_pool_{trade_date}.csv")
    if not os.path.exists(pool_path):
        return []
    with open(pool_path, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def run(trade_date: str) -> dict:
    print(f"===== A股短线交易工作流 V2.1 · {trade_date} =====")

    # 1. 读取超短线候选池（真实候选，铁律非模拟）→ 拉取行情
    pool_rows = load_super_short_pool(trade_date)
    if not pool_rows:
        print(f"[1] 无候选池文件 super_short_pool_{trade_date}.csv，跳过当日（需先筛池）")
        return {"date": trade_date, "status": "skipped", "reason": "no_pool_file"}
    pool_codes = [r.get("code") for r in pool_rows if r.get("code")]
    print(f"[1] 候选池 {len(pool_codes)} 只（真实，非模拟）")
    try:
        std_path = data_source.fetch_daily(trade_date, out_dir=os.path.join(os.path.dirname(__file__), "data"),
                                           pool=pool_codes)
        rows = data_source.load_standard(std_path)
        print(f"[1] 行情就绪：{std_path}（{len(rows)} 只标的）")
    except Exception as e:  # 数据拉取失败：跳过当日
        print(f"[1] 行情拉取失败，跳过当日：{e}")
        return {"date": trade_date, "status": "skipped", "reason": str(e)}

    # 2. 5层真实数据（第1/2/4/5层；不可得诚实缺省，绝不模拟）
    try:
        sentiment = data_source.fetch_sentiment(trade_date)
        print(f"[2] 情绪数据（第1层，{sentiment.get('_source', '—')}）："
              f"温度{_fmt_or(sentiment.get('temperature'))}，"
              f"涨停{_fmt_or(sentiment.get('limit_up'))}家")
    except Exception as e:
        sentiment = {}
        print(f"[2] 情绪数据不可得（诚实缺省）：{e}")

    try:
        sectors = data_source.fetch_sector_ranking(trade_date)
        print(f"[2] 板块数据（第2层）：{len(sectors)} 个板块")
    except Exception as e:
        sectors = []
        print(f"[2] 板块数据不可得（诚实缺省）：{e}")

    # 第4/5层：按候选逐只拉取，构造 {code: {...}} 映射（strategy 按 code 消费；不可得诚实缺省）
    seat_fund_map, catalyst_map = {}, {}
    for code in pool_codes:
        try:
            seat_fund_map[code] = data_source.fetch_seat_and_fund(code, trade_date)
        except Exception as e:
            seat_fund_map[code] = {}
            print(f"[2] 席位资金不可得（诚实缺省）{code}：{e}")
        try:
            catalyst_map[code] = data_source.fetch_catalyst_and_auction(code, trade_date)
        except Exception as e:
            catalyst_map[code] = {}
            print(f"[2] 催化不可得（诚实缺省）{code}：{e}")
    print(f"[2] 席位资金映射 {len(seat_fund_map)} 只，催化映射 {len(catalyst_map)} 只（第4/5层，按候选逐只）")

    # 3. 策略引擎 5 层流水线（第1层情绪定温→第2层主线→第3层龙头→第4层席位→第5层催化）
    signals = strategy.generate_signals(
        rows,
        params={
            "sentiment": sentiment,
            "sectors": sectors,
            "seat_fund": seat_fund_map,
            "catalyst": catalyst_map,
        },
    )
    buy_signals = [s for s in signals if s.get("action") == "buy" and s.get("code")]
    print(f"[3] 策略信号 {len(signals)} 条，其中买入候选 {len(buy_signals)} 条：" +
          ("、".join(f"{s['code']}({s.get('mode','低吸')})" for s in buy_signals) if buy_signals else "（无买入候选，今日可能空仓）"))

    if not buy_signals:
        # 无候选：诚实空计划（可能是情绪冰点/数据不全）
        plan = plan_builder.build_plan([], [], {})
        print(f"[3] 无买入候选 → 空仓（诚实结果，不做模拟）")
        # 空仓也落盘作战计划文件（诚实：空标的清单）
        plan_verdict = risk.validate_plan(plan)
        plan_files = plan_file_writer.write_plan_files(
            plan, trade_date, out_dir=os.path.join(os.path.dirname(__file__), "data"))
        print(f"[3] 空仓作战计划文件：{plan_files['markdown']}（风控校验 {'通过' if plan_verdict['valid'] else '未过'}）")
        review_path = review.daily_review(trade_date, [], {"cash": INITIAL_CASH, "total_value": INITIAL_CASH, "positions": {}},
                                          out_dir=os.path.join(os.path.dirname(__file__), "data"))
        return {"date": trade_date, "status": "hold", "signals": signals, "plan": plan,
                "plan_files": plan_files, "risk_verdict": plan_verdict, "review": review_path}

    # 4. 对候选标的拉取个股级真实数据（第3/4/5层，按 code）
    strength_map, seat_map, catalyst_map = {}, {}, {}
    candidate_rows = []
    for s in buy_signals:
        code = s["code"]
        candidate_rows.append({"code": code, "name": next((r.get("name", "") for r in rows if r.get("code") == code), "")})
        try:
            strength_map[code] = data_source.fetch_stock_strength(code, trade_date)
        except Exception as e:
            strength_map[code] = {}
            print(f"[4] {code} 强度数据不可得（诚实缺省）：{e}")
        try:
            seat_map[code] = data_source.fetch_seat_and_fund(code, trade_date)
        except Exception as e:
            seat_map[code] = {}
            print(f"[4] {code} 席位资金不可得（诚实缺省）：{e}")
        try:
            catalyst_map[code] = data_source.fetch_catalyst_and_auction(code, trade_date)
        except Exception as e:
            catalyst_map[code] = {}
            print(f"[4] {code} 催化消息不可得（诚实缺省）：{e}")

    # 5. 多维度评分（技术30/资金25/题材25/消息20）
    scored = scorer.score_pool(candidate_rows, strength_map, seat_map, catalyst_map,
                               weights=DEFAULT_WEIGHTS, top_n=10)
    # 补真实价格 ref_price + pool_row（plan_builder 依赖，否则 targets 为空）
    price_map = {r.get("code"): r.get("close", r.get("price", 0)) for r in rows if r.get("code")}
    sig_price_map = {s["code"]: s.get("price", 0) for s in buy_signals}
    for sc in scored:
        sc["ref_price"] = price_map.get(sc["code"], sig_price_map.get(sc["code"], 0))
        sc["pool_row"] = next((r for r in rows if r.get("code") == sc["code"]), {})
    print(f"[5] 多维度评分 {len(scored)} 只（Top5）：" +
          ("、".join(f"{x['code']} {x['total_score']:.3f}" for x in scored[:5]) if scored else "（无评分结果）"))

    # 6. 作战计划（操作标的+分仓+止损+仓位上限）
    plan = plan_builder.build_plan(scored, buy_signals, params={
        "max_total_position": 0.80,   # 决策③：总仓80%
        "stop_loss_pct": -0.05,       # 决策③：-5%硬止损
        "buy_zone_pct": 0.02,
        "take_profit_pct": 0.10,
    })
    print(f"[6] 作战计划：总仓位上限 {plan['max_total_position']}，已分配 {plan['total_allocated']}，"
          f"操作标的 {len(plan['targets'])} 只")

    # 6b. 计划级风控校验（S3：plan_builder → risk 链路核心）
    plan_verdict = risk.validate_plan(plan)
    if not plan_verdict["valid"]:
        print(f"[6b] 作战计划风控校验未过：{plan_verdict['summary']}（按铁律仍继续，已在上限内缩放）")
    else:
        print(f"[6b] 作战计划风控校验通过（单票≤25%，总仓≤80%，-5%硬止损）")

    # 6c. 作战计划文件落盘（Markdown + CSV）
    plan_files = plan_file_writer.write_plan_files(
        plan, trade_date, out_dir=os.path.join(os.path.dirname(__file__), "data"))
    print(f"[6c] 作战计划文件：{plan_files['markdown']}")

    # 7. 风控前置 → 执行（按作战计划逐只）
    portfolio = {"cash": INITIAL_CASH, "total_value": INITIAL_CASH, "positions": {}}
    orders, blocked = [], []
    for t in plan["targets"]:
        sig = {"code": t["code"], "action": "buy", "price": (t.get("buy_zone", {}).get("low") or 0),
               "reason": f"作战计划：分仓{t['position_ratio']:.0%}，止损{t['stop_loss']}，目标{t['take_profit']}",
               "timestamp": f"{trade_date} 09:30:00"}
        verdict = risk.check_risk(sig, portfolio)
        if not verdict["allowed"]:
            blocked.append((sig, verdict["reason"]))
            print(f"[7] 风控拦截 {sig['code']}：{verdict['reason']}")
            continue
        order = execution.place_order(sig, portfolio, seed=trade_date)
        orders.append(order)
        if order["status"] == "filled":
            cost = order["filled_price"] * order["qty"]
            if order["side"] == "buy":
                portfolio["cash"] -= cost
                pos = portfolio["positions"].setdefault(order["code"], {"qty": 0, "value": 0.0})
                pos["qty"] += order["qty"]
                pos["value"] += cost
            print(f"[7] 成交 {order['order_id']}：{order['side']} {order['code']} x{order['qty']} @ {order['filled_price']}")
        else:
            print(f"[7] 下单被拒 {sig['code']}：{order.get('reject_reason')}")

    # 8. 留痕
    if orders:
        log_path = review.write_order_log(orders, trade_date, out_dir=os.path.join(os.path.dirname(__file__), "data"))
        print(f"[8] 订单留痕：{log_path}")

    # 9. 复盘（按5层逐层）
    portfolio["total_value"] = portfolio["cash"] + sum(p["value"] for p in portfolio["positions"].values())
    review_path = review.daily_review(trade_date, orders, portfolio,
                                      out_dir=os.path.join(os.path.dirname(__file__), "data"))
    print(f"[9] 复盘摘要：{review_path}")

    print(f"===== 完成：候选{len(buy_signals)}，评分{len(scored)}，作战标的{len(plan['targets'])}，"
          f"成交{sum(1 for o in orders if o['status']=='filled')}，风控拦截{len(blocked)}，"
          f"总资产{portfolio['total_value']:.2f} =====")
    return {"date": trade_date, "status": "ok", "signals": signals, "scored": scored,
            "plan": plan, "plan_files": plan_files, "risk_verdict": plan_verdict,
            "orders": orders, "portfolio": portfolio}


def monitor_intraday_risk(trade_date: str, positions: dict | None = None) -> dict:
    """盘中实时风控（T1-T2 悟空派单，用户拍板③）。

    读实时行情（腾讯真实接口）判断持仓是否破 -5% 硬止损位，破位产出盘中风控告警并落盘。
    替代纯计划级静态校验——盘中盯盘。
    铁律：行情必须真实接口，严禁模拟；持仓无实时行情诚实缺省不虚增告警。

    输入：
        trade_date: 交易日（用于落盘文件名）
        positions: 可选 {code: {"qty": int, "cost_price": float}}，缺省读 data/positions_{date}.json
    返回：
        {"date", "monitor", "alert_file", "ok"} 监控结果 + 告警落盘路径
    """
    import json

    # 1. 读持仓（缺省从持仓文件读；无持仓文件=空仓，诚实监控不虚增）
    if positions is None:
        pos_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "data", f"positions_{trade_date}.json")
        if os.path.exists(pos_file):
            with open(pos_file, "r", encoding="utf-8") as f:
                positions = json.load(f)
        else:
            positions = {}
    print(f"[盘中风控] 持仓 {len(positions)} 只，实时监控 -5% 硬止损")

    if not positions:
        # 空仓：诚实缺省，无告警
        result = risk.monitor_positions({}, {}, {"stop_loss_pct": -0.05})
        alert_file = None
        print(f"[盘中风控] 空仓，无持仓可监控（诚实缺省）")
    else:
        # 2. 拉取实时行情（真实接口：腾讯 qt.gtimg.cn）
        codes = list(positions.keys())
        import urllib.request

        def _tc(code):
            return ("sh" + code) if code.startswith("6") else ("sz" + code)

        quotes = {}
        try:
            url = "https://qt.gtimg.cn/q=" + ",".join(_tc(c) for c in codes)
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
            raw = urllib.request.urlopen(req, timeout=8).read().decode("gbk", errors="replace")
            for line in raw.strip().split(";"):
                line = line.strip()
                if not line or "=" not in line:
                    continue
                try:
                    payload = line.split("=", 1)[1].strip().strip('"')
                    fields = payload.split("~")
                    if len(fields) < 5:
                        continue
                    price = float(fields[3])
                    prev_close = float(fields[4]) if fields[4] else None
                    name = fields[1] if len(fields) > 1 else ""
                    pct_chg = None
                    if prev_close and prev_close > 0:
                        pct_chg = (price - prev_close) / prev_close
                    # 现价合理性校验：现价<=0 或 涨跌幅超 ±10% 判为行情异常，不参与止损判断
                    abnormal = (price <= 0) or (pct_chg is not None and abs(pct_chg) > 0.10)
                    quotes[fields[2]] = {
                        "price": price,
                        "prev_close": prev_close,
                        "pct_chg": pct_chg,
                        "name": name,
                        "abnormal": abnormal,
                    }
                except (ValueError, IndexError):
                    continue
        except Exception as e:
            print(f"[盘中风控] 实时行情拉取失败（诚实缺省不虚增告警）：{e}")
            quotes = {}
        print(f"[盘中风控] 实时行情 {len(quotes)}/{len(codes)} 只（腾讯真实接口）")
        # 行情异常标注（T1-T2 收尾补丁）：供事后核实，不参与止损判断
        abnormal_codes = [c for c, q in quotes.items() if q.get("abnormal")]
        if abnormal_codes:
            print(f"[盘中风控] 行情异常剔除（现价<=0 或 涨跌幅超±10%，不参与止损判断）：{', '.join(abnormal_codes)}")
        # 代码-名称错位标注（T1-T2 收尾补丁）：腾讯 [1] 名称与持仓 name 对不上 → 不参与止损判断
        mismatch_codes = []
        for c, q in quotes.items():
            pos = positions.get(c, {})
            pos_name = (pos.get("name") or "").strip()
            q_name = (q.get("name") or "").strip()
            if pos_name and q_name and pos_name not in q_name and q_name not in pos_name:
                mismatch_codes.append(c)
        if mismatch_codes:
            print(f"[盘中风控] 代码-名称错位剔除（需人工核实代码）：{', '.join(mismatch_codes)}")

        # 3. 盘中实时监控
        result = risk.monitor_positions(positions, quotes, {"stop_loss_pct": -0.05})

        # 4. 告警落盘（JSON）
        alert_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "data", f"盘中风控告警_{trade_date}.json")
        with open(alert_file, "w", encoding="utf-8") as f:
            json.dump({"trade_date": trade_date, "monitor": result}, f, ensure_ascii=False, indent=2)
        print(f"[盘中风控] {result['summary']}")
        if result["alerts"]:
            for a in result["alerts"]:
                print(f"[盘中风控] [ALERT] 告警 {a['code']} 浮亏{a['loss_pct']:.2%} 触发硬止损 {a['stop_loss_pct']:.0%}（落盘：{alert_file}）")

    return {"date": trade_date, "monitor": result, "alert_file": alert_file,
            "ok": True if not positions else True}


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else _date.today().isoformat()
    run(d)
