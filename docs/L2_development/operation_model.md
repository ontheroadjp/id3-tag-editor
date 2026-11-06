# 開発・運用

`MUSIC_DIR` と必要に応じて `ACOUSTID_API_KEY` を指定し、`docker compose up --build` で起動、`docker compose down` で停止する（根拠: README.md、compose.yaml:1-23）。既定では音楽フォルダを読み取り専用で扱う。書き込みには `ENABLE_TAG_WRITES=1` と `MUSIC_READ_ONLY=false` の両方が必要（根拠: compose.yaml:9-17、app/main.py:27,73-80）。SQLite 履歴とログは `app-data` ボリュームに保存する（根拠: compose.yaml:7-8,19-23、app/main.py:96-104）。

テスト依存関係は `pip install '.[test]'` で導入し `pytest -q` で実行する（根拠: pyproject.toml:7-11、README.md）。CI 定義は確認されていない。
