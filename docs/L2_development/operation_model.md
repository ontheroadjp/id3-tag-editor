# 開発・運用

`MUSIC_DIR` とタグ取得用の `ACOUSTID_API_KEY` を設定し `docker compose up --build` で起動、`docker compose down` で停止する（根拠: README.md:15-25、compose.yaml:1-16）。キーがなくても閲覧・再生でき、音楽ディレクトリは読み取り専用。

Python のテスト依存関係は `pip install '.[test]'` で導入し、`pytest -q` で実行する（根拠: README.md:27、pyproject.toml:7-11）。CI 定義は存在しない。
