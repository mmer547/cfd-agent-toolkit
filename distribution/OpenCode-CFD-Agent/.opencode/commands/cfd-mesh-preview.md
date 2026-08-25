---
description: 指定ケースのメッシュworkflowをdry-runする
---

`cfd-case-agent` skillをロードしてください。対象ケース`$1`に対して共有controllerの
`--workspace . --case <case> plan mesh`を実行してください。provider/version、メッシュ工程、
並列数、代替decomposePar辞書、skipped_steps、blocking_issuesを説明してください。このcommandは
workflow構成のpreviewであり、メッシュ調整のセル上限を推測しないでください。
`internal.stageResources`が含まれる場合は、参照したAllrun、固定コピー元、ケース内コピー先、
不足時だけコピーすることも説明してください。Allrun自体は実行しないでください。
`--execute`を付けず、ケース辞書を変更しないでください。
