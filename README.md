# boatrace-app 🚤

競艇予測アプリの**公開サイト**（GitHub Pages）と**データ自動収集**（GitHub Actions）。

[![racelist](https://github.com/ayathyyy/boatrace-app/actions/workflows/racelist.yml/badge.svg)](https://github.com/ayathyyy/boatrace-app/actions/workflows/racelist.yml)
[![odds](https://github.com/ayathyyy/boatrace-app/actions/workflows/odds.yml/badge.svg)](https://github.com/ayathyyy/boatrace-app/actions/workflows/odds.yml)

- **アプリ**: https://ayathyyy.github.io/boatrace-app/
- **🧮 券種別分析ダッシュボード**: https://ayathyyy.github.io/boatrace-app/analysis.html （毎晩22:30台に自動更新・履歴蓄積）
- 会場・レースを選ぶだけで、当日の**選手情報＋オッズ（全券種）**が自動入力される。

## 仕組み

| workflow | タイミング(JST) | 内容 |
|---|---|---|
| racelist | 毎朝 08:18（09:32 リトライ） | 公式公開データの当日番組表B → `data/racelist_today.json`（コミット） |
| odds | 09:07〜21:37 の30分おき | 全会場・全レースのオッズ → `data/odds_today.json`（コミットせずデプロイのみ） |
| pages | main への push 時 | サイト再デプロイ（アプリ更新用） |

- アプリは選択のたびに `data/odds_today.json` を再取得（レースごとの取得時刻を表示）。
- 収集間隔・待機（2〜3秒/リクエスト）は控えめに設定（サーバー負荷への配慮）。

## 運用メモ

- **fan_st.json**（平均ST補完・約70KB）は PC 側 DB から `export_fan_st.py` で生成してコミット。
  **期替わり（4月・10月）に再生成すること**。
- 開発の本体（設計書・バックテスト・EV検証）は別リポ（Private）: `ayathyyy/BoatRacePredictor`
- ※個人的な学習・研究目的のツールです。公式データの利用は各サイトの規約に従ってください。
