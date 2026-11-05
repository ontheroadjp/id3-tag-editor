# id3-tag-editor

## Features

MP3・M4A フォルダの閲覧、既存タグ・アートワークの表示、ブラウザでの再生に対応します。音楽ファイルは読み取り専用で扱います。

## Installation

Docker と Docker Compose を用意してください。

## Usage

音楽フォルダの絶対パスを指定して起動します。

```sh
export MUSIC_DIR="/path/to/music"
docker compose up --build
```

同じ PC で http://127.0.0.1:18081/ を開きます。停止するには `docker compose down` を実行します。フォルダ内の MP3・M4A と子フォルダを表示します。パスに空白や日本語が含まれる場合も、上記のように引用符で囲んでください。リンク先が音楽フォルダの外にあるシンボリックリンクは表示しません。

テストは Python 3.12 以上で `pip install '.[test]'` の後、`pytest -q` で実行できます。

## Design Principles

ファイルの場所や名前を変更せず、読み取り時に音楽ファイルを書き換えません。現時点では同じ PC からの利用を想定し、外部公開には対応していません。
