# app/main.py

- FastAPI でファイル一覧、音声、タグ・画像の読み取りと照合操作を提供する（根拠: app/main.py:411-435,473-559）。
- 名前付きプリセットを SQLite に保存し、タグと検証済み画像の一覧・作成・更新・削除を提供する。音楽ファイルの書き込み設定とは独立してローカル origin を検証する（根拠: app/main.py:74-168）。
- タグ保存、SQLite 履歴の一覧・復元・削除、および中断時の復旧を tag_editor に委譲する（根拠: app/main.py:250-409）。
- 変更操作はローカルの Host・Origin と書き込み設定を検証し、ファイルのバージョンを使って画面表示後の変更を検出する（根拠: app/main.py:44-67,112-119,270-298）。
- MP3 は ID3、M4A は MP4 タグを読み、複数のコメントとアートワークを区別する（根拠: app/main.py:457-470,513-559）。

## 変更履歴（git log より自動生成）

- 8f18d7f feat(#10): save and apply named tag presets
- fffe586 feat(#8): support safe tag editing and history restoration (#9)
- ebe4570 feat(#4): add metadata lookup and comparison (#5)
- a0ddcbe feat(#2): add read-only music browsing and playback (#3)
