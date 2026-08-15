---
description: メッシュ最適化runの状態を確認する
---

`cfd-case-agent` skillをロードしてください。対象ケースは`$1`、run IDは`$2`です。
共有controllerの`--workspace . --case <case> mesh-optimization-status --run-id <run-id>`で
確認し、日本語で要約してください。run IDが空なら`<case>/.cfd-runs/mesh/`を読み取り、
利用可能なrun IDだけを列挙してください。
CFD processは起動しないでください。
