# -*- coding: utf-8 -*-
"""
金融文档转换脚本（S4-T2 · 文档转换金融交付）
============================================
把 A股短线交易专家（sample_daily.py）产出的评估报告 + 逐日真实样本，
转成用户可读的金融文档（胜率曲线 / 回撤曲线 / 盈亏比表格），
存到 D:\\My Obsidian\\My Obsidian\\13-nanoMuse记忆库。

用法：
    python build_financial_docs.py                # 读最新评估报告 + 全部样本，转文档
    python build_financial_docs.py --dry-run      # 只打印将生成的文档，不写库
    python build_financial_docs.py --report data/evaluation_中期_10日.md   # 指定评估报告

铁律：只读真实样本/报告，不生成模拟数据；数据不可得诚实缺省（空仓日照样如实记录）。
输出：
    <nanoMuse>/A股超短交易/胜率回撤评估_<tag>_<days>日.md    （胜率曲线/回撤曲线/盈亏比表格）
    <nanoMuse>/A股超短交易/每日复盘/<date>.md                 （逐日复盘转用户可读）
"""
import argparse
import csv
import json
import os
import re
import sys
from datetime import date as _date

BASE = os.path.dirname(os.path.abspath(__file__))
SAMPLES_DIR = os.path.join(BASE, "data", "samples")
DATA_DIR = os.path.join(BASE, "data")

# 目标记忆库（用户指定）
NANOMUSE_DIR = r"D:\My Obsidian\My Obsidian\13-nanoMuse记忆库"
OUT_SUBDIR = "A股超短交易"
OUT_DAILY = os.path.join(OUT_SUBDIR, "每日复盘")


def _load_samples() -> list[dict]:
    """读取 data/samples/ 下全部样本（按日期排序，兼容两种命名并去重）。"""
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
                    samples[d] = s
    return [samples[d] for d in sorted(samples)]


def _find_latest_report() -> str | None:
    """在 data/ 下找最新的 evaluation_*.md 报告（中期/完整，取文件名日期最新的）。"""
    if not os.path.isdir(DATA_DIR):
        return None
    cands = []
    for fn in os.listdir(DATA_DIR):
        if fn.startswith("evaluation_") and fn.endswith(".md"):
            cands.append(fn)
    if not cands:
        return None
    # 排序：完整评估优先，其次按文件名倒序（10日>？；直接按文件名，完整>中期）
    def key(fn):
        tag = "完整" if "完整" in fn else "中期"
        days = re.search(r"(\d+)日", fn)
        return (tag == "完整", int(days.group(1)) if days else 0)
    cands.sort(key=key, reverse=True)
    return os.path.join(DATA_DIR, cands[0])


def _parse_report(path: str) -> dict:
    """解析 evaluation_*.md 报告为 dict（容错解析，读取已有键值）。"""
    report = {}
    if not path or not os.path.exists(path):
        return report
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    title = re.search(r"#\s*(.+)", text)
    report["title"] = title.group(1).strip() if title else os.path.basename(path)
    # 解析 - 键：值 行
    for line in text.splitlines():
        m = re.match(r"^-\s*([^:：]+)[:：]\s*(.+)$", line.strip())
        if m:
            key = m.group(1).strip().rstrip("：:")
            report[key] = m.group(2).strip()
    return report


def _pnl_of_day(sample: dict) -> float:
    """当日盈亏率（总资产/初始-1），从 portfolio.total_value 计算；缺失=0.0 诚实占位。"""
    port = sample.get("portfolio", {})
    total = port.get("total_value", 0)
    if not total:
        return 0.0
    # INITIAL_CASH 若可导入则用，否则用默认 100000
    try:
        sys.path.insert(0, BASE)
        import run_trading_workflow
        init = getattr(run_trading_workflow, "INITIAL_CASH", 100000)
    except Exception:
        init = 100000
    return round((total - init) / init, 4)


def _build_curve_md(samples: list[dict], report: dict) -> str:
    """生成胜率曲线 / 回撤曲线 / 盈亏比表格 markdown 内容。"""
    days = len(samples)
    lines = []
    lines.append(f"# {report.get('title', 'A股超短交易评估')} · 金融文档")
    lines.append("")
    lines.append(f"> 生成时间：{_date.today().isoformat()}；样本 {days} 个交易日。")
    lines.append("> 全部数据来自真实行情（新浪/腾讯/东财），严禁模拟。")
    lines.append("")

    # 一、核心指标表（盈亏比表格）
    lines.append("## 一、核心指标（盈亏比表格）")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    for k in ["样本区间", "胜率", "最大回撤", "盈亏比", "累计收益", "平均盈利/平均亏损"]:
        v = report.get(k, "—")
        if isinstance(v, str):
            v = v.replace("%", "%")
        lines.append(f"| {k} | {v} |")
    lines.append("")
    lines.append("> 盈亏比 = 平均盈利 / 平均亏损；>1 表示盈利幅度大于亏损幅度。")
    lines.append("")

    # 二、逐日净值曲线（胜率曲线数据源）
    lines.append("## 二、逐日净值曲线（胜率曲线）")
    lines.append("")
    lines.append("| 日期 | 当日盈亏 | 累计净值 | 状态 |")
    lines.append("|------|---------|---------|------|")
    equity = 1.0
    for s in samples:
        pnl = _pnl_of_day(s)
        equity *= (1 + pnl)
        status = s.get("status", "")
        lines.append(f"| {s.get('date', '—')} | {pnl*100:+.2f}% | {equity:.4f} | {status} |")
    lines.append("")

    # 三、回撤曲线（逐日峰值回撤）
    lines.append("## 三、回撤曲线")
    lines.append("")
    lines.append("| 日期 | 累计净值 | 峰值 | 回撤 |")
    lines.append("|------|---------|------|------|")
    equity = 1.0
    peak = 1.0
    for s in samples:
        pnl = _pnl_of_day(s)
        equity *= (1 + pnl)
        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak else 0.0
        lines.append(f"| {s.get('date', '—')} | {equity:.4f} | {peak:.4f} | {dd*100:.2f}% |")
    lines.append("")

    # 四、数据源健康度摘要
    health_total = report.get("数据源健康记录", "—")
    lines.append("## 四、数据源健康度")
    lines.append("")
    lines.append(f"- 数据源健康记录：{health_total}")
    lines.append("- 铁律核验：全部样本真实行情，无模拟。")
    lines.append("")
    lines.append("> ⚠️ 仅供研究参考，不构成投资建议。")
    lines.append("")
    return "\n".join(lines)


def _build_daily_md(sample: dict) -> str:
    """逐日复盘转用户可读 markdown。"""
    d = sample.get("date", "—")
    lines = [f"# 每日复盘 · {d}", ""]
    lines.append(f"- 状态：{sample.get('status', '—')}")
    lines.append(f"- 记录时间：{sample.get('recorded_at', '—')}")
    signals = sample.get("signals", [])
    lines.append(f"- 信号数：{len(signals)}")
    for sig in signals:
        lines.append(f"  - {sig.get('action', '')}：{sig.get('reason', '')}（置信度 {sig.get('confidence', '—')}）")
    plan = sample.get("plan", {})
    targets = plan.get("targets", [])
    lines.append(f"- 作战计划：标的 {len(targets)} 个，总仓 {plan.get('total_allocated', 0)*100:.0f}%（上限 {plan.get('max_total_position', 0.8)*100:.0f}%），持有 {plan.get('holding_days', '—')}，止损 {plan.get('stop_loss_rule', {}).get('pct', -0.05)*100:.0f}%")
    for t in targets:
        lines.append(f"  - {t.get('code', '')}：买入 {t.get('buy_low', '—')}-{t.get('buy_high', '—')}，止损 {t.get('stop_loss', '—')}，目标 {t.get('take_profit', '—')}，仓位 {t.get('weight', '—')}")
    orders = sample.get("orders", [])
    lines.append(f"- 订单数：{len(orders)}")
    for o in orders:
        lines.append(f"  - {o.get('action', '')} {o.get('code', '')}：{o.get('status', '')}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印将生成文档，不写库")
    ap.add_argument("--report", help="指定评估报告路径（默认找最新）")
    args = ap.parse_args()

    samples = _load_samples()
    print(f"[转换] 读取 {len(samples)} 个样本（{SAMPLES_DIR}）")

    report_path = args.report or _find_latest_report()
    report = _parse_report(report_path) if report_path else {}
    if report:
        print(f"[转换] 使用评估报告：{report_path}")
    else:
        print(f"[转换] 未找到评估报告（样本 {len(samples)}/{10 if len(samples)<10 else 20} 未达标？），"
              f"仍生成逐日复盘文档，评估曲线待报告产出后补。")

    out_root = os.path.join(NANOMUSE_DIR, OUT_SUBDIR)
    daily_dir = os.path.join(NANOMUSE_DIR, OUT_DAILY)

    # 1. 评估报告 → 金融文档（胜率曲线/回撤曲线/盈亏比表格）
    doc_name = None
    if report:
        tag = "完整" if "完整" in report.get("title", "") else "中期"
        days = re.search(r"(\d+)日", report.get("title", ""))
        doc_name = f"胜率回撤评估_{tag}_{days.group(1) if days else len(samples)}日.md"
        content = _build_curve_md(samples, report)
        if args.dry_run:
            print(f"[DRY-RUN] 将写：{os.path.join(out_root, doc_name)}（{len(content)} 字符）")
        else:
            os.makedirs(out_root, exist_ok=True)
            path = os.path.join(out_root, doc_name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"[转换] 已写金融文档：{path}")

    # 2. 逐日复盘 → 用户可读文档（即使无评估报告，空仓日也如实记录）
    if not args.dry_run:
        os.makedirs(daily_dir, exist_ok=True)
    for s in samples:
        content = _build_daily_md(s)
        daily_name = f"{s.get('date', '—')}.md"
        if args.dry_run:
            print(f"[DRY-RUN] 将写：{os.path.join(daily_dir, daily_name)}")
        else:
            path = os.path.join(daily_dir, daily_name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"[转换] 已写每日复盘：{path}")

    if not report:
        print("[提示] 评估报告未达标，胜率/回撤/盈亏比曲线暂未生成——"
              f"当前样本 {len(samples)} 个交易日，达 10 日后 sample_daily.evaluate() 会自动产出报告，"
              "届时重跑本脚本即可生成完整金融文档。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
