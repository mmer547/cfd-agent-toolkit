---
description: 指定ケースの登録済みメッシュworkflowを実行する
---

`cfd-case-agent` skillをロードしてください。対象ケースは`$1`です。最初に共有controllerの
`--workspace . --case <case> plan mesh`をdry-runし、provider/version、全工程、並列数、
削除・保持対象、blocking_issuesを説明してください。blocking issueがあれば実行せず停止してください。
登録済みtutorial resourceのコピーがある場合は、Allrunが参照のみであること、固定コピー元と
ケース内コピー先、既存ファイルは上書きしないことを承認前に説明してください。

readyなら同じcontrollerの`run-workflow mesh --execute`を1回だけ呼び出してください。
utilityや`wsl.exe`を直接呼ばず、完了時はログとcheckMesh結果を報告してください。
