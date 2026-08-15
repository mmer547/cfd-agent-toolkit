---
description: 指定ケースを既存メッシュのまま再計算する
---

`cfd-case-agent` skillをロードしてください。対象ケース`$1`に対して共有controllerの
`--workspace . --case <case> plan rerun`をdry-runし、初期条件テンプレート、削除・保持対象、
provider/version、solver、並列数、blocking_issuesを説明してください。

readyなら`run-workflow rerun --execute`を1回だけ呼び出してください。`0`、`0.orig`、
`0.org`および0内の`.orig`/`.org`フィールドはcontrollerの計画どおり扱い、独自に削除しないでください。
既存の`constant/polyMesh`と`system`は保持してください。
