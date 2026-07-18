# 設計: BoatRacePredictor 20_設計/09_公開サイト自動収集設計.md §6-1（Phase 0 Go/No-Go 判定）
# GitHub Actions ランナーから boatrace.jp / mbrace.or.jp へ到達できるか・lhafile が動くかを検証する。
# すべて OK なら exit 0、1つでも NG なら exit 1（→プランB: PC側収集+push 方式へ切替）。
# 使い方: python scripts/probe.py

import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from boatrace_common import BOATRACE_BASE, UA, fetch_b_text, now_jst, parse_b

RE_ODDS = re.compile(r'oddsPoint[^"]*">([^<]+)<')


def main():
    hd = now_jst().strftime("%Y%m%d")
    print(f"probe対象日(JST): {hd}")
    ok = True
    session = requests.Session()

    # 1) boatrace.jp インデックス（開催場一覧）
    try:
        r = session.get(f"{BOATRACE_BASE}/index?hd={hd}", headers=UA, timeout=30)
        jcds = sorted(set(re.findall(r"jcd=(\d{2})", r.text)))
        print(f"[1] boatrace.jp index: HTTP {r.status_code} / 開催場 {len(jcds)} {jcds}")
        if r.status_code != 200 or not jcds:
            ok = False
    except Exception as e:
        print(f"[1] boatrace.jp index: NG {e}")
        ok = False
        jcds = []

    # 2) boatrace.jp oddstf（先頭場の1R）
    if jcds:
        try:
            r = session.get(f"{BOATRACE_BASE}/oddstf?rno=1&jcd={jcds[0]}&hd={hd}",
                            headers=UA, timeout=30)
            vals = RE_ODDS.findall(r.text)
            print(f"[2] oddstf {jcds[0]}-1: HTTP {r.status_code} / oddsPoint {len(vals)}個")
            if r.status_code != 200:
                ok = False
            # 発売前は12個未満もあり得るためHTTP 200のみ必須とする
        except Exception as e:
            print(f"[2] oddstf: NG {e}")
            ok = False

    # 3) mbrace 番組表B + lhafile 展開 + parse_b
    try:
        text = fetch_b_text(session, hd)
        if text is None:
            print("[3] mbrace B: 404（当日未提供。時間帯によっては正常）")
        else:
            n_venue = len(re.findall(r"^\d{2}BBGN", text, re.M))
            _races, entries = parse_b(text, hd)
            print(f"[3] mbrace B: 取得OK / {n_venue}会場 / entries {len(entries)}件")
            if not entries:
                ok = False
    except Exception as e:
        print(f"[3] mbrace B / lhafile: NG {e}")
        ok = False

    print("== probe:", "ALL OK" if ok else "NG あり（プランB検討）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
