# Windows版Codexで使う手順

## 配布物を1回だけ配置する

`distribution/OpenCode-CFD-Agent/`の中身を、複数のCFDケースを置く計算作業フォルダの
直下へ1回だけコピーします。フォルダ名は既存のOpenCode利用者との互換性のため維持していますが、
中身はCodexとOpenCodeの共通配布物です。

```text
C:\CFD-work\
├─ .agents\
│  └─ skills\
├─ .cfd-agent.json
├─ AGENTS.md
└─ cases\
   ├─ case-a\
   │  ├─ 0\（または0.orig、0.org）
   │  ├─ constant\
   │  └─ system\
   └─ case-b\
      ├─ 0\
      ├─ constant\
      └─ system\
```

`.opencode/`、`opencode.json`、`Start-OpenCode.cmd`はOpenCode用です。Codexはこれらを
使用しませんが、同じ作業フォルダに置いても支障はありません。

## WSLとOpenFOAMを設定する

共有`.cfd-agent.json`の`providers`で、実PCのWSL distribution名とOpenFOAMのbashrcを
設定します。Codex自身はWindows側で動かし、WSLへのパス変換とCFD utilityの起動は
controllerへ任せます。Codexから`wsl.exe`、`blockMesh`、`mpirun`などを直接実行しません。

```json
"foundation-v14": {
  "distribution": "Ubuntu",
  "bashrc": "/opt/openfoam14/etc/bashrc"
}
```

## Codexを作業フォルダで開始する

Codexデスクトップアプリでは、計算作業フォルダをワークスペースとして開きます。
Codex CLIを別途導入している場合はPowerShellから次を実行します。

```powershell
Set-Location C:\CFD-work
codex
```

Codexは作業フォルダ直下の`.agents/skills`を検出します。スキルが一覧に現れない場合は
Codexを再起動してください。明示的に使う場合はプロンプトで`$cfd-case-agent`または
`$stl-geometry-analyzer`を指定します。通常の日本語依頼から自動選択させることもできます。

## 最初の確認とdry-run

```text
$cfd-case-agent を使い、cases以下のケースを検出して構成を確認してください。
cases/case-aのメッシュ作成計画をdry-runしてください。
cases/case-aのSTLを解析し、メッシュ設定候補を提示してください。
```

最初に`discover-cases`、`inspect`、`list-commands`、対象workflowの`plan`を実行します。
メッシュ調整の上限セル数が依頼に含まれていない場合、Codexは値を質問し、回答を得るまで
調整用controllerを実行しません。配布設定には既定の上限値を持たせていません。
dry-runではCFD processを起動しません。ケース名や配置の深さに依存せず、辞書構成と
controlDictのvendor/versionからprofileとworkflowを選びます。

## 実行を依頼する

```text
cases/case-aのメッシュ設定を調整して実行してください。
cases/case-aの計算を実行してください。
cases/case-aとcases/case-bを指定順に1 JOBずつ計算してください。
```

実CFDの開始には明示的な依頼が必要です。複数ケースは指示がなければ全件を事前確認し、
指定順にケース単位で1 JOBずつ実行して、失敗時は後続を開始しません。各ケース内の並列数は
`decomposeParDict`から読み取ります。

## 権限とデータ境界

Codexには計算作業フォルダをワークスペースとして開かせてください。通常の読み書きとdry-runは
その中だけで行い、ケースデータをWeb検索、外部サービス、または作業フォルダ外へ送らないよう
`AGENTS.md`と各SKILL.mdで指定しています。実行時の確認表示はCodex本体のsandbox、approval、
管理ポリシーにも従います。これらを配布物から無効化することはありません。
