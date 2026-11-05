# API

- `GET /` は画面を返す。
- `GET /api/files?directory=<id>` はディレクトリ名、親 ID、子エントリを返す。
- `GET /api/files/{id}` はファイル名、タグ、アートワークの有無を返す。
- `GET /api/files/{id}/audio` は Range に対応した音声を返す。
- `GET /api/files/{id}/artwork` は埋め込み画像を返す。

ID は音楽ルート相対パスを URL 用 Base64 にしたもので、解決先をルート内に限定する（根拠: app/main.py:22-34,53-114）。認証と更新 API は実装されていない。
