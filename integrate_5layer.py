# -*- coding: utf-8 -*-
"""
5层完整链路集成驱动（阶段3跑通）
铁律：全部真实行情，严禁模拟。
职责：读真实候选池 → data_source 拉5层真实数据 → 适配 → strategy.generate_signals(params)
      → risk → execution → review 串联落盘。
契约适配层：把 data_source 的原始输出转成 strategy.layer4/layer5 期望的形状。
"""
import os
import sys
import time
import csv
from datetime import date as _date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import data_source
import execution
import review
import risk
import strategy

INITIAL_CASH = 1_000_000.0
WORKDIR = os.path.dirname(os.path.abspath(__file__))


# ============ 契约适配层：data_source 原始输出 → strategy 期望形状 ============

def adapt_seat_fund(raw: dict) -> dict:
    """data_source.fetch_seat_and_fund 返回 → layer4_seat_fund 期望的 seat_data[code]。
    raw: {code, dragon_tiger:[{net_amt,buy_amt,sell_amt,reason}], fund_flow:{...}, margin:{...}}
    layer4 期望: {code: {dragon_tiger:str, super_large_net:float, large_net:float, margin_change:float}}
    """
    ff = raw.get("fund_flow") or {}
    dragon_list = raw.get("dragon_tiger") or []
    # layer4 的 dragon_tiger 是 str（用于匹配游资关键词）；拼接 reason 串
    dragon_str = "；".join(str(d.get("reason", "")) for d in dragon_list if d.get("reason"))
    # 单位换算：元 → 亿（layer4 的 super_large_net/large_net 单位是"亿"）
    return {
        "code": raw.get("code"),
        "dragon_tiger": dragon_str,
        "super_large_net": round(float(ff.get("super_net") or 0) / 1e8, 4),
        "large_net": round(float(ff.get("large_net") or 0) / 1e8, 4),
        "margin_change": round(float((raw.get("margin") or {}).get("rzye_change") or 0) / 1e8, 4),
    }


def adapt_catalyst(raw: dict) -> dict:
    """data_source.fetch_catalyst_and_auction 返回 → layer5_catalyst 期望的 catalyst_data[code]。
    raw: {code, catalyst:[], announcement:[], auction:{open,auction_vol,bid1_*,ask1_*,pct_chg,turnoverratio}}
    layer5 期望: {code: {catalyst:str, announcement:str, auction_pct:float, auction_seal:float}}
    注意：data_source.catalyst/announcement 为诚实缺省 []（未接可靠免费源）。
    这里把"有催化/公告"判定为非空列表才视为有；若为空则视为无（诚实，不编造）。
    """
    catalyst_list = raw.get("catalyst") or []
    ann_list = raw.get("announcement") or []
    auction = raw.get("auction") or {}
    # 若列表元素是 dict，取文本字段；若已是 str 直接用
    def _txt(items):
        return "；".join(
            str(x.get("text", "")) if isinstance(x, dict) else str(x)
            for x in items if x
        )
    return {
        "code": raw.get("code"),
        "catalyst": _txt(catalyst_list),
        "announcement": _txt(ann_list),
        # 竞价涨幅：data_source.auction.pct_chg（%）；竞价封单强度用买一档量近似
        "auction_pct": float(auction.get("pct_chg") or 0),
        "auction_seal": float(auction.get("bid1_vol") or 0),
    }


def load_pool(path: str) -> list[dict]:
    """读候选池 CSV → 标准化行情行（策略 layer3 输入字段）。
    候选池字段：code,name,price,pct_chg,turnover,float_mv_yi,total_mv_yi,pe,date
    映射 strategy.layer3 期望：code/close/pct_chg/amount(元)/turnover/float_mv(元)/date
    """
    rows = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            # 候选池只有当日快照，无成交额字段 → amount 用真实市值近似不可行，置0（诚实）
            rows.append({
                "code": r["code"],
                "name": r.get("name", ""),
                "close": float(r.get("price") or 0),
                "pct_chg": float(r.get("pct_chg") or 0),
                "turnover": float(r.get("turnover") or 0),
                "float_mv": float(r.get("float_mv_yi") or 0) * 1e8,  # 亿→元（layer3 识别单位）
                "date": r.get("date", ""),
                "amount": 0.0,  # 候选池无成交额，诚实缺省；layer3 按 0 处理不给加分
            })
    return rows


def run(trade_date: str, pool_path: str | None = None) -> dict:
    print(f"===== A股短线交易工作流 5层完整链路 · {trade_date} =====")
    pool_path = pool_path or os.path.join(WORKDIR, "data", f"super_short_pool_{trade_date}.csv")
    if not os.path.exists(pool_path):
        print(f"[0] 候选池不存在：{pool_path}")
        return {"date": trade_date, "status": "skipped", "reason": f"候选池缺失 {pool_path}"}

    # 0. 读真实候选池
    rows = load_pool(pool_path)
    print(f"[0] 候选池就绪：{len(rows)} 只真实标的（{pool_path}）")
    if not rows:
        print("[0] 候选池为空，跳过")
        return {"date": trade_date, "status": "skipped", "reason": "候选池为空"}

    # 1. 情绪（第1层）
    try:
        sentiment = data_source.fetch_sentiment(trade_date)
        print(f"[1] 情绪数据（真实，来源 {sentiment.get('_source')}）："
              f"涨{sentiment.get('up_count')}/跌{sentiment.get('down_count')} "
              f"涨停{sentiment.get('limit_up')} 连板高度{sentiment.get('max_lianban')} "
              f"成交额{sentiment.get('total_amount')}亿")
    except Exception as e:
        print(f"[1] 情绪拉取失败，跳过当日：{e}")
        return {"date": trade_date, "status": "skipped", "reason": f"情绪失败 {e}"}

    # 2. 板块（第2层）
    try:
        sectors = data_source.fetch_sector_ranking(trade_date)
        print(f"[2] 板块数据（真实）：{len(sectors)} 条，前3 "
              + "、".join(f"{s.get('sector_name')}({s.get('pct_chg')}%)" for s in sectors[:3]))
    except Exception as e:
        print(f"[2] 板块拉取失败（单榜兜底，可能空）：{e}")
        sectors = []

    # 3. 席位资金（第4层）：只对候选池内标的拉取（避免全市场逐只拉取）
    seat_fund = {}
    for r in rows:
        code = r["code"]
        try:
            raw = data_source.fetch_seat_and_fund(code, trade_date)
            seat_fund[code] = adapt_seat_fund(raw)
        except Exception as e:
            # 单只席位拉取失败：诚实缺省（空 dict，layer4 会因无净流入且无游资而过滤）
            seat_fund[code] = {"code": code, "dragon_tiger": "", "super_large_net": 0,
                               "large_net": 0, "margin_change": 0}
    print(f"[3] 席位资金（真实，候选池 {len(seat_fund)} 只）："
          f"有龙虎榜席位 {sum(1 for v in seat_fund.values() if v['dragon_tiger'])} 只")

    # 4. 催化+竞价（第5层）
    catalyst = {}
    for r in rows:
        code = r["code"]
        try:
            raw = data_source.fetch_catalyst_and_auction(code, trade_date)
            catalyst[code] = adapt_catalyst(raw)
        except Exception as e:
            catalyst[code] = {"code": code, "catalyst": "", "announcement": "",
                              "auction_pct": 0, "auction_seal": 0}
    print(f"[4] 催化+竞价（真实，候选池 {len(catalyst)} 只）")

    # 5. 策略 5 层流水线
    params = {
        "sentiment": sentiment,
        "sectors": sectors,
        "seat_fund": seat_fund,
        "catalyst": catalyst,
    }
    signals = strategy.generate_signals(rows, params)
    print(f"[5] 策略信号 {len(signals)} 条：" +
          ("、".join(f"{s.get('code')}{s.get('action')}({s.get('reason')})" for s in signals)
           if signals else "（空，今日无操作）"))

    # 6. 风控前置 → 执行
    portfolio = {"cash": INITIAL_CASH, "total_value": INITIAL_CASH, "positions": {}}
    orders, blocked = [], []
    for s in signals:
        if s.get("action") == "hold" or not s.get("code"):
            continue  # hold 信号不进入交易执行
        verdict = risk.check_risk(s, portfolio)
        if not verdict["allowed"]:
            blocked.append((s, verdict["reason"]))
            print(f"[6] 风控拦截 {s['code']} {s['action']}：{verdict['reason']}")
            continue
        order = execution.place_order(s, portfolio, seed=trade_date)
        orders.append(order)
        if order["status"] == "filled":
            cost = order["filled_price"] * order["qty"]
            if order["side"] == "buy":
                portfolio["cash"] -= cost
                pos = portfolio["positions"].setdefault(order["code"], {"qty": 0, "value": 0.0})
                pos["qty"] += order["qty"]
                pos["value"] += cost
            else:
                portfolio["cash"] += cost
                portfolio["positions"].pop(order["code"], None)
            print(f"[6] 成交 {order['order_id']}：{order['side']} {order['code']} x{order['qty']} @ {order['filled_price']}")
        else:
            print(f"[6] 下单被拒 {s['code']}：{order['reject_reason']}")

    # 7. 留痕
    if orders:
        log_path = review.write_order_log(orders, trade_date, out_dir=os.path.join(WORKDIR, "data"))
        print(f"[7] 订单留痕：{log_path}")

    # 8. 复盘
    portfolio["total_value"] = portfolio["cash"] + sum(p["value"] for p in portfolio["positions"].values())
    review_path = review.daily_review(trade_date, orders, portfolio, out_dir=os.path.join(WORKDIR, "data"))
    print(f"[8] 复盘摘要：{review_path}")

    print(f"===== 完成：信号 {len(signals)}，成交 {sum(1 for o in orders if o['status']=='filled')}，"
          f"风控拦截 {len(blocked)}，总资产 {portfolio['total_value']:.2f} =====")
    return {"date": trade_date, "status": "ok", "signals": len(signals),
            "orders": orders, "portfolio": portfolio}


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else _date.today().isoformat()
    run(d)
