# リポジトリ構成

- `.gitignore` はインストールされた toolkit のディレクトリなどを Git の追跡対象から除外します（`.gitignore:1-7`）。
- `AGENTS.md` は AI エージェント向けの作業手順を記述しています（`AGENTS.md:1-12`）。
- `README.md` は現時点の機能と起動方法を示します（`README.md:1-31`）。
- `docs/` はこの初期化で作成したドキュメントを保持します。

- `app/main.py` は API と音声配信、`app/lookup.py` は指紋照合と外部データ取得を実装し、`app/static/` はブラウザ画面を構成する（根拠: app/main.py:23-51,85-146、app/lookup.py:75-152、app/static/index.html:1-15）。
- `tests/` は一覧、音声配信、照合や候補選択のテストを含む（根拠: tests/test_app.py:38-101、tests/test_lookup.py:21-282）。
- `Dockerfile`、`compose.yaml`、`pyproject.toml` はそれぞれコンテナ、実行構成、Python 依存関係を定義する（根拠: Dockerfile:1-9、compose.yaml:1-16、pyproject.toml:1-11）。
