# -*- coding: utf-8 -*-
"""
复盘模块（阶段3跑通用，简版）
读取当日订单日志与账户变化，输出复盘摘要 CSV + 控制台报告。
完整复盘模板由 文档转换金融 另行产出，本模块只保证数据落盘可追溯。

T2 扩展（2026-11-11，悟空派单）：
  - 逐笔盈亏归因：每笔成交 → 匹配评分（4维度）+ 信号 → 归因到主导维度/信号类型
  - 信号质量回测：按维度/信号类型分组算胜率，指出最准维度
  铁律：全真实数据（样本 JSON 由真实行情跑出），严禁模拟。
"""
import csv
import os
import json
from collections import defaultdict

# 4 维度中文名（与 scorer 权重键一致）
DIM_LABELS = {"technical": "技术形态", "capital": "资金", "theme": "题材", "message": "消息"}


def write_order_log(orders: list[dict], date: str, out_dir: str = "data") -> str:
    """成交回报落盘 order_log.csv（追加写）。"""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "order_log.csv")
    exists = os.path.exists(path)
    fields = ["date", "order_id", "code", "side", "qty", "status", "filled_price", "reject_reason"]
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            w.writeheader()
        for o in orders:
            w.writerow({
                "date": date,
                "order_id": o.get("order_id") or "",
                "code": o["code"],
                "side": o["side"],
                "qty": o["qty"],
                "status": o["status"],
                "filled_price": o.get("filled_price") or "",
                "reject_reason": o.get("reject_reason") or "",
            })
    return path


def daily_review(date: str, orders: list[dict], portfolio: dict, out_dir: str = "data") -> str:
    """生成当日复盘摘要，落盘 review_{date}.csv，返回路径。"""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"review_{date}.csv")
    filled = [o for o in orders if o["status"] == "filled"]
    rejected = [o for o in orders if o["status"] == "rejected"]
    rows = [
        ("交易日期", date),
        ("信号总数", len(orders)),
        ("成交笔数", len(filled)),
        ("拦截/失败笔数", len(rejected)),
        ("期末现金", round(portfolio.get("cash", 0), 2)),
        ("持仓数", len(portfolio.get("positions", {}))),
        ("总资产", round(portfolio.get("total_value", 0), 2)),
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["项目", "值"])
        w.writerows(rows)
    return path


# ============ T2：逐笔盈亏归因 + 信号质量回测 ============

def _dominant_dim(score_detail: dict) -> str:
    """取4维中得分最高的维度作为主导维度。score_detail 形如 {technical, capital, theme, message}。"""
    if not score_detail:
        return "未评分"
    best, best_val = "未评分", -1.0
    for k in ("technical", "capital", "theme", "message"):
        v = score_detail.get(k)
        if isinstance(v, (int, float)) and v > best_val:
            best, best_val = k, v
    return best


def _signal_type(sig: dict) -> str:
    """信号类型：优先 mode（低吸/追涨），否则从 reason 推断，缺省 '未知'。"""
    if not sig:
        return "未知"
    mode = sig.get("mode")
    if mode:
        return str(mode)
    reason = sig.get("reason", "")
    if "低吸" in reason:
        return "低吸"
    if "追涨" in reason or "突破" in reason:
        return "追涨"
    if "打板" in reason or "涨停" in reason:
        return "打板"
    return "未知"


def attribute_trades(sample: dict) -> list[dict]:
    """逐笔盈亏归因：把样本中的成交订单逐笔归因到主导维度 + 信号类型 + 盈亏。

    输入 sample（真实样本 JSON，含 orders/scored/signals），
    输出 [{date, code, side, qty, filled_price, dominant_dim, dim_label, signal_type,
           total_score, reason, pnl, pnl_pct, realized}]
    - pnl：配对同 code 的卖出与买入，按「卖出价-买入价)×qty」算盈亏；无卖出配对则 None（诚实缺省）。
    - realized：True 表示已配对算盈，False 表示未平仓。
    铁律：只消费真实样本（由真实行情跑出），无模拟。
    """
    date = sample.get("date", "")
    orders = sample.get("orders", []) or []
    scored_map = {s.get("code"): s for s in (sample.get("scored", []) or []) if s.get("code")}
    sig_map = {s.get("code"): s for s in (sample.get("signals", []) or []) if s.get("code")}

    # 配对同 code 的 buy/sell（按 code 分组，先买后卖配对）
    buys: dict[str, list[dict]] = defaultdict(list)
    sells: dict[str, list[dict]] = defaultdict(list)
    for o in orders:
        if o.get("status") != "filled":
            continue
        (buys if o.get("side") == "buy" else sells).setdefault(o.get("code", ""), []).append(o)

    out = []
    for o in orders:
        if o.get("status") != "filled":
            continue
        code = o.get("code", "")
        sc = scored_map.get(code, {})
        sig = sig_map.get(code, {})
        dim = _dominant_dim(sc.get("score_detail", {}))

        # 盈亏配对：买入订单配卖出（同 code），按 qty 逐笔配对
        pnl, pnl_pct, realized = None, None, False
        if o.get("side") == "sell":
            # 卖出：找对应买入（用卖出价 vs 买入价）
            b = buys.get(code, [])
            if b:
                buy_price = b[0].get("filled_price", 0)
                sell_price = o.get("filled_price", 0)
                qty = o.get("qty", 0)
                if buy_price > 0 and sell_price > 0 and qty > 0:
                    pnl = round((sell_price - buy_price) * qty, 2)
                    pnl_pct = round((sell_price - buy_price) / buy_price, 4)
                    realized = True
        elif o.get("side") == "buy":
            s = sells.get(code, [])
            if s:
                sell_price = s[0].get("filled_price", 0)
                buy_price = o.get("filled_price", 0)
                qty = o.get("qty", 0)
                if buy_price > 0 and sell_price > 0 and qty > 0:
                    pnl = round((sell_price - buy_price) * qty, 2)
                    pnl_pct = round((sell_price - buy_price) / buy_price, 4)
                    realized = True

        out.append({
            "date": date,
            "code": code,
            "side": o.get("side", ""),
            "qty": o.get("qty", 0),
            "filled_price": o.get("filled_price", 0),
            "dominant_dim": dim,
            "dim_label": DIM_LABELS.get(dim, dim if dim != "未评分" else "未评分"),
            "signal_type": _signal_type(sig),
            "total_score": sc.get("total_score", 0),
            "reason": sig.get("reason", ""),
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "realized": realized,
        })
    return out


def _group_win_rate(rows: list[dict], key_fn) -> dict:
    """按 key_fn 分组统计胜率。胜率基于「已实现盈亏订单（realized=True）中 pnl>0 的比例」。

    当前样本为空仓（无成交），本函数在无数据时返回 {group: {"count":0, "win":0, "win_rate":None, "realized":0}}，
    诚实不虚报。待有真实成交且平仓（realized=True）样本后计算真实胜率。
    铁律：只统计真实样本的已实现盈亏，绝不虚增。
    """
    groups = defaultdict(lambda: {"count": 0, "win": 0, "win_rate": None, "realized": 0})
    for r in rows:
        g = key_fn(r)
        groups[g]["count"] += 1
        if r.get("realized") and r.get("pnl") is not None:
            groups[g]["realized"] += 1
            if r["pnl"] > 0:
                groups[g]["win"] += 1
    for g in groups:
        if groups[g]["realized"] > 0:
            groups[g]["win_rate"] = round(groups[g]["win"] / groups[g]["realized"], 4)
        else:
            groups[g]["win_rate"] = None  # 无已实现盈亏样本，诚实缺省
    return dict(groups)


def signal_quality_backtest(samples: list[dict]) -> dict:
    """信号质量回测：按维度/信号类型分组，统计成交笔数与（有平仓样本时的）胜率。

    输入 samples（真实样本列表），输出：
    {
      "by_dim": {dim_label: {"count": n, "win": m, "win_rate": r|None}},
      "by_signal_type": {signal_type: {...}},
      "best_dim": dim_label|None,   # 胜率最高（有数据时）
      "sample_count": len(samples),
      "trade_count": 总成交笔数,
    }
    铁律：只消费真实样本。样本未达平仓数据时不产出伪胜率（win_rate=None 诚实缺省）。
    """
    all_rows = []
    for s in samples:
        all_rows.extend(attribute_trades(s))

    by_dim = _group_win_rate(all_rows, lambda r: r.get("dim_label", "未评分"))
    by_sig = _group_win_rate(all_rows, lambda r: r.get("signal_type", "未知"))

    # 找最准维度：有胜率数据的维度里取最高；无数据诚实 None
    best_dim = None
    best_rate = -1.0
    for dim, st in by_dim.items():
        if st["win_rate"] is not None and st["win_rate"] > best_rate:
            best_dim, best_rate = dim, st["win_rate"]

    return {
        "by_dim": by_dim,
        "by_signal_type": by_sig,
        "best_dim": best_dim,
        "sample_count": len(samples),
        "trade_count": len(all_rows),
    }


def deep_review(samples: list[dict], out_dir: str = "data", out_name: str = "review_deep") -> str:
    """深度复盘：逐笔归因 + 信号质量回测，落盘 JSON + 控制台报告，返回 JSON 路径。"""
    os.makedirs(out_dir, exist_ok=True)
    all_rows = []
    for s in samples:
        all_rows.extend(attribute_trades(s))
    bt = signal_quality_backtest(samples)

    result = {
        "generated": True,
        "mode": "deep_review_t2",
        "sample_count": len(samples),
        "trade_count": len(all_rows),
        "attribution": all_rows,
        "quality": bt,
    }
    path = os.path.join(out_dir, f"{out_name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"===== 深度复盘（T2）=====")
    print(f"样本数 {len(samples)}，成交笔数 {len(all_rows)}")
    if all_rows:
        print(f"逐笔归因：")
        for r in all_rows:
            pnl_str = (f"{r['pnl']:.2f} ({r['pnl_pct']:.2%})" if r["realized"] else "未平仓")
            print(f"  {r['date']} {r['code']} {r['side']} x{r['qty']} @ {r['filled_price']} "
                  f"| 主导维度 {r['dim_label']} | 信号类型 {r['signal_type']} | 总分 {r['total_score']} | 盈亏 {pnl_str}")
    else:
        print(f"无成交样本（当前诚实空仓），逐笔归因空——待有真实成交后归因。")
    print(f"信号质量（按维度）：")
    for dim, st in bt["by_dim"].items():
        print(f"  {dim}: 成交{st['count']}笔/已实现{st['realized']}笔，胜率 {st['win_rate'] if st['win_rate'] is not None else '待平仓样本'}")
    print(f"信号质量（按信号类型）：")
    for sig, st in bt["by_signal_type"].items():
        print(f"  {sig}: 成交{st['count']}笔/已实现{st['realized']}笔，胜率 {st['win_rate'] if st['win_rate'] is not None else '待平仓样本'}")
    print(f"最准维度（有数据时）：{bt['best_dim'] or '待样本达标'}")
    print(f"落盘：{path}")
    return path


if __name__ == "__main__":
    import sys
    samples_dir = sys.argv[1] if len(sys.argv) > 1 else "data/samples"
    # 读取全部真实样本（兼容 <date>.json 与 sample_<date>.json）
    samples = []
    if os.path.isdir(samples_dir):
        for fn in sorted(os.listdir(samples_dir)):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(samples_dir, fn), "r", encoding="utf-8") as f:
                        samples.append(json.load(f))
                except Exception:
                    continue
    deep_review(samples, out_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
