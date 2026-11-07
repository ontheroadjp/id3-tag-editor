# API

- `GET /api/files`、`GET /api/files/{id}`、`GET /api/files/{id}/audio`、`GET /api/files/{id}/artwork` はファイル・タグ・音声・画像を返す（根拠: app/main.py:478-559）。
- `POST /api/files/{id}/tags` は選択されたタグと画像を保存する（根拠: app/main.py:44-67,270-298）。
- `GET /api/files/{id}/history` と `POST /api/files/{id}/history/{backup_id}/restore` は履歴と復元を提供する。履歴削除、未完了処理の一覧、復旧とコピー整理の API もある（根拠: app/main.py:250-268,300-409）。
- 照合ジョブの開始・状態・中止と選択されたリリースの取得を提供する（根拠: app/main.py:411-439）。
- `GET /api/presets` は一覧を返し、`POST`・`PUT`・`DELETE /api/presets` は名前付きプリセットを作成・更新・削除する。曲名とコメントを除くタグと検証済み画像を受け付ける（根拠: app/main.py:74-110,122-166）。
- 更新要求はローカル Host・Origin を確認する。音楽ファイルの変更には書き込み設定も必要だが、プリセット操作には不要である（根拠: app/main.py:112-119,129-166,270-276）。
