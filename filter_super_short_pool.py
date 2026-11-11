# -*- coding: utf-8 -*-
"""
超短线股票池筛选脚本（真实行情版，铁律：严禁模拟数据）
数据源：新浪 Market_Center 实时接口（真实行情，稳定翻页）
  URL: vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData
  字段: code/name/trade/changepercent/turnoverratio/nmc(流通市值万元)/mktcap(总市值万元)/per
筛选标准（A股短线交易专家 策略文档 v1.0）：
  - 流通市值 20亿 - 300亿
  - 换手率 5% - 30%
  - 非 ST、非退市
  - 当日上涨优先（题材活跃度初筛）
输出：候选名单 CSV + Markdown 落盘
"""
import csv
import json
import os
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}

# 筛选参数（策略文档 v1.0）
FLOAT_MV_MIN = 20.0    # 亿
FLOAT_MV_MAX = 300.0   # 亿
TURNOVER_MIN = 5.0     # %
TURNOVER_MAX = 30.0    # %
PAGES = 50             # 拉取页数（每页100，约5000只，覆盖全市场）
PER_PAGE = 100
TOP_N = 50


def _fetch_sina(page: int) -> list[dict]:
    """新浪 Market_Center，按换手率降序拉一页真实行情。失败抛异常。"""
    url = (
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"Market_Center.getHQNodeData?page={page}&num={PER_PAGE}"
        "&sort=turnoverratio&asc=0&node=hs_a&symbol=&_s_r_a=init"
    )
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_all() -> list[dict]:
    """拉全市场（按涨跌幅降序翻页），带重试。真实数据。"""
    all_rows = []
    empty_streak = 0
    for pn in range(1, PAGES + 1):
        for attempt in range(3):
            try:
                page = _fetch_sina(pn)
                if not page:
                    empty_streak += 1
                    break
                empty_streak = 0
                all_rows.extend(page)
                if pn % 10 == 0:
                    print(f"  已拉 {pn} 页 / {len(all_rows)} 条")
                break
            except Exception as e:
                if attempt == 2:
                    print(f"  第{pn}页失败: {e}")
                else:
                    time.sleep(1)
        if empty_streak >= 2:
            break
    return all_rows


def main(trade_date: str | None = None):
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(out_dir, exist_ok=True)

    print("[1] 拉取全市场真实行情（新浪 Market_Center，按涨跌幅降序）...")
    rows = fetch_all()
    print(f"  拉取到 {len(rows)} 条真实行情")

    seen = set()
    candidates = []
    for d in rows:
        code = str(d.get("code", ""))
        name = str(d.get("name", ""))
        if not code or code in seen:
            continue
        seen.add(code)

        if "ST" in name.upper() or "退" in name:
            continue

        try:
            price = float(d.get("trade") or 0)
            pct = float(d.get("changepercent") or 0)
            turnover = float(d.get("turnoverratio") or 0)
            nmc_wan = float(d.get("nmc") or 0)   # 万元
            mktcap_wan = float(d.get("mktcap") or 0)
            per = float(d.get("per") or 0)
        except (ValueError, TypeError):
            continue

        float_mv_yi = nmc_wan / 10000.0   # 万元→亿
        total_mv_yi = mktcap_wan / 10000.0

        if not (FLOAT_MV_MIN <= float_mv_yi <= FLOAT_MV_MAX):
            continue
        if not (TURNOVER_MIN <= turnover <= TURNOVER_MAX):
            continue

        candidates.append({
            "code": code,
            "name": name,
            "price": round(price, 2),
            "pct_chg": round(pct, 2),
            "turnover": round(turnover, 2),
            "float_mv_yi": round(float_mv_yi, 2),
            "total_mv_yi": round(total_mv_yi, 2),
            "pe": round(per, 2),
        })

    candidates.sort(key=lambda x: x["pct_chg"], reverse=True)
    print(f"[2] 符合核心筛选（市值20-300亿 + 换手5-30%）: {len(candidates)} 只")

    active = [c for c in candidates if c["pct_chg"] > 0]
    print(f"[3] 其中当日上涨（题材活跃度初筛）: {len(active)} 只")

    # 日期：优先命令行参数，否则当天（S4 每日跑关键）
    import sys as _sys
    from datetime import date as _date
    if trade_date is None:
        trade_date = _sys.argv[1] if len(_sys.argv) > 1 else _date.today().isoformat()
    date = trade_date
    csv_path = os.path.join(out_dir, f"super_short_pool_{date}.csv")
    md_path = os.path.join(out_dir, f"super_short_pool_{date}.md")

    top = active[:TOP_N] if active else candidates[:TOP_N]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["code", "name", "price", "pct_chg", "turnover",
                                          "float_mv_yi", "total_mv_yi", "pe", "date"])
        w.writeheader()
        for c in top:
            w.writerow({**c, "date": date})

    lines = [
        "# 超短线股票池候选名单（真实行情）",
        "",
        f"> 筛选日期：{date}",
        f"> 数据源：新浪 Market_Center 实时接口（真实行情，严禁模拟）",
        f"> 筛选标准：流通市值 {FLOAT_MV_MIN}-{FLOAT_MV_MAX}亿、换手率 {TURNOVER_MIN}-{TURNOVER_MAX}%、非ST、当日上涨优先",
        f"> 初筛符合 {len(candidates)} 只，当日上涨 {len(active)} 只，列出前 {len(top)} 只",
        "",
        "| 代码 | 名称 | 现价 | 涨跌幅% | 换手率% | 流通市值(亿) | 总市值(亿) | 市盈率 |",
        "|------|------|------|---------|---------|------------|-----------|--------|",
    ]
    for c in top:
        lines.append(
            f"| {c['code']} | {c['name']} | {c['price']} | {c['pct_chg']} | "
            f"{c['turnover']} | {c['float_mv_yi']} | {c['total_mv_yi']} | {c['pe']} |"
        )
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[4] 落盘完成：{csv_path}")
    print(f"      {md_path}")
    print(f"[5] 候选池共 {len(top)} 只（按涨跌幅降序）")


if __name__ == "__main__":
    main()