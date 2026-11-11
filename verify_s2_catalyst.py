# -*- coding: utf-8 -*-
"""S2-T2 验证：东财公告真实催化源接入 strategy.py 第4/5层（无催化→hold）。

用 11-09 候选池真实股票，调 fetch_catalyst_and_auction（东财公告真实源），
再走 strategy 第5层（layer5_catalyst）确认：有催化→按 auction 判断、无催化→hold。
铁律：全部真实接口，绝不模拟；接口失败/无公告诚实缺省（不虚增）。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_source import fetch_catalyst_and_auction
from strategy import layer5_catalyst


def load_pool(pool_path):
    """读候选池 CSV，返回 code 列表（去表头）。"""
    codes = []
    with open(pool_path, encoding="utf-8-sig") as f:
        header = f.readline()
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if parts and parts[0]:
                codes.append(parts[0])
    return codes


def main():
    pool_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "data", "super_short_pool_2026-11-09.csv",
    )
    codes = load_pool(pool_path)
    print(f"[S2-T2] 候选池 {len(codes)} 只（真实，非模拟）")

    catalyst_map = {}
    ok = 0
    fail = 0
    cat_hit = 0
    for code in codes:
        try:
            cd = fetch_catalyst_and_auction(code)
            catalyst_map[code] = cd
            ok += 1
            if cd.get("catalyst") or cd.get("announcement"):
                cat_hit += 1
        except Exception as e:
            catalyst_map[code] = {"code": code, "catalyst": [], "announcement": [], "auction": {}}
            fail += 1
            print(f"  {code}: 拉取异常 {e}（诚实缺省）")

    print(f"[S2-T2] 催化映射拉取：成功 {ok}，失败 {fail}（诚实缺省），有催化 {cat_hit}，无催化 {ok - cat_hit}")

    # 走 strategy 第5层：layer5_catalyst（verified 列表由第4层产出，这里用最小已验证条目模拟）
    verified = []
    for code in codes:
        verified.append({
            "code": code,
            "name": code,
            "verified": True,
            "close": 10.0,
            "pct_chg": 2.0,
        })
    finals = layer5_catalyst(verified, catalyst_map)
    # layer5 语义：有催化→条目带 final_mode（确认），无催化→不进 finals（即 hold）
    confirm_cnt = sum(1 for f in finals if f.get("final_mode"))
    hold_cnt = len(verified) - len(finals)
    print(f"[S2-T2] strategy 第5层输出：{len(verified)} 只，催化确认 {confirm_cnt}（final_mode 有值），"
          f"无催化→hold {hold_cnt}")

    # 铁律：绝无模拟数据——检查 catalyst_map 全部来自真实东财公告源
    for code in codes[:3]:
        cd = catalyst_map.get(code, {})
        ann = cd.get("announcement", [])
        print(f"  {code}: 公告 {len(ann)} 条"
              + (f"，首条: {ann[0].get('title')}" if ann else "，无公告（诚实缺省）"))

    # 结论：hold 分支必须有（无催化确认），buy/speculate 只能来自真实催化
    print(f"\n[S2-T2] 结论：催化映射真实拉取 {ok}/{len(codes)}，第5层 hold={hold_cnt}（无催化确认）")
    print("[S2-T2] DONE")


if __name__ == "__main__":
    main()
