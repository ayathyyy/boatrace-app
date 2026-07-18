# 設計: BoatRacePredictor 20_設計/09_公開サイト自動収集設計.md §3-4
# 当日開催の全レースの単勝・複勝(oddstf)・2連単(odds2tf)オッズを取得し data/odds_today.json を生成する。
# ・取得関数は phase2/odds_fetch.py から逐語移植（万舟の整数オッズ・複勝レンジ下限・2連単30通り自己検証）
# ・開始時に公開サイトの現行 odds_today.json を取得し、当日分ならレース単位で上書きマージ
#   （一時的な取得失敗・未発売で既存値を消さない安全網。設計09 §3）
# ・日付は必ず JST（Actions は UTC。設計09 §6-2）
# 使い方: python scripts/fetch_odds.py [--date YYYYMMDD]

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from boatrace_common import BOATRACE_BASE as BASE
from boatrace_common import SLEEP_ODDS as SLEEP
from boatrace_common import UA as HEADERS
from boatrace_common import now_jst

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "odds_today.json"
LIVE_URL = "https://ayathyyy.github.io/boatrace-app/data/odds_today.json"
# Actions の timeout(27分) 内に必ず終えるためのスクリプト内デッドライン。
# 時間切れ分は打ち切り→liveマージにより前回値が残り、次スイープ（開始会場ローテーション）で補完される。
DEADLINE_SEC = 21 * 60

# 実ページは class="oddsPoint " と末尾に空白が入る（phase2/odds_fetch.py と同一）
RE_ODDS = re.compile(r'oddsPoint[^"]*">([^<]+)<')
RE_EXACTA_PAIR = re.compile(
    r'<td class="is-fs14 is-boatColor\d[^"]*">(\d+)</td>\s*'
    r'<td class="oddsPoint\s*">([^<]+)</td>')


def today_jcds(session, hd):
    """本日開催の場コード一覧をインデックスページから取得"""
    r = session.get(f"{BASE}/index?hd={hd}", headers=HEADERS, timeout=30)
    time.sleep(SLEEP)
    return sorted(set(re.findall(r"jcd=(\d{2})", r.text)))


def fetch_race_odds(session, jcd, rno, hd):
    """oddstf ページから 単勝6艇＋複勝6艇 のオッズを取得。開催なしは None"""
    r = session.get(f"{BASE}/oddstf?rno={rno}&jcd={jcd}&hd={hd}",
                    headers=HEADERS, timeout=30)
    time.sleep(SLEEP)
    vals = RE_ODDS.findall(r.text)
    if len(vals) < 12:
        return None  # 未発売・開催なし等
    def num(s):  # "3.4" / "1.0-1.2"（下限）/ "1725"（万舟級=小数点省略）/ "欠場"・"0.0" 等
        m = re.match(r"(\d+(?:\.\d+)?)", s.strip())
        if not m:
            return None
        v = float(m.group(1))
        return v if v > 0 else None  # 0.0 は欠場・未発売として除外
    tansho = [num(v) for v in vals[0:6]]
    fukusho = [num(v) for v in vals[6:12]]
    return tansho, fukusho


def fetch_race_exacta(session, jcd, rno, hd):
    """odds2tf ページから 2連単30通り のオッズを取得。{"i-j": odds} を返す。
    30通り揃わない/並びが想定外（欠場・未発売）は None。2連複は対象外。"""
    r = session.get(f"{BASE}/odds2tf?rno={rno}&jcd={jcd}&hd={hd}",
                    headers=HEADERS, timeout=30)
    time.sleep(SLEEP)
    text = r.text
    try:
        sec = text[text.index("2連単オッズ"):text.index("2連複オッズ")]
    except ValueError:
        return None
    pairs = RE_EXACTA_PAIR.findall(sec)
    if len(pairs) != 30:
        return None
    exacta = {}
    for k, (second, odds) in enumerate(pairs):
        first = (k % 6) + 1
        second = int(second)
        expected = [b for b in range(1, 7) if b != first][k // 6]
        if second != expected:  # 自己検証: 列内2着は昇順(自分除外)
            return None
        m = re.match(r"(\d+(?:\.\d+)?)", odds.strip())
        if not m:
            return None
        exacta[f"{first}-{second}"] = float(m.group(1))
    return exacta if len(exacta) == 30 else None


def load_live_base(hd):
    """公開サイトの現行JSONを取得。当日分なら races をマージのベースにする。"""
    try:
        r = requests.get(LIVE_URL, headers=HEADERS, timeout=15,
                         params={"t": int(time.time())})
        if r.status_code == 200:
            j = r.json()
            if j.get("date") == hd and isinstance(j.get("races"), dict):
                print(f"liveマージ: 既存 {len(j['races'])}レースをベースに更新")
                return j["races"]
    except Exception as e:
        print(f"live取得スキップ: {e}")
    return {}


def main():
    ap = argparse.ArgumentParser(description="当日全レースのオッズを odds_today.json へ（Actions用）")
    ap.add_argument("--date", help="対象日 YYYYMMDD（既定=JSTの今日）")
    args = ap.parse_args()
    hd = args.date or now_jst().strftime("%Y%m%d")

    session = requests.Session()
    t0 = time.monotonic()
    races = load_live_base(hd)
    jcds = today_jcds(session, hd)
    # 開始会場をスロットごとにローテーション（デッドライン打ち切り時の取り残しを均す）
    if jcds:
        rot = (now_jst().hour * 2 + now_jst().minute // 30) % len(jcds)
        jcds = jcds[rot:] + jcds[:rot]
        print(f"開催 {len(jcds)}場（開始会場ローテ: {jcds[0]} から）", flush=True)
    n_ok = n_ex = 0
    stopped = False
    for jcd in jcds:
        for rno in range(1, 13):
            if time.monotonic() - t0 > DEADLINE_SEC:
                stopped = True
                break
            try:
                got = fetch_race_odds(session, jcd, rno, hd)
                if not got:
                    continue  # 未発売等 → live ベース値があればそのまま残る
                entry = {"at": now_jst().strftime("%H:%M"),
                         "tansho": got[0], "fukusho": got[1]}
                ex = fetch_race_exacta(session, jcd, rno, hd)
                if ex:
                    entry["exacta"] = ex
                    n_ex += 1
                elif isinstance(races.get(f"{jcd}-{rno}"), dict) and "exacta" in races[f"{jcd}-{rno}"]:
                    entry["exacta"] = races[f"{jcd}-{rno}"]["exacta"]  # 今回だけ取れなかった場合は前回値を維持
                races[f"{jcd}-{rno}"] = entry
                n_ok += 1
            except Exception as e:
                print(f"{jcd}-{rno} ERROR {e}", flush=True)
        if stopped:
            break
        print(f"{jcd} 完了（経過 {int(time.monotonic() - t0)}秒）", flush=True)

    data = {"date": hd,
            "fetched_at": now_jst().isoformat(timespec="seconds"),
            "races": races}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    cut = "（⏱時間上限で途中打切り→残りは次スイープで補完）" if stopped else ""
    print(f"{hd}: 場={len(jcds)} 更新={n_ok}レース(2連単 {n_ex}) 合計={len(races)}レース{cut} → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
