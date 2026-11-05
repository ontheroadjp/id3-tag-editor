# プロジェクト概要

MP3 と M4A を閲覧・再生するローカル Web アプリ。ファイル一覧、既存タグとアートワークの表示、音声配信に加え、音声指紋からオンラインの録音・リリース候補を取得して比較する（根拠: app/main.py:23-51,85-146、app/lookup.py:96-152）。

Python 3.12 以上、FastAPI、Uvicorn、mutagen、Chromaprint を使用する（根拠: pyproject.toml:1-8、Dockerfile:1-9）。Docker Compose は同じ PC に公開し、音楽ディレクトリを読み取り専用にする（根拠: compose.yaml:1-16）。

タグの更新と認証は現時点の実装にない。将来の仕様は未確定（根拠: app/main.py:23-146）。
