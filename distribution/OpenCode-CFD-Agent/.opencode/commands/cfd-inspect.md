---
description: 指定CFDケースとprovider・workflowを安全に点検する
---

`cfd-case-agent` skillをロードしてください。対象ケースは`$1`です。空の場合は
`discover-cases --root .`だけを実行し、候補を示して停止してください。

対象が指定されたら、共有controllerを
`python .agents/skills/cfd-case-agent/scripts/cfdctl.py --workspace . --case <case> inspect`
の形で呼び出してください。続いて`list-commands`と`plan mesh`、`plan solve`、
`plan rerun`をdry-runしてください。検出したvendor/version、profile、provider、
decomposePar辞書、直列・並列、blocking_issuesをユーザーの言語で説明してください。
`--execute`を付けず、CFD processを起動しないでください。
