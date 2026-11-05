# 開発・運用

`MUSIC_DIR` を設定し `docker compose up --build` で起動、`docker compose down` で停止する（根拠: README.md:13-20、compose.yaml:1-15）。音楽ディレクトリは読み取り専用。

Python のテスト依存関係は `pip install '.[test]'` で導入し、`pytest -q` で実行する（根拠: README.md:22、pyproject.toml:7-11）。CI 定義は存在しない。
