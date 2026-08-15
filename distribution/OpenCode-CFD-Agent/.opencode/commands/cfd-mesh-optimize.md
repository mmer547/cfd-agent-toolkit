---
description: snappyHexMesh設定を目標品質まで反復実行する
---

`cfd-case-agent` skillをロードしてください。対象ケースは`$1`です。このcommandはsnappyHexMeshケースの実メッシュ
最適化を明示的に依頼します。`system/snappyHexMeshDict`がなければ実行せず、blockMeshのみの
通常作成には`/cfd-mesh-run`を案内してください。対象がsnappyHexMeshケースなら、最初に
dry-run preview、6,000,000セル上限、checkMesh許容失敗数、最大反復数、
decomposeParDictの有無による直列・並列判定を確認して短く提示してください。その後、
OpenCodeのbash承認を受けて共有controllerの
`--workspace . --case <case> optimize-mesh --execute`を実行してください。
utilityを直接実行したり、meshQualityDictを緩和したりしないでください。完了時はaccepted/best
iteration、停止理由、run directory、最終metricsを報告してください。
