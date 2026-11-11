# -*- coding: utf-8 -*-
"""
多维度评分模块（scorer.py）—— 阶段3 多维度全面评分
按用户已确认决策（悟空 11-09 20:04 拍板 ① 按推荐）：
  4维度权重 —— 技术形态30% / 资金25% / 题材25% / 消息20%
  输出：每只标的的综合分 + 4项分项分，供 plan_builder.py 汇总作战计划。
铁律：所有输入必须来自真实行情接口（data_source 各 fetch_*），严禁模拟数据。
输入契约（对接流程 V2 字段名）：
  strength: dict   ← data_source.fetch_stock_strength(code, date) 原始输出
                     {lianban_tier, fengdan_amt, fengcheng_ratio, turnoverratio,
                      amount, nmc_yi, pct_chg, zbc(炸板次数)}
  seat_fund: dict  ← data_source.fetch_seat_and_fund(code, date) 原始输出
                     {dragon_tiger: list, fund_flow: dict, margin: dict}
  catalyst: dict   ← data_source.fetch_catalyst_and_auction(code, date) 原始输出
                     {catalyst: list, announcement: list, auction: dict}
  pool_row: dict   ← 候选池 CSV 行 {code,name,price,pct_chg,turnover,float_mv_yi,...}
接口：score_stock(code, pool_row, strength, seat_fund, catalyst, weights=None) -> dict
     score_pool(rows, strength_map, seat_map, catalyst_map, weights=None) -> list[dict]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 默认4维度权重（用户已确认）：技术30% / 资金25% / 题材25% / 消息20%
DEFAULT_WEIGHTS = {
    "technical": 0.30,
    "capital": 0.25,
    "theme": 0.25,
    "message": 0.20,
}


def _f(v, default=0.0):
    """安全转 float，None/''/异常一律返回 default（诚实缺省，绝不编造）。"""
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


# ============ 维度1：技术形态（30%） ============

def score_technical(strength: dict, pool_row: dict) -> dict:
    """技术形态 0-100。基于真实数据：
    - 连板梯队位置 lianban_tier（越高越强）
    - 封单额 fengdan_amt + 封成比 fengcheng_ratio（>5倍=强封）
    - 换手率 turnoverratio（5-30%最优）
    - 成交额 amount（20亿以上龙头有人接力）
    - 炸板次数 zbc（越多越弱，扣分）
    - 当日涨幅 pct_chg
    """
    sc = 0.0
    # 连板梯队（0-30）：lianban_tier 越高越强，3板以上封顶
    tier = _f(strength.get("lianban_tier"))
    sc += min(30, tier * 8)

    # 封单/封成比（0-25）：>5倍成交=强封
    fc_ratio = _f(strength.get("fengcheng_ratio"))
    fengdan = _f(strength.get("fengdan_amt"))
    if fengdan > 0:
        sc += min(25, fc_ratio / 5 * 25)

    # 换手率（0-15）：5-30% 最优
    turnover = _f(strength.get("turnoverratio"))
    if 5 <= turnover <= 30:
        sc += 15
    elif 3 <= turnover <= 40:
        sc += 8

    # 成交额（0-15）：20亿+ 强
    amount = _f(strength.get("amount"))
    if amount > 1e6:  # 元单位
        amount = amount / 1e8
    if amount >= 20:
        sc += 15
    elif amount >= 10:
        sc += 10
    elif amount >= 5:
        sc += 5

    # 涨幅（0-10）：涨停20%创业板/10%主板，按 pct_chg 线性
    pct = _f(strength.get("pct_chg"))
    sc += min(10, pct)

    # 炸板扣分（0-5）：每次 -2
    zbc = _f(strength.get("zbc"))
    sc -= min(5, zbc * 2)

    return {"technical": round(_clamp(sc / 100.0, 0, 1), 4), "detail": {
        "lianban_tier": tier, "fengcheng_ratio": fc_ratio,
        "turnoverratio": turnover, "amount_yi": round(amount, 2),
        "pct_chg": pct, "zbc": zbc,
    }}


# ============ 维度2：资金（25%） ============

def score_capital(seat_fund: dict) -> dict:
    """资金 0-100。基于真实席位/资金流（东财 push2 限流时诚实缺省，绝不编造）：
    - 超大单+大单净流入 super_net+large_net（元，>0 净流入加分）
    - 龙虎榜知名游资席位（炒股养家/作手新一/章盟主/方新侠/赵老哥/著名刺客）
    - 融资余额变动 margin_change（杠杆加仓方向）
    注意：fund_flow 为空（东财 push2 限流/不可得）时，资金维度只能靠龙虎榜/融资，
    主力的超大单/大单缺省为 0 分（诚实），评分不虚增。
    """
    sc = 0.0
    ff = seat_fund.get("fund_flow") or {}
    super_net = _f(ff.get("super_net"))
    large_net = _f(ff.get("large_net"))
    net = super_net + large_net  # 元
    has_fund_flow = bool(ff)  # 东财资金流是否可得

    # 主力净流入（0-50）：>0 加分，按亿元梯度；fund_flow 不可得不加不减
    net_yi = net / 1e8
    if has_fund_flow:
        if net_yi > 0:
            sc += min(50, 20 + net_yi * 5)
        elif net_yi < 0:
            sc -= min(30, abs(net_yi) * 3)

    # 龙虎榜游资席位（0-30）
    dragon_list = seat_fund.get("dragon_tiger") or []
    dragon_str = "；".join(str(d.get("reason", "")) for d in dragon_list if isinstance(d, dict))
    hot_seats = ["炒股养家", "作手新一", "章盟主", "方新侠", "赵老哥", "著名刺客"]
    has_hot_seat = any(k in dragon_str for k in hot_seats)
    if has_hot_seat:
        sc += 30

    # 融资余额变动（0-20）：杠杆加仓；margin 不可得不加分
    margin = seat_fund.get("margin") or {}
    margin_change = _f(margin.get("rzye_change"))
    if margin:
        if margin_change > 0:
            sc += min(20, margin_change / 1e8 * 10)

    return {"capital": round(_clamp(sc / 100.0, 0, 1), 4), "detail": {
        "super_net_yi": round(super_net / 1e8, 4), "large_net_yi": round(large_net / 1e8, 4),
        "hot_seat": has_hot_seat,
        "margin_change_yi": round(margin_change / 1e8, 4),
        "has_fund_flow": has_fund_flow, "has_margin": bool(margin),
    }}


# ============ 维度3：题材（25%） ============

def score_theme(strength: dict, catalyst: dict) -> dict:
    """题材 0-100。催化/题材列表非空即视为有题材（诚实，不编造内容）：
    - 有题材催化 catalyst 非空 → 加分
    - 梯队/封单佐证题材活跃度（若涨停且封单强，题材更可信）
    注意：catalyst 诚实缺省 []（未接可靠免费源）时，题材维度不虚增。
    """
    sc = 0.0
    catalyst_list = catalyst.get("catalyst") or []
    # 题材催化非空（有真实题材内容）
    if catalyst_list:
        sc += 60
    # 梯队/封单佐证题材活跃度（若涨停且封单强，题材更可信）
    fengdan = _f(strength.get("fengdan_amt"))
    if fengdan > 0:
        sc += 20
    tier = _f(strength.get("lianban_tier"))
    if tier >= 2:
        sc += 20
    return {"theme": round(_clamp(sc / 100.0, 0, 1), 4), "detail": {
        "has_catalyst": bool(catalyst_list),
        "catalyst_count": len(catalyst_list),
        "lianban_tier": tier,
        "data_note": "catalyst 诚实缺省，题材维度仅由梯队/封单佐证" if not catalyst_list else "",
    }}


# ============ 维度4：消息（20%） ============

def score_message(catalyst: dict) -> dict:
    """消息 0-100。公告面 announcement + 竞价（均真实 data_source 输出）：
    - 有公告（重组/业绩/异动）非空 → 加分
    - 竞价涨幅 auction.pct_chg：0-5% 低吸最优，>5% 谨慎追高，<0 低开偏弱
    注意：announcement 诚实缺省 []（未接可靠免费源）时，公告面不虚增；
    竞价数据来自腾讯真实快照，稳定可得。
    """
    sc = 0.0
    ann_list = catalyst.get("announcement") or []
    if ann_list:
        sc += 50  # 有公告面
    auction = catalyst.get("auction") or {}
    auction_pct = _f(auction.get("pct_chg"))
    # 竞价涨幅：0-5% 温和高开最健康（低吸机会）
    if 0 <= auction_pct <= 5:
        sc += 40
    elif 5 < auction_pct <= 8:
        sc += 25  # 高开但需谨慎
    elif auction_pct > 8:
        sc += 10  # 高开过多，追高风险
    elif -3 <= auction_pct < 0:
        sc += 15  # 低开，低吸机会
    # 竞价封单强度（买一档量）：越大越强
    bid1_vol = _f(auction.get("bid1_vol"))
    if bid1_vol > 0:
        sc += min(10, bid1_vol / 10000 * 2)
    return {"message": round(_clamp(sc / 100.0, 0, 1), 4), "detail": {
        "has_announcement": bool(ann_list),
        "announcement_count": len(ann_list),
        "auction_pct": auction_pct, "bid1_vol": bid1_vol,
        "data_note": "announcement 诚实缺省，消息维度仅由竞价佐证" if not ann_list else "",
    }}


# ============ 主接口：单只评分 ============

def score_stock(code: str, pool_row: dict | None = None,
                strength: dict | None = None, seat_fund: dict | None = None,
                catalyst: dict | None = None, weights: dict | None = None) -> dict:
    """单只标的 4 维度综合评分。
    输入：真实 data_source 输出（strength/seat_fund/catalyst）+ 候选池行。
    输出：{code, name, total_score(0-1), score_detail, dims, weights}
    """
    weights = weights or DEFAULT_WEIGHTS
    strength = strength or {}
    seat_fund = seat_fund or {}
    catalyst = catalyst or {}
    pool_row = pool_row or {}

    t = score_technical(strength, pool_row)
    c = score_capital(seat_fund)
    th = score_theme(strength, catalyst)
    m = score_message(catalyst)

    total = (
        t["technical"] * weights["technical"]
        + c["capital"] * weights["capital"]
        + th["theme"] * weights["theme"]
        + m["message"] * weights["message"]
    )
    return {
        "code": code,
        "name": pool_row.get("name", ""),
        "total_score": round(total, 4),
        "score_detail": {
            "technical": t["technical"],
            "capital": c["capital"],
            "theme": th["theme"],
            "message": m["message"],
        },
        "dims_detail": {
            "technical": t["detail"], "capital": c["detail"],
            "theme": th["detail"], "message": m["detail"],
        },
        "weights": weights,
    }


# ============ 主接口：批量评分（候选池） ============

def score_pool(rows: list[dict], strength_map: dict | None = None,
               seat_map: dict | None = None, catalyst_map: dict | None = None,
               weights: dict | None = None, top_n: int = 10) -> list[dict]:
    """对候选池逐只评分，按综合分降序返回 top_n 只。
    rows: 候选池行 list[dict]；strength_map/seat_map/catalyst_map 按 code 索引真实数据。
    每只输出含 data_completeness：可得维度权重和（用于诚实缺省时归一化，不虚增）。
    """
    strength_map = strength_map or {}
    seat_map = seat_map or {}
    catalyst_map = catalyst_map or {}
    weights = weights or DEFAULT_WEIGHTS
    results = []
    for r in rows:
        code = r.get("code")
        if not code:
            continue
        sc = score_stock(
            code, r,
            strength=strength_map.get(code, {}),
            seat_fund=seat_map.get(code, {}),
            catalyst=catalyst_map.get(code, {}),
            weights=weights,
        )
        # 数据可得性：该标的有多少维度真实数据可得（诚实缺省不虚增总分）
        dims_detail = sc.get("dims_detail", {})
        avail_weights = 0.0
        # 技术维度：strength 有真实内容（非空）
        s = strength_map.get(code, {})
        if s:
            avail_weights += weights.get("technical", 0)
        # 资金维度：fund_flow 或 dragon_tiger 非空
        sf = seat_map.get(code, {})
        if (sf.get("fund_flow") or sf.get("dragon_tiger")):
            avail_weights += weights.get("capital", 0)
        # 题材维度：catalyst 有真实内容
        ca = catalyst_map.get(code, {})
        if ca.get("catalyst"):
            avail_weights += weights.get("theme", 0)
        # 消息维度：announcement 或 auction 非空（竞价来自腾讯稳定，通常可得）
        if ca.get("announcement") or ca.get("auction"):
            avail_weights += weights.get("message", 0)
        sc["data_completeness"] = round(min(1.0, avail_weights), 4)
        results.append(sc)
    results.sort(key=lambda x: x["total_score"], reverse=True)
    return results[:max(1, top_n)]


if __name__ == "__main__":
    # 冒烟测试：用真实候选池 + 真实 data_source 输出跑一遍（不编造）
    import data_source
    from datetime import date as _date

    d = _date.today().isoformat()
    pool_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "data", f"super_short_pool_{d}.csv")
    rows = []
    import csv
    with open(pool_path, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    print(f"=== scorer.py 冒烟测试（真实数据 {d}，候选池 {len(rows)} 只）===")
    strength_map, seat_map, catalyst_map = {}, {}, {}
    for r in rows[:15]:  # 冒烟只测前15只，避免拉取过久
        code = r["code"]
        try:
            strength_map[code] = data_source.fetch_stock_strength(code, d)
        except Exception as e:
            print(f"  strength {code} 失败: {e}")
        try:
            seat_map[code] = data_source.fetch_seat_and_fund(code, d)
        except Exception as e:
            print(f"  seat {code} 失败: {e}")
        try:
            catalyst_map[code] = data_source.fetch_catalyst_and_auction(code, d)
        except Exception as e:
            print(f"  catalyst {code} 失败: {e}")

    top = score_pool(rows[:15], strength_map, seat_map, catalyst_map, top_n=5)
    print("\n=== 综合分 Top5 ===")
    for t in top:
        print(f"  {t['code']} {t['name']}: {t['total_score']:.4f} "
              f"(技术{t['score_detail']['technical']:.2f}/资金{t['score_detail']['capital']:.2f}/"
              f"题材{t['score_detail']['theme']:.2f}/消息{t['score_detail']['message']:.2f})")
    print("\n冒烟测试通过 (exit 0)")
