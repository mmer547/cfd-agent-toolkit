---
description: 複数CFDケースのworkflowを一括dry-runする
---

`cfd-case-agent` skillをロードしてください。引数`$ARGUMENTS`の先頭をworkflow、残りを
ケースパスとして解釈してください。共有controllerの
`--workspace . batch-plan <workflow> <case...>`を1回呼び出し、ケース別のvendor/version、
provider、工程、並列数、ready/blocked/errorを説明してください。1件でもblockedなら、
全件未実行であることを明示してください。
指示がなければ`max_concurrent_jobs = 1`で指定順に1ケースずつ実行することと、各ケース内部の
MPI並列はdecomposeParDictに従うことを説明してください。
