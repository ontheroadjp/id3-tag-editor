# リポジトリ構成

- `app/main.py` は API、`app/lookup.py` は照合、`app/tag_editor.py` はタグ編集と SQLite 履歴を実装する（根拠: app/main.py:250-409、app/lookup.py:75-157、app/tag_editor.py:309-443,879-974）。
- `app/static/` はブラウザ画面、`tests/` は API・編集・照合のテストを含む（根拠: app/static/index.html:1-17、tests/test_app.py、tests/test_tag_editor.py、tests/test_lookup.py）。
- `Dockerfile`、`compose.yaml`、`pyproject.toml` は実行環境・起動設定・依存関係を定義する（根拠: Dockerfile:1-9、compose.yaml:1-23、pyproject.toml:1-11）。
- `docs/` に設計と運用資料を置く。
