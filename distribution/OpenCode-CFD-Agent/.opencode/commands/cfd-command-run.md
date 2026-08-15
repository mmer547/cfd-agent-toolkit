---
description: 登録済みCFD utilityを1つ実行する
---

`cfd-case-agent` skillをロードしてください。対象ケース`$1`と登録済みcommand ID `$2`について、
最初に共有controllerの`--workspace . --case <case> run-command <command-id>`を
`--execute`なしでpreviewし、provider、直列・並列、実行commandを
示してください。その後、同じ`run-command`へ`--execute`を付けて1回だけ実行してください。

OpenFOAMのインストールや別のbashrcを探索しないでください。失敗時は追加のシェル調査をせず、
設定されたdistributionとbashrc、controllerのログパス、終了コード、ログ末尾のエラーを報告して
停止してください。utilityや`wsl.exe`を直接呼び出さないでください。
