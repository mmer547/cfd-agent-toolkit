---
description: 作業フォルダ下のCFDケースを検出する
---

`cfd-case-agent` skillをロードしてください。探索起点は`$1`です。空なら`.`を使い、
`python .agents/skills/cfd-case-agent/scripts/cfdctl.py --workspace . discover-cases --root <root>`
を実行してください。検出ケースだけを一覧表示し、CFD processは起動しないでください。
