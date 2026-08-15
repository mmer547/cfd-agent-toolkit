---
description: STL全体寸法と狭隙推定をJSONへ出力する
---

`stl-geometry-analyzer` skillをロードしてください。対象ケースは`$1`です。Windows Pythonで
`<case>/constant/triSurface`内のSTLを解析し、座標単位をm、隙間内セル数を4として
`<case>/.cfd-runs/geometry/stl-analysis.json`へ保存してください。全体bounds、領域、連結成分、
最小opposed clearance、gap用最大cell size、limitationsを日本語で要約してください。
メッシュ辞書を変更せず、OpenFOAMやWSLを起動しないでください。
