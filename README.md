# id3-tag-editor

## Features

MP3・M4A フォルダの閲覧、既存タグ・アートワークの表示、ブラウザでの再生に対応します。音楽ファイルは読み取り専用で扱います。

音声指紋から AcoustID と MusicBrainz の候補を取得し、選択したリリースと既存タグを比較できます。取得時は音声指紋と再生時間が AcoustID に送信されます。

## Installation

Docker と Docker Compose を用意してください。

## Usage

音楽フォルダの絶対パスを指定して起動します。

```sh
export MUSIC_DIR="/path/to/music"
export ACOUSTID_API_KEY="取得したAPIキー"
docker compose up --build
```

同じ PC で http://127.0.0.1:18081/ を開きます。停止するには `docker compose down` を実行します。フォルダ内の MP3・M4A と子フォルダを表示します。パスに空白や日本語が含まれる場合も、上記のように引用符で囲んでください。リンク先が音楽フォルダの外にあるシンボリックリンクは表示しません。

タグ取得には [AcoustID のアプリケーション API キー](https://acoustid.org/new-application) が必要です。未設定でもファイルの閲覧・再生は利用できます。曲を選択し「タグ情報を取得」を押した後、候補からリリースを選択してください。「取得情報の言語」は MusicBrainz のリリースに登録された言語で候補を絞り込み、テキストの翻訳は行いません。言語が登録されていない版は「言語未登録」で確認できます。音楽ファイルは変更されません。

テストは Python 3.12 以上で `pip install '.[test]'` の後、`pytest -q` で実行できます。

## Design Principles

ファイルの場所や名前を変更せず、読み取り時に音楽ファイルを書き換えません。現時点では同じ PC からの利用を想定し、外部公開には対応していません。
