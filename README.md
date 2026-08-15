# CFD agent package project

このリポジトリでは、OpenCodeからWindows Pythonのcontrollerを経由して、WSL上の
OpenFOAMまたは互換CFD utilityを実行する配布物を管理します。

## フォルダ構成

```text
distribution/OpenCode-CFD-Agent/  利用者が計算作業フォルダへ1回コピーする配布物
tests/                            開発時だけ使用する自動テスト
development/                      配布しないエージェント用メタデータ
```

利用手順は[OPENCODE手順.md](OPENCODE手順.md)を参照してください。利用者へ渡すのは
`distribution/OpenCode-CFD-Agent`の中身だけです。各ケースへの個別コピーは不要です。
