# -*- coding: utf-8 -*-
"""
作战计划文件生成器（plan_file_writer.py）—— S3 交付核心
把 plan_builder.build_plan 输出的作战计划 dict 落盘为每日作战计划文件：
  - Markdown（可读，含标的清单+每标的分仓+总仓+持有周期+止损规则）
  - CSV（结构化，可被 Agent 或后续执行模块消费）
铁律：仅格式化输出，绝不引入任何模拟数据；输入必须是真实评分/信号产出的计划。
接口：write_plan_files(plan, trade_date, out_dir) -> {"markdown": path, "csv": path}
"""
import os
import csv


def _fmt_pct(v):
    try:
        return f"{float(v):.2%}"
    except (TypeError, ValueError):
        return "—"


def _fmt_price(v):
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return "—"


def write_plan_files(plan: dict, trade_date: str, out_dir: str) -> dict:
    """把作战计划 dict 写为 Markdown + CSV 两个文件，返回路径字典。"""
    os.makedirs(out_dir, exist_ok=True)
    targets = plan.get("targets", [])
    max_total = plan.get("max_total_position", 0.80)
    total_alloc = plan.get("total_allocated", 0.0)
    holding = plan.get("holding_days", "1-3日")
    stop_rule = plan.get("stop_loss_rule", {}).get("pct", -0.05)
    tp_rule = plan.get("take_profit_rule", {}).get("pct", 0.10)

    md_path = os.path.join(out_dir, f"作战计划_{trade_date}.md")
    csv_path = os.path.join(out_dir, f"作战计划_{trade_date}.csv")

    # --- Markdown ---
    lines = [
        f"# 每日作战计划 · {trade_date}",
        "",
        "> 铁律：全部数据来自真实行情接口（新浪/东财/腾讯），严禁模拟。",
        "",
        f"- **总仓位上限**：{_fmt_pct(max_total)}",
        f"- **已分配仓位**：{_fmt_pct(total_alloc)}",
        f"- **持有周期**：{holding}（超短）",
        f"- **止损规则**：硬止损 {_fmt_pct(stop_rule)}",
        f"- **止盈规则**：{_fmt_pct(tp_rule)}",
        f"- **操作标的数**：{len(targets)}",
        "",
        "## 操作标的清单",
        "",
    ]
    if not targets:
        lines += ["（今日无操作标的——诚实空仓，无候选触发或数据缺省。）", ""]
    else:
        lines += [
            "| 代码 | 名称 | 综合分 | 仓位占比 | 买入区间 | 止损位 | 目标位 | 理由 |",
            "|------|------|-------:|--------:|----------|-------:|-------:|------|",
        ]
        for t in targets:
            bz = t.get("buy_zone", {})
            lines.append(
                f"| {t.get('code', '')} | {t.get('name', '')} | "
                f"{float(t.get('total_score', 0)):.3f} | {_fmt_pct(t.get('position_ratio', 0))} | "
                f"{_fmt_price(bz.get('low'))}-{_fmt_price(bz.get('high'))} | "
                f"{_fmt_price(t.get('stop_loss'))} | {_fmt_price(t.get('take_profit'))} | "
                f"{t.get('plan_reason', '')} |"
            )
        lines += [""]

    # --- CSV ---
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["trade_date", "code", "name", "total_score", "position_ratio",
                         "buy_low", "buy_high", "stop_loss", "take_profit",
                         "max_total_position", "holding_days", "stop_loss_rule", "plan_reason"])
        for t in targets:
            bz = t.get("buy_zone", {})
            writer.writerow([
                trade_date, t.get("code", ""), t.get("name", ""),
                t.get("total_score", ""), t.get("position_ratio", ""),
                bz.get("low", ""), bz.get("high", ""),
                t.get("stop_loss", ""), t.get("take_profit", ""),
                max_total, holding, stop_rule, t.get("plan_reason", ""),
            ])

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return {"markdown": md_path, "csv": csv_path}
