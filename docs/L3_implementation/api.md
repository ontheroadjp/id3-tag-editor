# API

- `GET /api/files`、`GET /api/files/{id}`、`GET /api/files/{id}/audio`、`GET /api/files/{id}/artwork` はファイル・タグ・音声・画像を返す（根拠: app/main.py:392-482）。
- `POST /api/files/{id}/tags` は選択されたタグと画像を保存する（根拠: app/main.py:43-65,184-211）。
- `GET /api/files/{id}/history` と `POST /api/files/{id}/history/{backup_id}/restore` は履歴と復元を提供する。履歴削除、未完了処理の一覧、復旧とコピー整理の API もある（根拠: app/main.py:164-182,214-322）。
- 照合ジョブの開始・状態・中止と選択されたリリースの取得を提供する（根拠: app/main.py:325-354）。
- 更新要求はローカル Host・Origin と書き込み設定を確認する（根拠: app/main.py:73-80）。
