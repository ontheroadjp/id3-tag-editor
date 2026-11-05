# テスト

`tests/test_app.py` はディレクトリ一覧、タグと画像、外部パス拒否、Range 応答、日本語名、M4A を検証する。M4A テストは ffmpeg がない場合スキップする（根拠: tests/test_app.py:38-101）。

`tests/test_lookup.py` は照合の模擬応答、リトライ、頻度制限、中止、候補選択・検索・言語絞り込みを検証する。画面ロジックのテストは Node.js がなければスキップする（根拠: tests/test_lookup.py:21-282）。

`pip install '.[test]'` の後に `pytest -q` を実行する（根拠: README.md:27、pyproject.toml:7-11）。カバレッジ基準は未定義。
