# -*- coding: utf-8 -*-
"""
策略模块 —— 超级短线工作流 5 层 22 项决策版（v2.0）
按用户 5 层框架落地：
  第1层 情绪周期定温  → 情绪温度 0-100 + 开仓开关 gate_open（决定能不能打）
  第2层 主线识别      → 主线板块 top_sector（决定打哪条线）
  第3层 龙头识别      → 龙头候选 candidate_leaders（决定打哪只票）
  第4层 席位与资金    → 验证主力身份 verified_leaders（安全边际）
  第5层 催化与消息    → 时机确认 final_signals（拿先手）
接口契约（对接流程.md 3.1）：generate_signals(df_rows, params) → 信号列表，接口不变。
铁律：所有输入必须来自真实行情接口，严禁模拟数据。
"""
import statistics


# ============ 第1层 情绪周期定温（7项 → 情绪温度0-100 + gate_open） ============

def layer1_sentiment(sentiment: dict) -> dict:
    """第1层：情绪周期定温。sentiment 为真实情绪数据 dict：
    {
      "adv_decl_ratio": float,        # 涨跌家数比（>1.5亢奋, <0.5冰点）
      "limit_up": int,                # 涨停家数
      "limit_down": int,              # 跌停家数（>20 别伸手）
      "max_lianban": int,             # 连板高度（最高板）
      "lianban_promo_rate": float,    # 连板晋级率（1进2成功率, <30%打板胜率骤降）
      "yesterday_limit_premium": float, # 昨日涨停溢价（负=亏钱效应）
      "turnover_amount": float,       # 两市成交额（亿, 量能是情绪燃料）
      "market_turnover": float,       # 全市场换手率（%）
    }
    返回 {"temperature": 0-100, "gate_open": bool, "level": str}
    """
    # 逐项打分（每项0-14.3，7项共100）
    # 对齐对接流程契约字段名：up_count/down_count, lianban_jinlv, total_amount
    scores = []

    # 1. 涨跌家数比：>1.5 亢奋(高), <0.5 冰点(低)
    up_c = sentiment.get("up_count", sentiment.get("adv_decl_ratio", 1.0))
    dn_c = sentiment.get("down_count", None)
    if isinstance(up_c, (int, float)) and isinstance(dn_c, (int, float)) and dn_c:
        ratio = up_c / dn_c
    else:
        ratio = sentiment.get("adv_decl_ratio", 1.0)
        ratio = ratio if isinstance(ratio, (int, float)) else 1.0
    scores.append(max(0, min(14.3, (ratio - 0.5) / 1.0 * 14.3)))

    # 2. 涨停/跌停：跌停>20 别伸手（扣分）
    limit_up = sentiment.get("limit_up", 0)
    limit_down = sentiment.get("limit_down", 0)
    limit_up = limit_up if isinstance(limit_up, (int, float)) else 0
    limit_down = limit_down if isinstance(limit_down, (int, float)) else 0
    s2 = min(14.3, limit_up / 80 * 14.3) if limit_up else 0
    if limit_down > 20:
        s2 *= 0.3
    scores.append(s2)

    # 3. 连板高度：高度压缩=退潮预警（3板以下弱, 6板+亢奋）
    # 真实数据可能诚实缺省 None（新浪兜底无连板字段）→ 按0处理，绝不编造
    max_lb = sentiment.get("max_lianban", 0)
    max_lb = max_lb if isinstance(max_lb, (int, float)) else 0
    scores.append(min(14.3, max_lb / 6 * 14.3))

    # 4. 连板晋级率：<30% 打板胜率骤降
    promo = sentiment.get("lianban_jinlv", sentiment.get("lianban_promo_rate", 0.5))
    promo = promo if isinstance(promo, (int, float)) else 0.5
    scores.append(max(0, min(14.3, promo / 0.6 * 14.3)))

    # 5. 昨日涨停溢价：负=亏钱效应
    premium = sentiment.get("yesterday_limit_premium", 0.0)
    premium = premium if isinstance(premium, (int, float)) else 0.0
    scores.append(max(0, min(14.3, (premium + 3) / 6 * 14.3)))

    # 6. 两市成交额：缩量修复=弱修复（1万亿以下弱, 1.5万亿+亢奋）
    amount = sentiment.get("total_amount", sentiment.get("turnover_amount", 1.0))
    amount = amount if isinstance(amount, (int, float)) else 0.0
    scores.append(max(0, min(14.3, (amount - 8000) / 7000 * 14.3)))

    # 7. 全市场换手率
    mkt_turnover = sentiment.get("market_turnover", 1.0)
    mkt_turnover = mkt_turnover if isinstance(mkt_turnover, (int, float)) else 1.0
    scores.append(max(0, min(14.3, mkt_turnover / 3.0 * 14.3)))

    temperature = round(sum(scores), 1)

    # 开仓开关：温度>=40 才可开仓（冰点以下不开）
    gate_open = temperature >= 40.0

    if temperature >= 70:
        level = "亢奋"
    elif temperature >= 55:
        level = "活跃"
    elif temperature >= 40:
        level = "中性"
    elif temperature >= 25:
        level = "冰点"
    else:
        level = "极寒"

    return {"temperature": temperature, "gate_open": gate_open, "level": level}


# ============ 第2层 主线识别（4项 → top_sector 主线板块） ============

def layer2_mainline(sectors) -> list[dict]:
    """第2层：主线识别。sectors 为 fetch_sector_ranking 返回的 DataFrame（或 list[dict]），
    按对接流程契约字段名：sector_name/pct_chg/main_net_inflow(当日/5日/20日)/up_ratio/tier。
    main_net_inflow 可为 dict{1d,5d,20d} 或 float(当日)。tier 为梯队完整度(1-4)。
    按综合得分排序，返回前3条主线板块。
    """
    # 兼容 DataFrame：转 list[dict]（契约 fetch_sector_ranking 返回 DataFrame）
    if hasattr(sectors, "to_dict"):
        sectors = sectors.to_dict("records")

    def score(s):
        sc = 0
        sc += min(30, max(0, float(s.get("pct_chg", 0) or 0)))  # 涨幅 0-30
        # 主力净流入：契约 main_net_inflow 可为 dict{1d,5d,20d} 或 float
        inflow = s.get("main_net_inflow", 0)
        if isinstance(inflow, dict):
            f5 = float(inflow.get("5d", inflow.get("5日", 0)) or 0)
            f1 = float(inflow.get("1d", inflow.get("1日", 0)) or 0)
            f20 = float(inflow.get("20d", inflow.get("20日", 0)) or 0)
        else:
            # 兼容旧字段名 fund_flow_*，若只有单一值当当日
            f5 = float(s.get("fund_flow_5d", 0) or 0)
            f1 = float(s.get("fund_flow_1d", inflow or 0) or 0)
            f20 = float(s.get("fund_flow_20d", 0) or 0)
        # 5日净流入=持续主线（权重高）
        sc += 20 if f5 > 5 else (10 if f5 > 0 else 0)
        sc += 10 if f1 > 0 else 0
        sc += 5 if f20 > 0 else 0
        # 上涨家数占比 9/9=1.0（契约 up_ratio）
        sc += float(s.get("up_ratio", 0) or 0) * 20
        # 梯队完整度 4层=完整（契约 tier，兼容旧 ladder）
        tier = s.get("tier", s.get("ladder", 0))
        # 新浪兜底板块 tier 为字符串（"完整"/"有主线"/"弱"），数值映射
        if isinstance(tier, str):
            tier = {"完整": 4, "有主线": 3, "弱": 1}.get(tier, 0)
        try:
            tier_int = int(tier or 0)
        except (ValueError, TypeError):
            tier_int = 0
        sc += min(20, tier_int * 5)
        return sc

    ranked = sorted(sectors, key=score, reverse=True)
    return ranked[:3]


# ============ 第3层 龙头识别（5项 → candidate_leaders） ============

def layer3_leader(df_rows, params: dict | None = None) -> list[dict]:
    """第3层：龙头识别。df_rows 为标准化行情行（list[dict] 或 DataFrame）。
    字段：code/close/pct_chg/amount(元或亿)/turnover/float_mv 或 float_mcap(亿)。
    按连板梯队+封单/换手/成交额/流通市值综合评分，返回龙头候选。
    """
    params = params or {}
    leader_ratio = params.get("leader_top_ratio", 0.2)  # 取前20%为龙头候选

    # 兼容 DataFrame：转 list[dict]
    if hasattr(df_rows, "to_dict"):
        df_rows = df_rows.to_dict("records")

    def score(r):
        sc = 0
        # 涨幅（连板梯队：涨幅越高身位越高）
        pct = r.get("pct_chg", 0)
        sc += min(30, float(pct or 0))
        # 换手率 5-30% 最优
        turnover = float(r.get("turnover", 0) or 0)
        if 5 <= turnover <= 30:
            sc += 20
        elif 3 <= turnover <= 40:
            sc += 10
        # 成交额：20亿成交的龙头才有人接力（兼容元/亿）
        amount = float(r.get("amount", 0) or 0)
        if amount > 1e6:  # 元为单位
            amount = amount / 1e8
        if amount >= 20:
            sc += 25
        elif amount >= 10:
            sc += 15
        elif amount >= 5:
            sc += 8
        # 流通市值：小票好拉升（20-100亿最优，兼容 float_mv 元/float_mcap 亿）
        float_mv = float(r.get("float_mv", r.get("float_mcap", 0)) or 0)
        if float_mv > 1e6:  # 元为单位
            float_mv = float_mv / 1e8
        if 20 <= float_mv <= 100:
            sc += 15
        elif 100 < float_mv <= 300:
            sc += 8
        # 连板基因
        lb = float(r.get("lianban", r.get("lianban_tier", 0)) or 0)
        sc += min(10, int(lb) * 5)
        return sc

    ranked = sorted(df_rows, key=score, reverse=True)
    n = max(1, int(len(ranked) * leader_ratio)) if ranked else 0
    return ranked[:max(1, n)]


# ============ 第4层 席位与资金（3项 → verified_leaders） ============

def layer4_seat_fund(leaders: list[dict], seat_data: dict) -> list[dict]:
    """第4层：席位与资金验证。leaders 为第3层龙头候选，seat_data 为真实席位/资金数据
    （data_source.fetch_seat_and_fund 返回结构，兼容扁平旧字段）：
    {
      "code": {"dragon_tiger": list,   # 龙虎榜席位 list[{reason,...}]（炒股养家/作手新一等）
               "fund_flow": {super_net, large_net, ...},  # 超大单/大单净流入（元）
               "margin": {rzye_change, ...}}               # 融资余额变动
    }
    返回通过验证的龙头（游资/大单加持才保留）。
    """
    verified = []
    for lead in leaders:
        code = lead.get("code")
        sd = seat_data.get(code, {}) or {}
        # 兼容 data_source 嵌套结构 + 旧扁平结构
        dragon_raw = sd.get("dragon_tiger", "")
        # 龙虎榜 list：提取 reason 字符串；旧结构可能为 str
        if isinstance(dragon_raw, list):
            dragon = ";".join(
                (d.get("reason", "") if isinstance(d, dict) else str(d)) for d in dragon_raw
            )
        else:
            dragon = dragon_raw if isinstance(dragon_raw, str) else str(dragon_raw or "")
        # 资金流：新嵌套 fund_flow{super_net,large_net}；旧扁平 super_large_net/large_net
        ff = sd.get("fund_flow", {}) or {}
        super_net = ff.get("super_net", sd.get("super_large_net", 0)) or 0
        large_net = ff.get("large_net", sd.get("large_net", 0)) or 0
        # 融资余额变动：新嵌套 margin{rzye_change}；旧扁平 margin_change
        margin = sd.get("margin", {}) or {}
        margin_change = margin.get("rzye_change", sd.get("margin_change", 0)) or 0

        # 验证：知名游资席位 或 大单+超大单净流入（元转亿统一，>0 即净流入）
        is_hot_seat = any(k in dragon for k in ["炒股养家", "作手新一", "章盟主", "方新侠", "赵老哥", "著名刺客"])
        is_fund_inflow = (float(super_net or 0) + float(large_net or 0)) > 0
        if is_hot_seat or is_fund_inflow:
            lead = dict(lead)
            lead["verified"] = "游资席位" if is_hot_seat else "资金净流入"
            lead["seat_detail"] = dragon
            lead["super_large_net"] = float(super_net or 0)
            lead["large_net"] = float(large_net or 0)
            lead["margin_change"] = float(margin_change or 0)
            verified.append(lead)
    return verified


# ============ 第5层 催化与消息（3项 → final_signals） ============

def _item_title(x):
    """催化/公告条目转字符串：dict 取 title（东财公告结构），否则 str。"""
    if isinstance(x, dict):
        return x.get("title") or x.get("name") or str(x)
    return str(x)


def layer5_catalyst(verified: list[dict], catalyst_data: dict) -> list[dict]:
    """第5层：催化与消息时机确认。catalyst_data（data_source.fetch_catalyst_and_auction
    返回结构，兼容扁平旧字段）：
    {
      "code": {"catalyst": list,       # 题材催化（非空即有）
               "announcement": list,   # 公告面（非空即有）
               "auction": {pct_chg, bid1_vol, ...}}  # 竞价数据
    }
    高开+有催化才确认最终信号（拿先手）。
    """
    finals = []
    for v in verified:
        code = v.get("code")
        cd = catalyst_data.get(code, {}) or {}
        # 兼容嵌套 auction + 旧扁平 auction_pct/auction_seal
        auction = cd.get("auction", {}) or {}
        auction_pct = auction.get("pct_chg", cd.get("auction_pct", 0)) or 0
        auction_seal = auction.get("bid1_vol", cd.get("auction_seal", 0)) or 0
        # 催化/公告：新结构 list 非空即有；旧结构 str 非空即有
        catalyst = cd.get("catalyst", "")
        announcement = cd.get("announcement", "")
        if isinstance(catalyst, list):
            catalyst = ";".join(_item_title(x) for x in catalyst if x)
        if isinstance(announcement, list):
            announcement = ";".join(_item_title(x) for x in announcement if x)
        catalyst = catalyst if isinstance(catalyst, str) else str(catalyst or "")
        announcement = announcement if isinstance(announcement, str) else str(announcement or "")

        # 有催化/公告 且 竞价高开（0-5% 打板, >5% 谨慎追高, <0 低吸）
        if catalyst or announcement:
            if float(auction_pct or 0) >= 5:
                mode = "打板"
            elif float(auction_pct or 0) >= 0:
                mode = "低吸"
            else:
                mode = "低吸(低开)"
            v = dict(v)
            v["final_mode"] = mode
            v["catalyst"] = catalyst or announcement
            v["auction_pct"] = float(auction_pct or 0)
            v["auction_seal"] = float(auction_seal or 0)
            finals.append(v)
    return finals


# ============ 主入口：5层决策 ============

def generate_signals(df_rows: list[dict], params: dict | None = None) -> list[dict]:
    """接口契约见 对接流程.md 3.1。输入标准化行情行 + 5层真实数据，输出信号列表。
    params 可携带：
      sentiment: dict  → 第1层情绪数据（layer1_sentiment 输入）
      sectors: list    → 第2层板块数据
      seat_fund: dict  → 第4层席位资金数据
      catalyst: dict   → 第5层催化消息数据
    """
    params = params or {}
    sentiment = params.get("sentiment", {})
    sectors = params.get("sectors", [])
    seat_fund = params.get("seat_fund", {})
    catalyst = params.get("catalyst", {})

    # 第1层 情绪定温
    s1 = layer1_sentiment(sentiment)
    if not s1["gate_open"]:
        # 情绪冰点，不开仓
        return [{
            "action": "hold",
            "reason": f"情绪冰点（温度{s1['temperature']}，{s1['level']}），不开仓",
            "confidence": 0.3,
        }]

    # 第2层 主线识别
    top_sectors = layer2_mainline(sectors)

    # 第3层 龙头识别（在候选池内）
    leaders = layer3_leader(df_rows, params)

    # 第4层 席位与资金验证
    verified = layer4_seat_fund(leaders, seat_fund)

    # 第5层 催化与消息
    finals = layer5_catalyst(verified, catalyst)

    if not finals:
        return [{
            "action": "hold",
            "reason": f"情绪温度{s1['temperature']}({s1['level']})开仓，但无催化确认标的",
            "confidence": 0.4,
        }]

    signals = []
    # 兼容 DataFrame 取 date
    first_row = df_rows.iloc[0] if hasattr(df_rows, "iloc") and len(df_rows) > 0 else (df_rows[0] if df_rows else {})
    first_date = first_row.get("date", "") if hasattr(first_row, "get") else ""
    for f in finals:
        signals.append({
            "code": f.get("code"),
            "action": "buy",
            "price": f.get("close"),
            "mode": f.get("final_mode", "低吸"),
            "reason": (
                f"5层共振：情绪{s1['temperature']}({s1['level']}) | "
                f"主线{top_sectors[0].get('sector_name', top_sectors[0].get('sector', '—')) if top_sectors else '—'} | "
                f"龙头{f.get('code')} | 验证{f.get('verified')} | 催化{f.get('catalyst')}"
            ),
            "confidence": round(min(0.95, 0.5 + s1["temperature"] / 200), 2),
            "timestamp": f"{first_date} 09:30:00",
        })
    return signals
