---
description: 指定ケースの登録済み計算workflowを実行する
---

`cfd-case-agent` skillをロードしてください。対象ケースは`$1`です。最初に共有controllerの
`--workspace . --case <case> plan solve`をdry-runし、provider/version、solver、初期化工程、
直列・並列、blocking_issuesを説明してください。readyなら`run-workflow solve --execute`を
1回だけ呼び出してください。登録utilityや`wsl.exe`を直接実行しないでください。
