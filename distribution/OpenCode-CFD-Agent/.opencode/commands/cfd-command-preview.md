---
description: 登録済みCFD utilityを1つdry-runする
---

`cfd-case-agent` skillをロードしてください。対象ケース`$1`と登録済みcommand ID `$2`を
共有controllerの`--workspace . --case <case> run-command <command-id>`に渡し、
`--execute`なしで展開結果を示してください。IDが未登録なら実行せず、
`list-commands`の候補を示してください。utilityを直接呼び出さないでください。
