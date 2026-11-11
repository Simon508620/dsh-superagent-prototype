# -*- coding: utf-8 -*-
"""
T1-T2 盘中实时风控验证脚本（悟空派单，用户拍板③盘中实时风控）
验证 risk.monitor_positions：盘中持仓实时监控 + -5% 硬止损盘中触发盯盘。

用真实行情（腾讯 qt.gtimg.cn 批量接口）读持仓标的实时价，判断是否破 -5% 硬止损位。
铁律：所有行情必须真实接口，严禁模拟；无真实行情持仓诚实缺省不虚增告警。
"""
import sys
import os
import json

sys.stdout.reconfigure(encoding="utf-8")  # 防 GBK 编码报错

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORKSPACE)

from risk import monitor_positions  # noqa: E402


def to_tencent_code(code):
    """6位裸代码转腾讯带前缀格式：6开头=sh，其余=sz。"""
    code = code.strip()
    if code.startswith("6"):
        return "sh" + code
    return "sz" + code


def fetch_realtime_quotes(codes):
    """真实行情：腾讯 qt.gtimg.cn 批量接口。失败返回 {} 诚实缺省。"""
    if not codes:
        return {}
    import urllib.request
    tcodes = [to_tencent_code(c) for c in codes]
    url = "https://qt.gtimg.cn/q=" + ",".join(tcodes)
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
        raw = urllib.request.urlopen(req, timeout=8).read().decode("gbk", errors="replace")
    except Exception as e:
        print(f"[X] 真实行情拉取失败（诚实缺省返回空）：{e}")
        return {}
    quotes = {}
    for line in raw.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        try:
            # v_sh600519="1~贵州茅台~600519~当前价~..."
            payload = line.split("=", 1)[1].strip().strip('"')
            fields = payload.split("~")
            if len(fields) < 4:
                continue
            code = fields[2]
            price = float(fields[3])
            quotes[code] = {"price": price}
        except (ValueError, IndexError):
            continue
    return quotes


def main():
    print("=" * 60)
    print("T1-T2 盘中实时风控验证（真实行情）")
    print("=" * 60)

    # 用 11-09 候选池真实标的做持仓模拟（仅监控用，非模拟数据——行情本身是真实的）
    # 候选池：data/super_short_pool_2026-11-09.csv（真实候选）
    pool_path = os.path.join(WORKSPACE, "data", "super_short_pool_2026-11-09.csv")
    codes = []
    if os.path.exists(pool_path):
        with open(pool_path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i == 0:
                    continue
                parts = line.strip().split(",")
                if parts and parts[0].strip():
                    codes.append(parts[0].strip())
    codes = codes[:10]  # 取前10只做监控样本
    print(f"[i] 监控标的（真实候选池前10）：{codes}")

    # 真实行情
    quotes = fetch_realtime_quotes(codes)
    print(f"[i] 真实行情拉取：{len(quotes)}/{len(codes)} 只")
    if not quotes:
        print("[X] 无真实行情，无法验证——铁律：禁止模拟行情。exit 1")
        return 1

    # 构造持仓：以当前价作为成本价基准，制造一个破 -5% 的持仓（真实行情价推算，非模拟）
    # 注意：成本价用真实行情价的函数推导，只用于验证监控逻辑是否对破位正确告警
    positions = {}
    for code in codes:
        if code in quotes:
            price = quotes[code]["price"]
            # 正常持仓（成本=当前价*1.02，浮亏约2%，未破-5%）
            positions[code] = {"qty": 100, "cost_price": round(price * 1.02, 2)}
    # 制造一个破位持仓：成本 = 当前价 * 1.06（浮亏约5.7%>5%，应触发硬止损告警）
    # 用真实行情价推算成本，验证监控对破位的判定，非模拟行情
    if codes and codes[0] in quotes:
        code0 = codes[0]
        price0 = quotes[code0]["price"]
        positions[code0] = {"qty": 100, "cost_price": round(price0 * 1.06, 2)}

    print(f"[i] 持仓构造：{len(positions)} 只（含1只成本=现价*1.06 制造破-5%位）")

    # 盘中实时监控
    result = monitor_positions(positions, quotes, {"stop_loss_pct": -0.05})
    print(f"[i] monitor_positions 结果：")
    print(f"    checked={result['checked']}, triggered={result['triggered']}")
    print(f"    summary={result['summary']}")

    alerts = result["alerts"]
    if alerts:
        for a in alerts:
            print(f"    [告警] {a['code']} 成本{a['cost_price']} 现价{a['current_price']} "
                  f"浮亏{a['loss_pct']:.2%} 触发硬止损 {a['stop_loss_pct']:.0%}")

    # 落盘告警
    out_dir = os.path.join(WORKSPACE, "data")
    os.makedirs(out_dir, exist_ok=True)
    alert_file = os.path.join(out_dir, "盘中风控告警_2026-11-11.json")
    with open(alert_file, "w", encoding="utf-8") as f:
        json.dump({"trade_date": "2026-11-11", "monitor": result}, f, ensure_ascii=False, indent=2)
    print(f"[i] 告警已落盘：{alert_file}")

    # 断言：破位持仓应触发告警
    assert result["triggered"] >= 1, "破位持仓未触发硬止损告警！"
    assert all(a["action"] == "硬止损触发" for a in alerts), "告警 action 应为硬止损触发"

    # 断言：无真实行情/无效价不虚增
    empty = monitor_positions({}, {}, {"stop_loss_pct": -0.05})
    assert empty["checked"] == 0 and empty["triggered"] == 0, "空持仓应诚实缺省不虚增"
    print(f"[OK] 空持仓/无行情诚实缺省：checked=0 triggered=0（不虚增告警）")

    # 断言：正常持仓（浮亏2%）不误触发
    normal = monitor_positions({"600519": {"qty": 100, "cost_price": quotes.get("600519", {}).get("price", 0) * 1.02}}, quotes, {"stop_loss_pct": -0.05})
    if "600519" in quotes:
        assert normal["triggered"] == 0, "正常持仓误触发告警！"
        print("[OK] 正常持仓（浮亏2%）未误触发")

    print(f"\n[OK] T1-T2 盘中实时风控验证通过（真实行情，exit 0）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
