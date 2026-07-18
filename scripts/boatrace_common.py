# 設計: BoatRacePredictor 20_設計/09_公開サイト自動収集設計.md §4
# 共通定義: 会場名対照表・User-Agent・JST時刻・番組表B(mbrace)の取得と解析。
# parse_b は phase2/parse_load.py から逐語移植（純関数・DB非依存）。
# GitHub Actions は UTC で動くため、日付は必ず now_jst() を使うこと（設計09 §6-2）。

import re
import time
from datetime import datetime, timedelta, timezone

try:  # Actions(ubuntu)はOSのtzデータ、WindowsローカルはUTC+9固定にフォールバック（日本はDSTなし）
    from zoneinfo import ZoneInfo
    JST = ZoneInfo("Asia/Tokyo")
except Exception:
    JST = timezone(timedelta(hours=9), "JST")

UA = {"User-Agent": "BoatRacePredictor/0.1 (personal study tool)"}
SLEEP_ODDS = 2.0     # boatrace.jp のリクエスト間隔（負荷防止・phase2/odds_fetch.py と同値）
SLEEP_MBRACE = 3.0   # mbrace.or.jp のリクエスト間隔（phase2/download.py と同値）

MBRACE_BASE = "http://www1.mbrace.or.jp/od2"
BOATRACE_BASE = "https://www.boatrace.jp/owpc/pc/race"

JCD_NAMES = {"01": "桐生", "02": "戸田", "03": "江戸川", "04": "平和島", "05": "多摩川",
             "06": "浜名湖", "07": "蒲郡", "08": "常滑", "09": "津", "10": "三国",
             "11": "びわこ", "12": "住之江", "13": "尼崎", "14": "鳴門", "15": "丸亀",
             "16": "児島", "17": "宮島", "18": "徳山", "19": "下関", "20": "若松",
             "21": "芦屋", "22": "福岡", "23": "唐津", "24": "大村"}


def now_jst():
    return datetime.now(JST)


def fetch_b_text(session, ymd: str):
    """番組表B(LZH)を取得して cp932 テキストを返す。未提供(404)は None。"""
    import lhafile  # pure Python（requirements.txt）
    import tempfile
    from pathlib import Path

    stem = f"b{ymd[2:]}"
    url = f"{MBRACE_BASE}/B/{ymd[:6]}/{stem}.lzh"
    r = session.get(url, headers=UA, timeout=30)
    time.sleep(SLEEP_MBRACE)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=".lzh", delete=False) as tmp:
        tmp.write(r.content)
        tmp_path = Path(tmp.name)
    try:
        lf = lhafile.Lhafile(str(tmp_path))
        data = b"".join(lf.read(info.filename) for info in lf.infolist())
        try:  # Windows は開いたままだと削除できない（WinError32）ため先に閉じる
            lf.fp.close()
        except Exception:
            pass
    finally:
        try:  # 一時ファイル削除は best-effort（失敗してもパイプは落とさない）
            tmp_path.unlink()
        except OSError:
            pass
    return data.decode("cp932", errors="replace")


# --- B（番組表）解析: phase2/parse_load.py から逐語移植 ---------------------
ZEN2HAN = str.maketrans("０１２３４５６７８９", "0123456789")
RE_B_VENUE = re.compile(r"^(\d{2})BBGN")
RE_B_RACER = re.compile(
    r"^([1-6]) (\d{4})(.{4})(\d{2})(.{2})(\d{2})([AB][12])(.*)$")


def parse_b(text: str, ymd: str):
    races, entries = [], []
    jcd = rno = None
    for raw in text.splitlines():
        m = RE_B_VENUE.match(raw)
        if m:
            jcd = m.group(1)
            continue
        if jcd is None:
            continue
        line = raw.translate(ZEN2HAN)
        m = RE_B_RACER.match(line)
        if m and rno is not None:
            lane, regno, name, _age, _branch, _wt, cls, rest = m.groups()
            # 固定桁で切り出す（2連率が100.00のとき番号と桁詰まりで連結するため split 不可）
            try:
                nat = float(rest[0:5])
                nat2 = float(rest[5:11])
                loc = float(rest[11:16])
                loc2 = float(rest[16:22])
                mno = int(rest[22:25])
                m2 = float(rest[25:31])
                bno = int(rest[31:34])
                b2 = float(rest[34:40])
            except (ValueError, IndexError):
                continue  # 数値列が崩れた行はスキップ
            rid = f"{ymd}-{jcd}-{rno}"
            entries.append((rid, int(lane), int(regno),
                            name.replace("　", "").strip(), cls,
                            nat, nat2, loc, loc2, mno, m2, bno, b2))
            continue
        m = re.match(r"^[　\s]*(\d{1,2})Ｒ?[RＲ]?\s+(\S+)", line)
        if m and ("R" in line[:8] or "Ｒ" in raw[:8]):
            rno = int(m.group(1))
            title = m.group(2)
            dm = re.search(r"[HＨ](\d{3,4})[mｍ]", line)
            dist = int(dm.group(1)) if dm else None
            races.append((f"{ymd}-{jcd}-{rno}", f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}",
                          jcd, rno, title, dist))
    return races, entries
