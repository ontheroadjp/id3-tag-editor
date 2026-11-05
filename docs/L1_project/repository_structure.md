# リポジトリ構成

- `.gitignore` はインストールされた toolkit のディレクトリなどを Git の追跡対象から除外します（`.gitignore:1-7`）。
- `AGENTS.md` は AI エージェント向けの作業手順を記述しています（`AGENTS.md:1-12`）。
- `README.md` は現時点の機能と未確定事項を示します（`README.md:1-17`）。
- `docs/` はこの初期化で作成したドキュメントを保持します。

- `app/main.py` は API と音声配信を実装し、`app/static/` はブラウザ画面を構成する（根拠: app/main.py:18-19,53-114、app/static/index.html:1-14）。
- `tests/` は一覧・タグ・アートワーク・音声配信などのテストを含む（根拠: tests/test_app.py:38-101）。
- `Dockerfile`、`compose.yaml`、`pyproject.toml` はそれぞれコンテナ、実行構成、Python 依存関係を定義する（根拠: Dockerfile:1-8、compose.yaml:1-15、pyproject.toml:1-11）。
