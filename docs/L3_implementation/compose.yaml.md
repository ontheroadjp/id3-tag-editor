# compose.yaml

- アプリをローカル PC に公開し、音楽ディレクトリを既定で読み取り専用にする。書き込みの有効化は環境変数で制御する（根拠: compose.yaml:1-17）。
- アプリデータは名前付きボリュームに保持する（根拠: compose.yaml:19-23）。

## 変更履歴（git log より自動生成）

- 9fd8407 feat(#8): support safe tag editing and history restoration
- ebe4570 feat(#4): add metadata lookup and comparison (#5)
- a0ddcbe feat(#2): add read-only music browsing and playback (#3)
