# プロジェクト概要

MP3・M4A の閲覧、再生、音声指紋による候補取得、タグ・画像の比較編集と履歴復元を提供するローカル Web アプリ（根拠: app/main.py:250-409,473-559、app/lookup.py:75-157）。Python 3.12 以上、FastAPI、Mutagen、Chromaprint を使用する（根拠: pyproject.toml:1-11、Dockerfile:1-9）。書き込みは既定で無効であり、SQLite 履歴は Compose ボリュームに保持する（根拠: compose.yaml:5-23、app/main.py:24-28,182-184）。

共通のタグ入力値と画像を名前付きプリセットとして再利用できる（根拠: app/main.py:74-166、app/static/app.js:40-124）。
