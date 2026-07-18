# 設計: BoatRacePredictor 20_設計/09_公開サイト自動収集設計.md §4
# 当日の番組表B(mbrace)から data/racelist_today.json を生成する（GitHub Actions 用・DB非依存）。
# phase2/export_racelist.py と同スキーマ（generated_at キーのみ追加・アプリの ^\d{8}$ フィルタで互換安全）。
# ST は data/fan_st.json（PCのDBから半年ごとに生成）で補完: コース別→選手平均→0.16。
# 0レースのときは exit 1（コミットもデプロイもしない＝前日データ維持。設計09 §4）。
# 使い方: python scripts/fetch_racelist.py [--date YYYYMMDD]

import argparse
import json
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from boatrace_common import JCD_NAMES, fetch_b_text, now_jst, parse_b

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "racelist_today.json"
FAN_ST = ROOT / "data" / "fan_st.json"


def load_fan_st():
    """fan_st.json → { regno(str): [c1..c6 の平均ST or None] }。無ければ空。"""
    try:
        return json.loads(FAN_ST.read_text(encoding="utf-8"))["st"]
    except Exception as e:
        print(f"fan_st.json 読込失敗（ST=0.16固定で続行）: {e}")
        return {}


def resolve_st(fan, regno: str, lane: int):
    """phase2/export_racelist.py resolve_st と同ロジック（0.01〜1のみ有効・round2）"""
    arr = fan.get(regno)
    if arr:
        st = arr[lane - 1] if 1 <= lane <= 6 else None
        if st is not None and 0.01 <= st <= 1:
            return round(st, 2)
        vals = [v for v in arr if v is not None and 0.01 <= v <= 1]
        if vals:
            return round(sum(vals) / len(vals), 2)
    return 0.16


def main():
    ap = argparse.ArgumentParser(description="当日Bから racelist_today.json を生成（Actions用）")
    ap.add_argument("--date", help="対象日 YYYYMMDD（既定=JSTの今日）")
    args = ap.parse_args()
    ymd = args.date or now_jst().strftime("%Y%m%d")

    session = requests.Session()
    text = fetch_b_text(session, ymd)
    if text is None:
        print(f"{ymd}: 番組表Bが未提供(404)。生成を中止します（exit 1）")
        return 1

    races, entries = parse_b(text, ymd)
    if not entries:
        print(f"{ymd}: entries が0件。生成を中止します（exit 1）")
        return 1

    # 締切予定時刻マップ {"jcd-rno": "HH:MM"}（fetch_odds の締切優先収集用・T-20260718-09）
    closes = {}
    for r in races:
        if r[6]:
            closes[f"{r[2]}-{r[3]}"] = r[6]

    fan = load_fan_st()
    day, venues = {}, {}
    # entries: (rid, lane, racer_no, name, class, nat_win, nat2, local_win, loc2, motor_no, motor2, bno, b2)
    for e in sorted(entries, key=lambda t: (t[0], t[1])):
        rid, lane, racer, name = e[0], e[1], e[2], e[3]
        nat, loc, motor = e[5], e[7], e[10]
        _ymd, jcd, rno = rid.split("-")
        day.setdefault(f"{jcd}-{rno}", []).append({
            "name": name or "",
            "regno": str(racer),
            "nat": nat,
            "local": loc,
            "motor": motor,
            "st": resolve_st(fan, str(racer), lane),
        })
        venues[jcd] = JCD_NAMES.get(jcd, jcd)

    data = {
        "generated_at": now_jst().isoformat(timespec="seconds"),
        ymd: day,
        "venues": dict(sorted(venues.items())),
        "closes": closes,   # 締切予定時刻（アプリは未使用・fetch_odds の優先順位付けに使用）
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    nboat = sum(len(v) for v in day.values())
    print(f"{ymd}: {len(venues)}会場 {len(day)}レース {nboat}艇 → {OUT}")
    bad = [k for k, v in day.items() if len(v) != 6]
    if bad:
        print(f"警告: 6艇でないレース {len(bad)}件: {bad[:10]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
