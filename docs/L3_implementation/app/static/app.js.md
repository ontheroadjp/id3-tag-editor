# app/static/app.js

- 一覧・プレーヤー・既存タグと取得したタグを表示し、候補を選択して編集できる（根拠: app/static/app.js:630-763,831-1050）。
- 曲名とコメント以外の入力値と画像をプリセットとして保存・読み出し・削除する。読み出しは編集画面に反映し、音楽ファイルの保存は別操作とする（根拠: app/static/app.js:37-127）。
- フィールドと画像の反映対象を選び、保存とリセットを操作する。変更中の画面遷移には確認を挟む（根拠: app/static/app.js:170-395,452-503,768-787）。
- 比較表の末尾に画像の選択・反映・既存画像を配置し、取得またはアップロードした画像を入力欄にプレビューする（根拠: app/static/app.js:303-395,785-799）。
- 保存履歴の表示・復元・削除と、未完了の復旧操作を扱う（根拠: app/static/app.js:504-629）。

## 変更履歴（git log より自動生成）

- 8f18d7f feat(#10): save and apply named tag presets
- fffe586 feat(#8): support safe tag editing and history restoration (#9)
- ebe4570 feat(#4): add metadata lookup and comparison (#5)
- a0ddcbe feat(#2): add read-only music browsing and playback (#3)
