# -*- coding: utf-8 -*-
"""
风控模块（阶段3跑通用，简化实现版 + T1-T2 盘中实时风控扩展）
按策略文档第四章风控逻辑做硬约束：
- 单票仓位上限（默认 25%）
- 总仓位上限（按情绪阶段，默认发酵期 80%）
- 单日亏损/连续亏损等账户级风控在真实环境接入账户流水后启用
T1-T2 新增（2026-11-11 悟空派单，用户拍板③盘中实时风控）：
- monitor_positions()：盘中持仓实时监控，读实时行情判断持仓是否破 -5% 硬止损位，破位产出盘中风控告警
- 盘中盯盘替代纯计划级静态校验（validate_plan 保留用于买入前计划校验）
铁律：所有行情必须真实接口（腾讯/东财/新浪），严禁模拟；告警只落盘报群，实盘执行由用户自决策。
"""


def check_risk(signal: dict, portfolio: dict, risk_params: dict | None = None) -> dict:
    """接口契约见 对接流程.md 3.3。返回放行/拦截结论。"""
    risk_params = risk_params or {}
    max_position = risk_params.get("max_position", 0.25)      # 单票上限
    max_total = risk_params.get("max_total_position", 0.80)   # 总仓位上限

    cash = portfolio.get("cash", 0.0)
    total_value = portfolio.get("total_value", cash)
    positions = portfolio.get("positions", {})  # {code: {"qty":int,"value":float}}

    if signal["action"] == "sell":
        if signal["code"] not in positions:
            return {"allowed": False, "reason": "无持仓，卖出信号忽略", "max_position": max_position}
        return {"allowed": True, "reason": "卖出持仓，放行", "max_position": max_position}

    # buy 信号
    order_value = signal["price"] * 100  # 最小 1 手
    if order_value > cash:
        return {"allowed": False, "reason": f"现金不足（需 {order_value:.0f}，余 {cash:.0f}）", "max_position": max_position}

    cur = positions.get(signal["code"], {}).get("value", 0.0)
    if (cur + order_value) / total_value > max_position:
        return {"allowed": False, "reason": f"单票仓位超限（{(cur + order_value) / total_value:.0%} > {max_position:.0%}）", "max_position": max_position}

    invested = sum(p["value"] for p in positions.values())
    if (invested + order_value) / total_value > max_total:
        return {"allowed": False, "reason": f"总仓位超限（{(invested + order_value) / total_value:.0%} > {max_total:.0%}）", "max_position": max_position}

    return {"allowed": True, "reason": "单票与总仓位均未超限", "max_position": max_position}


def validate_plan(plan: dict, risk_params: dict | None = None) -> dict:
    """作战计划级风控校验（S3：plan_builder → risk 链路核心）。

    对整个作战计划做硬约束校验，确认生成的目标组合符合风控铁律：
    - 单票仓位占比 ≤ max_position（默认 25%）
    - 总仓位占比 ≤ max_total_position（默认 80%）
    - 止损规则 = 硬止损 -5%（stop_loss_rule.pct 或每个 target 的 stop_loss 相对买入价）
    输入：plan_builder.build_plan 输出的作战计划 dict。
    返回：{"valid": bool, "checks": [...], "violations": [...], "summary": str}
    铁律：仅校验，绝不修改仓位占比（计划已按上限缩放，校验确认）。
    """
    risk_params = risk_params or {}
    max_position = risk_params.get("max_position", 0.25)
    max_total = risk_params.get("max_total_position", 0.80)
    stop_loss_pct = risk_params.get("stop_loss_pct", -0.05)

    targets = plan.get("targets", [])
    total_allocated = float(plan.get("total_allocated", 0.0) or 0.0)
    checks, violations = [], []

    # 1. 总仓位校验
    if total_allocated > max_total + 1e-9:
        violations.append(f"总仓位超限：{total_allocated:.2%} > {max_total:.2%}")
    checks.append({"check": "总仓位", "value": total_allocated, "limit": max_total,
                   "ok": total_allocated <= max_total + 1e-9})

    # 2. 单票校验（逐个 target）
    for t in targets:
        ratio = float(t.get("position_ratio", 0.0) or 0.0)
        ok = ratio <= max_position + 1e-9
        checks.append({"check": f"单票 {t.get('code')} {t.get('name', '')}",
                       "value": ratio, "limit": max_position, "ok": ok})
        if not ok:
            violations.append(f"单票超限 {t.get('code')}：{ratio:.2%} > {max_position:.2%}")

    # 3. 止损规则校验（-5% 硬止损）
    rule_pct = plan.get("stop_loss_rule", {}).get("pct", stop_loss_pct)
    if abs(float(rule_pct) - stop_loss_pct) > 1e-9:
        violations.append(f"止损规则不符：计划 {rule_pct} ≠ 风控 {stop_loss_pct}")
    checks.append({"check": "止损规则", "value": rule_pct, "limit": stop_loss_pct,
                   "ok": abs(float(rule_pct) - stop_loss_pct) <= 1e-9})

    # 4. 持有周期校验（超短 1-3 日）
    holding = plan.get("holding_days", "")
    holding_ok = holding in ("1-3日", "1-3 日", "1-3日(超短)")
    checks.append({"check": "持有周期", "value": holding, "limit": "1-3日", "ok": holding_ok})
    if not holding_ok:
        violations.append(f"持有周期不符：{holding}（应为1-3日超短）")

    valid = len(violations) == 0
    return {
        "valid": valid,
        "checks": checks,
        "violations": violations,
        "summary": ("作战计划风控校验通过" if valid else
                    "；".join(violations)),
        "max_position": max_position,
        "max_total_position": max_total,
        "stop_loss_pct": stop_loss_pct,
    }


def monitor_positions(positions: dict, quotes: dict, risk_params: dict | None = None) -> dict:
    """盘中持仓实时监控 + -5% 硬止损盘中触发盯盘（T1-T2 悟空派单）。

    读实时行情判断每个持仓是否跌破硬止损位，破位即产出盘中风控告警。
    替代纯计划级静态校验（validate_plan 仍保留用于买入前计划校验）。

    输入：
        positions: {code: {"qty": int, "cost_price": float, "name": str, ...}} 当前持仓（成本价+名称）
        quotes:    {code: {"price": float, "prev_close": float|None, "pct_chg": float|None, "name": str, "abnormal": bool, ...}} 实时行情快照（真实接口）
        risk_params: 可选，{stop_loss_pct: -0.05}，默认 -5% 硬止损
    返回：
        {"alerts": [...], "checked": int, "triggered": int, "abnormal": int, "code_mismatch": int, "summary": str}
        每个 alert: {code, qty, cost_price, current_price, prev_close, pct_chg, loss_pct, stop_loss_pct, action}
    铁律：quotes 必须来自真实接口；缺失行情视为无法判定（不虚增告警），
    行情异常（abnormal=True，涨跌幅超 ±10% 或现价<=0）不参与止损判断（防脏数据误告警），
    代码-名称错位（quote.name 与 pos.name 对不上）标「代码异常」不参与止损判断（防误告警），
    告警只产出信号供盘后/盘中落盘报群，实盘执行由用户自决策。
    """
    risk_params = risk_params or {}
    stop_loss_pct = risk_params.get("stop_loss_pct", -0.05)  # 默认 -5% 硬止损

    alerts = []
    checked = 0
    triggered = 0
    abnormal = 0
    code_mismatch = 0

    for code, pos in (positions or {}).items():
        quote = (quotes or {}).get(code)
        if not quote:
            # 真实行情缺失：诚实缺省，无法判定不虚增告警
            continue
        current_price = float(quote.get("price", 0.0) or 0.0)
        cost_price = float(pos.get("cost_price", 0.0) or 0.0)
        if current_price <= 0 or cost_price <= 0:
            # 无效价格：无法判定，跳过
            continue
        # 代码-名称错位校验（T1-T2 收尾补丁）：腾讯 [1] 名称与持仓 name 对不上 → 标代码异常，不参与止损判断
        pos_name = (pos.get("name") or "").strip()
        q_name = (quote.get("name") or "").strip()
        if pos_name and q_name and pos_name not in q_name and q_name not in pos_name:
            code_mismatch += 1
            continue        # 现价合理性校验（T1-T2 收尾补丁）：行情异常（涨跌幅超 ±10% 或现价<=0）不参与止损判断
        if quote.get("abnormal"):
            abnormal += 1
            continue

        checked += 1
        loss_pct = (current_price - cost_price) / cost_price  # 负值=亏损

        if loss_pct <= stop_loss_pct:  # 跌破/触及硬止损位
            triggered += 1
            alerts.append({
                "code": code,
                "qty": int(pos.get("qty", 0) or 0),
                "cost_price": cost_price,
                "current_price": current_price,
                "prev_close": quote.get("prev_close"),
                "pct_chg": quote.get("pct_chg"),
                "loss_pct": loss_pct,
                "stop_loss_pct": stop_loss_pct,
                "action": "硬止损触发",
                "alert_time": None,  # 由调用方补时间戳
            })

    return {
        "alerts": alerts,
        "checked": checked,
        "triggered": triggered,
        "abnormal": abnormal,
        "code_mismatch": code_mismatch,
        "summary": (f"盘中持仓监控：检查 {checked} 只（代码异常剔除 {code_mismatch} 只，"
                    f"行情异常剔除 {abnormal} 只），触发硬止损告警 {triggered} 只"
                    if triggered == 0 else
                    f"盘中持仓监控：检查 {checked} 只（代码异常剔除 {code_mismatch} 只，"
                    f"行情异常剔除 {abnormal} 只），触发硬止损告警 {triggered} 只！"
                    f"（{', '.join(a['code'] for a in alerts)}）"),
    }
