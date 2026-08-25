---
description: snappyHexMesh設定を目標品質まで反復実行する
---

`cfd-case-agent` skillをロードしてください。対象ケースは`$1`です。このcommandはsnappyHexMeshケースの実メッシュ
最適化を明示的に依頼します。`system/snappyHexMeshDict`がなければ実行せず、blockMeshのみの
通常作成には`/cfd-mesh-run`を案内してください。対象がsnappyHexMeshケースなら、ユーザーが
上限セル数をまだ指定していない場合は、その値を質問してここで停止してください。既定値を推測したり
`.cfd-agent.json`へ保存したりしないでください。値を得たら最初にdry-run preview、指定された
セル上限、checkMesh許容失敗数、最大反復数、decomposeParDictの有無による直列・並列判定を
確認して短く提示してください。その後、
OpenCodeのbash承認を受けて共有controllerの
`--workspace . --case <case> optimize-mesh --max-cells <user-limit> --execute`を実行してください。
utilityを直接実行したり、meshQualityDictを緩和したりしないでください。完了時はaccepted/best
iteration、停止理由、run directory、最終metricsを報告してください。
