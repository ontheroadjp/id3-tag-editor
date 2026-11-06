# app/main.py

- FastAPI でファイル一覧、音声、タグ・画像の読み取りと照合操作を提供する（根拠: app/main.py:325-482）。
- タグ保存、SQLite 履歴の一覧・復元・削除、および中断時の復旧を tag_editor に委譲する（根拠: app/main.py:164-322）。
- 変更操作はローカルの Host・Origin と書き込み設定を検証し、ファイルのバージョンを使って画面表示後の変更を検出する（根拠: app/main.py:43-94,184-211）。
- MP3 は ID3、M4A は MP4 タグを読み、複数のコメントとアートワークを区別する（根拠: app/main.py:371-385,427-451）。

## 変更履歴（git log より自動生成）

- 9fd8407 feat(#8): support safe tag editing and history restoration
- ebe4570 feat(#4): add metadata lookup and comparison (#5)
- a0ddcbe feat(#2): add read-only music browsing and playback (#3)
