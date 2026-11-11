# -*- coding: utf-8 -*-
"""
T1-T2 收尾补丁验证脚本 v2（悟空派单升级：代码-名称错位校验）
验证 monitor_intraday_risk 加「现价合理性 + 代码-名称比对」双校验。
按 A股短线交易专家三源核实：用户 5 只持仓里仅 300063(天龙集团) 代码对，
其余 4 只（002433/300672/603075/002637）代码-名称错位，应标「代码异常」不告警。
用用户 5 只真实持仓跑 monitor_intraday_risk，全真实行情，铁律合规。
"""
import sys
import os
import json

sys.stdout.reconfigure(encoding="utf-8")  # 防 GBK 编码报错

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORKSPACE)

import run_trading_workflow as rtw  # noqa: E402

POSITIONS_FILE = os.path.join(WORKSPACE, "data", "positions_2026-11-11.json")


def main():
    print("=" * 70)
    print("T1-T2 收尾补丁验证 v2（真实行情 + 代码-名称比对校验）")
    print("=" * 70)

    # 1. 读用户 5 只真实持仓
    with open(POSITIONS_FILE, encoding="utf-8") as f:
        positions = json.load(f)
    print(f"[i] 用户真实持仓 {len(positions)} 只：{list(positions.keys())}")

    # 2. 先单独拉行情看每个代码的实际返回名称（验证用，只读不落盘）
    import urllib.request

    def _tc(code):
        return ("sh" + code) if code.startswith("6") else ("sz" + code)

    url = "https://qt.gtimg.cn/q=" + ",".join(_tc(c) for c in positions.keys())
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
    raw = urllib.request.urlopen(req, timeout=8).read().decode("gbk", errors="replace")
    quote_names = {}
    for line in raw.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        try:
            payload = line.split("=", 1)[1].strip().strip('"')
            fields = payload.split("~")
            if len(fields) < 5:
                continue
            quote_names[fields[2]] = {"name": fields[1], "price": float(fields[3])}
        except (ValueError, IndexError):
            continue
    print("[i] 腾讯实际返回（代码→名称/现价）：")
    for c in positions.keys():
        q = quote_names.get(c, {})
        print(f"    {c} → {q.get('name')} / {q.get('price')}（持仓表 name={positions[c].get('name')}）")

    # 3. 跑 monitor_intraday_risk（真实行情 + 双校验）
    result = rtw.monitor_intraday_risk("2026-11-11", positions=positions)
    monitor = result["monitor"]

    print(f"[i] 监控结果：checked={monitor.get('checked')} "
          f"triggered={monitor.get('triggered')} "
          f"abnormal={monitor.get('abnormal')} "
          f"code_mismatch={monitor.get('code_mismatch')}")
    print(f"[i] summary：{monitor.get('summary')}")

    # 4. 断言（按三源核实：仅 300063 天龙集团代码对，应正常告警；其余 4 只错位不告警）
    alerts = {a["code"]: a for a in monitor.get("alerts", [])}
    passed = True

    # 300063 天龙集团（代码对）：应正常告警（真实破位 -29.15%）
    if "300063" not in alerts:
        print("[X] 300063 天龙集团 应触发硬止损告警，但未触发")
        passed = False
    else:
        a = alerts["300063"]
        print(f"[OK] 300063 天龙集团 告警：现价 {a['current_price']} "
              f"浮亏 {a['loss_pct']:.2%}（止损 {a['stop_loss_pct']:.0%}）")

    # 其余 4 只（002433/300672/603075/002637）：应标「代码异常」（code_mismatch），不触发止损告警
    mismatch_codes = ["002433", "300672", "603075", "002637"]
    for c in mismatch_codes:
        if c in alerts:
            print(f"[X] {c} 不应触发止损告警（代码-名称错位应剔除），但触发了")
            passed = False
        else:
            print(f"[OK] {c} 未触发告警（代码-名称错位剔除，需人工核实）")

    # 代码错位计数应 == 4
    if monitor.get("code_mismatch", 0) != 4:
        print(f"[X] code_mismatch 应 ==4，实际 {monitor.get('code_mismatch')}")
        passed = False
    else:
        print(f"[OK] code_mismatch={monitor.get('code_mismatch')}（4 只错位已剔除）")

    # 汇总
    print("=" * 70)
    print("PASS" if passed else "FAIL")
    print("=" * 70)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
