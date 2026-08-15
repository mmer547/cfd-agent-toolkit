---
description: 複数CFDケースのworkflowを安全に順次実行する
---

`cfd-case-agent` skillをロードしてください。引数`$ARGUMENTS`の先頭をworkflow、残りを
ケースパスとして解釈してください。最初に`--workspace . batch-plan <workflow> <case...>`を
実行して全ケースを説明してください。全件readyの場合だけ、同じ並びで
`--workspace . batch-run <workflow> <case...> --execute`を1回呼び出してください。
ユーザーから別の実行方針が明示されていなければ、指定順にケース単位で1 JOBずつ逐次実行してください。
JOB Nが正常終了してからJOB N+1を開始し、失敗時は後続JOBを開始せず停止してください。同一ケース内の
MPI並列数はそのケースのdecomposeParDictに従います。ケースの並行起動やjob schedulingは行わないでください。
