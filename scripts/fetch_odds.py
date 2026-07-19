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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from boatrace_common import BOATRACE_BASE as BASE
from boatrace_common import UA as HEADERS
from boatrace_common import now_jst

# Actions ランナーからの応答が遅く（実測 数秒/ページ）21分で全会場を回りきれないため、
# 3並列×待機1.0秒に調整（合計リクエストレートは従来の単独2.0秒間隔と同水準・設計09 §6-8）。
SLEEP = 1.0
WORKERS = 5   # 締切近接絞り(§8.4)で対象が減るぶん並列を1増やす。ピークレートはIP遮断リスク(§6-1)最優先で控えめ据え置き（設計09 §6-8/§8.4）
_tls = threading.local()


def _session():
    """スレッドごとに requests.Session を1本持つ（keep-alive維持）"""
    if not hasattr(_tls, "s"):
        _tls.s = requests.Session()
    return _tls.s

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "odds_today.json"
LIVE_URL = "https://ayathyyy.github.io/boatrace-app/data/odds_today.json"
# 自己連鎖(設計09 §8)の間隔~13分に合わせたスクリプト内デッドライン。workflow timeout(25分)内で必ず終える。
# 時間切れ分は打ち切り→liveマージで前回値が残り、次連鎖（締切優先/開始会場ローテーション）で補完される。
DEADLINE_SEC = 10 * 60
# 締切がこの分数より先の「遠い未来レース」は対象外にして対象数を絞る（スイープ短縮・近接レースの鮮度優先）。
# 締切が近づけば後続の連鎖スイープで自然に対象化される（設計09 §8.4）。
FUTURE_HORIZON_MIN = 120

# 実ページは class="oddsPoint " と末尾に空白が入る（phase2/odds_fetch.py と同一）
RE_ODDS = re.compile(r'oddsPoint[^"]*">([^<]+)<')
# 「艇番セル（is-boatColor 系）＋直後の oddsPoint セル」のペア。class の前置き（is-fs14 等）や
# 後置き（is-borderLeftNone 等）の表記ゆれを許容する（2026-07-18 実ページで 116/120 落ちが出たため緩和）。
# 2着/相手グループのセルは直後が oddsPoint でないため誤マッチしない（隣接条件でフィルタ）。
RE_CELL = re.compile(
    r'<td class="[^"]*is-boatColor\d[^"]*">(\d+)</td>\s*'
    r'<td class="oddsPoint[^"]*">([^<]+)</td>')


def num_range(s):
    """'3.4' → (3.4, 3.4) / '1.0-1.2' → (1.0, 1.2) / '1725'（万舟整数）対応。不正・0.0 は None"""
    m = re.match(r"(\d+(?:\.\d+)?)(?:-(\d+(?:\.\d+)?))?", s.strip())
    if not m:
        return None
    lo = float(m.group(1))
    hi = float(m.group(2)) if m.group(2) else lo
    return (lo, hi) if lo > 0 else None


def _reconstruct(pairs, seqs, as_range=False):
    """(表示数字, オッズ文字列) の列を、候補シーケンス（組タプルの並び・2026-07-18 実ページ解読済み）に割り当てる。
    各セルの表示数字＝組の最後の要素、で全数自己検証し、完全一致した候補のみ採用（不一致は None）。"""
    for seq in seqs:
        if len(pairs) != len(seq):
            continue
        out = {}
        ok = True
        for (digit, odds), combo in zip(pairs, seq):
            if int(digit) != combo[-1]:
                ok = False  # 並び不一致＝レイアウト違い → この候補は不採用
                break
            v = num_range(odds)
            if v is None:
                continue  # "0.0-0.0"・"欠場" 等はその組だけスキップ（表全体は採用）
            out["-".join(map(str, combo))] = list(v) if as_range else v[0]
        if ok:
            return out if out else None
    return None


def _others(*used):
    return [b for b in range(1, 7) if b not in used]


# --- 実ページで解読済みのセル並び（2026-07-18 debug_layout で全digit一致を確認）---
# 2連複・ワイド: 対角レイアウト（大きい番号=行）: (1,2),(1,3),(2,3),(1,4),(2,4),(3,4),…
SEQ_PAIRS = [(s, L) for L in range(2, 7) for s in range(1, L)]
# 3連複: 2着グループ j → 3着 k → 1着 f の順
SEQ_TRIO = [(f, j, k) for j in range(2, 6) for k in range(j + 1, 7) for f in range(1, j)]
# 3連単: 6列（1着）等高・行優先（2着昇順×3着昇順）
SEQ_TRIFECTA = []
for _r in range(20):
    for _f in range(1, 7):
        _s = _others(_f)[_r // 4]
        _t = _others(_f, _s)[_r % 4]
        SEQ_TRIFECTA.append((_f, _s, _t))
# 2連単: 6列（1着）等高・行優先（2着昇順）
SEQ_EXACTA = [(f, _others(f)[r]) for r in range(5) for f in range(1, 7)]


def today_jcds(session, hd):
    """本日開催の場コード一覧をインデックスページから取得"""
    r = session.get(f"{BASE}/index?hd={hd}", headers=HEADERS, timeout=30)
    time.sleep(SLEEP)
    return sorted(set(re.findall(r"jcd=(\d{2})", r.text)))


def fetch_race_odds(session, jcd, rno, hd):
    """oddstf: 単勝6（数値）＋複勝6（[lo,hi] レンジ両端・v2）。開催なしは None"""
    r = session.get(f"{BASE}/oddstf?rno={rno}&jcd={jcd}&hd={hd}",
                    headers=HEADERS, timeout=30)
    time.sleep(SLEEP)
    vals = RE_ODDS.findall(r.text)
    if len(vals) < 12:
        return None  # 未発売・開催なし等
    tansho = [(v[0] if (v := num_range(x)) else None) for x in vals[0:6]]
    fukusho = [(list(v) if (v := num_range(x)) else None) for x in vals[6:12]]
    return tansho, fukusho


# 順不同券種（2連複・ワイド）のキーを昇順 "i-j" に正規化
def _norm_pairs(d):
    return {"-".join(map(str, sorted(map(int, k.split("-"))))): v for k, v in d.items()} if d else d


def fetch_race_2t2f(session, jcd, rno, hd):
    """odds2tf 1ページから 2連単30通り と 2連複15通り を取得（(exacta, quinella)・各 None あり）"""
    r = session.get(f"{BASE}/odds2tf?rno={rno}&jcd={jcd}&hd={hd}",
                    headers=HEADERS, timeout=30)
    time.sleep(SLEEP)
    text = r.text
    exacta = quinella = None
    try:
        sec = text[text.index("2連単オッズ"):text.index("2連複オッズ")]
        exacta = _reconstruct(RE_CELL.findall(sec), [SEQ_EXACTA])
    except ValueError:
        pass
    try:
        sec2 = text[text.index("2連複オッズ"):]
        quinella = _norm_pairs(_reconstruct(RE_CELL.findall(sec2), [SEQ_PAIRS]))
    except ValueError:
        pass
    return exacta, quinella


def fetch_race_3t(session, jcd, rno, hd):
    """odds3t: 3連単120通り {"i-j-k": odds}。揃わなければ None"""
    r = session.get(f"{BASE}/odds3t?rno={rno}&jcd={jcd}&hd={hd}",
                    headers=HEADERS, timeout=30)
    time.sleep(SLEEP)
    i = r.text.find("3連単オッズ")
    if i < 0:
        return None
    return _reconstruct(RE_CELL.findall(r.text[i:]), [SEQ_TRIFECTA])


def fetch_race_3f(session, jcd, rno, hd):
    """odds3f: 3連複20通り {"i-j-k": odds}（i<j<k）。揃わなければ None"""
    r = session.get(f"{BASE}/odds3f?rno={rno}&jcd={jcd}&hd={hd}",
                    headers=HEADERS, timeout=30)
    time.sleep(SLEEP)
    i = r.text.find("3連複オッズ")
    if i < 0:
        return None
    return _reconstruct(RE_CELL.findall(r.text[i:]), [SEQ_TRIO])


def fetch_race_wide(session, jcd, rno, hd):
    """oddsk: ワイド15通り {"i-j": [lo,hi]}（レンジ・i<j）。揃わなければ None"""
    r = session.get(f"{BASE}/oddsk?rno={rno}&jcd={jcd}&hd={hd}",
                    headers=HEADERS, timeout=30)
    time.sleep(SLEEP)
    i = r.text.find("拡連複オッズ")
    if i < 0:
        return None
    return _norm_pairs(_reconstruct(RE_CELL.findall(r.text[i:]), [SEQ_PAIRS], as_range=True))


def load_closes(hd):
    """リポ内 data/racelist_today.json の締切予定時刻 {"jcd-rno": "HH:MM"}（当日分のみ・T-20260718-09）"""
    try:
        j = json.loads((ROOT / "data" / "racelist_today.json").read_text(encoding="utf-8"))
        if hd in j and isinstance(j.get("closes"), dict):
            return j["closes"]
    except Exception as e:
        print(f"closes読込スキップ: {e}")
    return {}


def build_targets(jcds, hd):
    """締切が近い順のレースリストを作る（締切優先収集・T-20260718-09）。
    ・締切を40分超過したレースは除外（最終オッズは過去スイープで取得済み・liveマージで保持）
    ・締切まで FUTURE_HORIZON_MIN 分超の遠い未来レースも除外（後続連鎖で対象化・スイープ短縮＝§8.4）
    ・締切情報が無いレース（closes未配布の朝など）は従来のローテーションで末尾に"""
    closes = load_closes(hd)
    now = now_jst()
    now_min = now.hour * 60 + now.minute
    pri, rest = [], []
    for jcd in jcds:
        for rno in range(1, 13):
            c = closes.get(f"{jcd}-{rno}")
            if c:
                try:
                    hh, mm = c.split(":")
                    delta = int(hh) * 60 + int(mm) - now_min
                except ValueError:
                    rest.append((jcd, rno))
                    continue
                if delta < -40 or delta > FUTURE_HORIZON_MIN:
                    continue  # 締切-40分超=取得済み / 締切まで遠い=まだ不要。後続連鎖で対象化（§8.4）
                pri.append((delta, jcd, rno))
            else:
                rest.append((jcd, rno))
    pri.sort()
    if rest:
        rot = ((now.hour * 4 + now.minute // 15) * 12) % len(rest)
        rest = rest[rot:] + rest[:rot]
    if closes:
        print(f"締切優先: {len(pri)}レース（締切-40分〜+{FUTURE_HORIZON_MIN}分の近接のみ）＋締切情報なし {len(rest)}レース", flush=True)
    else:
        print(f"closes情報なし→従来ローテーション（{len(rest)}レース）", flush=True)
    return [(j, r) for _, j, r in pri] + rest


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

    t0 = time.monotonic()
    races = load_live_base(hd)
    jcds = today_jcds(requests.Session(), hd)
    print(f"開催 {len(jcds)}場・{WORKERS}並列", flush=True)
    targets = build_targets(jcds, hd)

    def fetch_one(jcd, rno):
        """1レース分（単複/2連単/2連複/3連単/3連複/ワイド＝5ページ）を取得。戻り値 (key, entry) or None"""
        try:
            got = fetch_race_odds(_session(), jcd, rno, hd)
            if not got:
                return None  # 未発売等 → live ベース値があればそのまま残る
            entry = {"at": now_jst().strftime("%H:%M"),
                     "tansho": got[0], "fukusho": got[1]}
            ex, qn = fetch_race_2t2f(_session(), jcd, rno, hd)
            for k2, v in (("exacta", ex), ("quinella", qn),
                          ("trifecta", fetch_race_3t(_session(), jcd, rno, hd)),
                          ("trio", fetch_race_3f(_session(), jcd, rno, hd)),
                          ("wide", fetch_race_wide(_session(), jcd, rno, hd))):
                if v:
                    entry[k2] = v
            return (f"{jcd}-{rno}", entry)
        except Exception as e:
            print(f"{jcd}-{rno} ERROR {e}", flush=True)
            return None

    COMBO_KEYS = ("exacta", "quinella", "trifecta", "trio", "wide")
    n_ok = n_full = done = 0
    stopped = False
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for i in range(0, len(targets), 12):  # 締切が近い順に12レースずつ処理（T-20260718-09）
            if time.monotonic() - t0 > DEADLINE_SEC:
                stopped = True
                break
            chunk = targets[i:i + 12]
            for res in pool.map(lambda t: fetch_one(t[0], t[1]), chunk):
                if not res:
                    continue
                key, entry = res
                prev = races.get(key) if isinstance(races.get(key), dict) else None
                for k2 in COMBO_KEYS:  # 今回だけ取れなかった券種は前回値を維持
                    if k2 not in entry and prev and k2 in prev:
                        entry[k2] = prev[k2]
                if all(k2 in entry for k2 in COMBO_KEYS):
                    n_full += 1
                races[key] = entry
                n_ok += 1
            done += len(chunk)
            print(f"進捗 {done}/{len(targets)}（経過 {int(time.monotonic() - t0)}秒）", flush=True)

    data = {"schema": 2, "date": hd,
            "fetched_at": now_jst().isoformat(timespec="seconds"),
            "races": races}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    cut = "（⏱時間上限で途中打切り→残りは次スイープで補完）" if stopped else ""
    print(f"{hd}: 場={len(jcds)} 更新={n_ok}レース(全券種そろい {n_full}) 合計={len(races)}レース{cut} → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
