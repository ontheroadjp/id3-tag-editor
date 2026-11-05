# API

- `GET /` は画面を返す。
- `GET /api/files?directory=<id>` はディレクトリ名、親 ID、子エントリを返す。
- `GET /api/files/{id}` はファイル名、タグ、アートワークの有無を返す。
- `GET /api/files/{id}/audio` は Range に対応した音声を返す。
- `GET /api/files/{id}/artwork` は埋め込み画像を返す。
- `POST /api/files/{id}/lookup` は照合ジョブを開始し ID を返す。
- `GET /api/lookups/{job_id}` はジョブの状態・候補・警告を返し、`DELETE /api/lookups/{job_id}` は処理を中止する。
- `GET /api/recordings/{recording_id}/releases/{release_id}` は選択した版のタグ候補と画像 URL を返す。

ID は音楽ルート相対パスを URL 用 Base64 にしたもので、解決先をルート内に限定する（根拠: app/main.py:23-66,85-146）。認証と更新 API は実装されていない。
