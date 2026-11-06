# テスト

`tests/test_app.py` は閲覧、API 経由の保存・履歴復元と復旧を検証し、`tests/test_tag_editor.py` は MP3・M4A の編集、SQLite の履歴、失敗時の復元と入力検証を扱う。`tests/test_lookup.py` は照合と候補選択を検証する。ffmpeg や Node.js が必要なテストはコマンドがない場合スキップされる（根拠: tests/test_app.py、tests/test_tag_editor.py、tests/test_lookup.py）。

`pip install '.[test]'` の後に `pytest -q` を実行する（根拠: pyproject.toml:7-11、README.md）。カバレッジ基準は未定義。
