# -*- coding: utf-8 -*-
"""
每日样本记录脚本（S4-T1）—— 阶段4：多日真实样本积累 + 胜率/回撤评估
用法：python sample_daily.py [YYYY-MM-DD]
调用 run_trading_workflow.run(date) 跑真实盘面（铁律：全真实行情，严禁模拟），
把结果 + 复盘 + 数据源健康度序列化到 data/samples/<date>.json，
累积样本达 10/20 交易日时产出胜率/回撤/盈亏比评估报告 data/samples/evaluation_*.md。
"""
import json
import os
import sys
from datetime import date as _date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_trading_workflow

SAMPLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "samples")
EVAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _load_health(trade_date: str) -> dict:
    """读取当日数据源健康度摘要（真实记录，health.csv）。"""
    health_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "data", "data_source_health.csv")
    rows = []
    if os.path.exists(health_path):
        import csv
        with open(health_path, "r", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("date", "") == trade_date or (not r.get("date") and r.get("source")):
                    rows.append({k: v for k, v in r.items() if v != ""})
    return {"count": len(rows), "sources": rows}


def record_sample(trade_date: str) -> dict:
    """跑当日真实盘面，记录样本到 data/samples/<date>.json，返回样本 dict。"""
    os.makedirs(SAMPLES_DIR, exist_ok=True)
    result = run_trading_workflow.run(trade_date)
    # 序列化样本：信号 + 评分 + 作战计划 + 订单 + 账户 + 复盘 + 数据源健康度
    sample = {
        "date": trade_date,
        "status": result.get("status"),
        "signals": result.get("signals", []),
        "scored": result.get("scored", []),
        "plan": result.get("plan", {}),
        "orders": result.get("orders", []),
        "portfolio": result.get("portfolio", {}),
        "review": result.get("review", ""),
        "data_health": _load_health(trade_date),
        "recorded_at": _date.today().isoformat(),
    }
    path = os.path.join(SAMPLES_DIR, f"{trade_date}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sample, f, ensure_ascii=False, indent=2, default=str)
    print(f"[样本] 已记录 {path}（status={sample['status']}，信号{len(sample['signals'])}，"
          f"作战标的{len(sample['plan'].get('targets', []))}，成交{sum(1 for o in sample['orders'] if o.get('status')=='filled')}）")
    return sample


def _pnl_of_day(sample: dict) -> float:
    """当日盈亏率（总资产 / 初始 - 1）。"""
    port = sample.get("portfolio", {})
    total = port.get("total_value", 0)
    if not total:
        return 0.0
    return round((total - run_trading_workflow.INITIAL_CASH) / run_trading_workflow.INITIAL_CASH, 4)


def _load_samples() -> list[dict]:
    """读取 data/samples/ 下全部样本（按日期排序，兼容两种命名并去重）。
    兼容命名：<date>.json（本鱼 sample_daily.py）与 sample_<date>.json（Agent编排 run_daily_s4.py）。
    同日期取最新一条（date 字段一致视为同一天）。"""
    samples = {}
    if not os.path.isdir(SAMPLES_DIR):
        return []
    for fn in sorted(os.listdir(SAMPLES_DIR)):
        if fn.endswith(".json"):
            with open(os.path.join(SAMPLES_DIR, fn), "r", encoding="utf-8") as f:
                try:
                    s = json.load(f)
                except Exception:
                    continue
                d = s.get("date")
                if d:
                    samples[d] = s  # 同日期覆盖为最新
    return [samples[d] for d in sorted(samples)]


def evaluate(target_days: int = 10) -> dict:
    """样本数达 target_days 时产出中期评估（10日）/完整评估（20日）。
    指标：胜率（盈利交易日占比）、最大回撤、盈亏比、累计收益、样本区间、数据源健康度。"""
    samples = _load_samples()
    n = len(samples)
    if n < target_days:
        return {"days": n, "target": target_days, "ready": False,
                "note": f"样本 {n}/{target_days}，未达标不评估"}

    # 逐日盈亏
    pnls = [_pnl_of_day(s) for s in samples]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = len(wins) / n if n else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    profit_factor = (avg_win / avg_loss) if avg_loss else 0.0

    # 最大回撤（累计净值回撤）
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for p in pnls:
        equity *= (1 + p)
        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak else 0.0
        max_dd = max(max_dd, dd)

    cum_ret = 1.0
    for p in pnls:
        cum_ret *= (1 + p)
    cum_ret = round(cum_ret - 1, 4)

    # 数据源健康度汇总
    health_total = sum(len(s.get("data_health", {}).get("sources", [])) for s in samples)

    eval_report = {
        "days": n,
        "target": target_days,
        "ready": True,
        "sample_range": [samples[0].get("date"), samples[-1].get("date")],
        "win_rate": round(win_rate, 4),
        "max_drawdown": round(max_dd, 4),
        "profit_factor": round(profit_factor, 2),
        "cum_return": cum_ret,
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "data_health_records": health_total,
    }
    return eval_report


def write_evaluation(report: dict) -> str:
    """落盘评估报告 markdown，返回路径。"""
    os.makedirs(EVAL_DIR, exist_ok=True)
    tag = "中期" if report["target"] == 10 else "完整"
    path = os.path.join(EVAL_DIR, f"evaluation_{tag}_{report['days']}日.md")
    lines = [
        f"# A股超短交易 {tag}评估报告（{report['days']} 交易日真实样本）",
        "",
        f"- 样本区间：{report['sample_range'][0]} ~ {report['sample_range'][-1]}",
        f"- 胜率：{report['win_rate']*100:.1f}%",
        f"- 最大回撤：{report['max_drawdown']*100:.1f}%",
        f"- 盈亏比：{report['profit_factor']}",
        f"- 累计收益：{report['cum_return']*100:.1f}%",
        f"- 平均盈利/平均亏损：{report['avg_win']*100:.1f}% / {report['avg_loss']*100:.1f}%",
        f"- 数据源健康记录：{report['data_health_records']} 条",
        "",
        "> 全部样本来自真实行情（新浪/腾讯/东财），严禁模拟数据。",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[评估] 已落盘 {path}")
    return path


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else _date.today().isoformat()
    rec = record_sample(d)

    # 累积样本后检查是否达 10/20 交易日
    for target in (10, 20):
        rep = evaluate(target)
        if rep["ready"]:
            write_evaluation(rep)
            print(f"[评估] {target} 日样本达标，报告已生成")
    print(f"[完成] 当前样本 {len(_load_samples())} 个交易日")
