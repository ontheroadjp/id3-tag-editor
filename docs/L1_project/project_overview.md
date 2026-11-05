# プロジェクト概要

MP3 と M4A を閲覧・再生するローカル Web アプリ。ファイル一覧、既存タグとアートワークの表示、音声配信を実装する（根拠: app/main.py:53-114、app/static/app.js:15-69）。

Python 3.12 以上、FastAPI、Uvicorn、mutagen を使用する（根拠: pyproject.toml:1-8）。Docker Compose は同じ PC に公開し、音楽ディレクトリを読み取り専用にする（根拠: compose.yaml:1-15）。

タグのオンライン取得・更新と認証は現時点の実装にない。将来の仕様は未確定。
