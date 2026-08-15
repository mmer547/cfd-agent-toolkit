---
description: 外部scheduler用のsolver-only Allrunを生成する
---

`cfd-case-agent` skillをロードしてください。対象ケースは`$1`です。mesh optimizationが
accept済みか状態を確認し、共有controllerの`--workspace . --case <case> export-allrun
--workflow solve --output Allrun.solve.generated`でsolver-only scriptを
生成してください。生成物を静的に確認し、mesh utilityが含まれないこと、並列数を実行時に
decomposeParDictから読むことを報告してください。CFD processは起動しないでください。
