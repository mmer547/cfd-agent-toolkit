# Windows版OpenCodeで使う手順

## 配布物を1回だけ配置する

利用者へ渡すのは`distribution/OpenCode-CFD-Agent/`の中身だけです。これをCFDケースごとではなく、
複数ケースを置く計算作業フォルダの直下へ1回だけコピーします。

```text
C:\CFD-work\
├─ .agents\
├─ .opencode\
├─ .cfd-agent.json
├─ AGENTS.md
├─ opencode.json
├─ Start-OpenCode.cmd
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

ケースは作業フォルダ内なら任意の深さに置けます。OpenFOAMケース、STL、計算結果、ログは配布物に含まれません。

## WSLとOpenFOAM profileを設定する

共有`.cfd-agent.json`の`providers`で、各版を起動するWSL名と環境初期化スクリプトを指定します。
同梱値はWSL名`Ubuntu`と標準的なインストール先の例です。実PCの構成が違う場合はここだけ直してください。

```json
"foundation-v14": {
  "distribution": "Ubuntu",
  "bashrc": "/opt/openfoam14/etc/bashrc"
},
"openfoam-com-v2512": {
  "distribution": "Ubuntu",
  "bashrc": "/usr/lib/openfoam/openfoam2512/etc/bashrc"
}
```

同梱profileはFoundation 10～14、およびOpenCFD v2206、v2212、v2306、v2312、v2406、
v2412、v2506、v2512、v2606です。`system/controlDict`のvendor/versionを完全一致で判定します。
未登録版を別版の環境で推測実行せず停止します。ケース固有工程はフォルダ名ではなく、`case_rules`が
solverや辞書構成を照合して選択します。そのため、ケースを`sample`から`temp`や`scratch`へ移動したり、
ケース名を変更したりしても同じ構成なら同じworkflowになります。

同梱ルールは、snappyHexMesh＋topoSet＋代替decompose辞書のケースと、Lagrangian初期状態・
`mapFieldsDict`を持つ後続ケースを認識します。後続ケースでは、同じ親フォルダにある
`constant/kinematicCloudPositions`を持つケースを一意に探して依存元にします。0件または複数件なら
推測せず停止します。

構造だけでは区別できない例外に限り、`case_overrides`へ作業フォルダからの相対パスを書けます。

```json
"case_overrides": {
  "temp/my-special-case": {
    "profile": "openfoam-com-v2512",
    "decompose_par_dict": "system/decomposeParDict.6"
  }
}
```

## Windows側でOpenCodeを起動する

PowerShellで計算作業フォルダへ移動し、ランチャーを実行します。OpenCodeをWSL側で二重起動しません。

```powershell
Set-Location C:\CFD-work
.\Start-OpenCode.cmd
```

初回はOpenCodeの`/connect`と`/models`でLLM providerとmodelを設定します。

## ケースを検出・確認する

```text
/cfd-cases cases
/cfd-inspect cases/case-a
/cfd-mesh-preview cases/case-a
/cfd-solve-preview cases/case-a
```

controllerはケースごとに版、solver、利用可能な辞書を検出します。`snappyHexMeshDict`がなければ
snappy工程を省略し、`decomposeParDict`がないか`numberOfSubdomains 1`なら直列にします。
並列数をCPU数から推測しません。代替辞書を指定した場合は、その辞書から並列数を読み、対応utilityへ
辞書オプションを渡します。previewはCFD processを起動しません。

チュートリアルで必要な形状などを`Allrun`が`$FOAM_TUTORIALS`からコピーしている場合は、
登録済みの固定コピーだけをmesh workflowの先頭で実行できます。previewには参照したAllrun、
コピー元、コピー先が表示され、コピー先が不足するときだけ実行されます。Allrun自体を実行・sourceしたり、
未登録のshell処理を解釈したりはしません。固定コピーとして表現できない場合はblockedのまま停止します。
`FOAM_TUTORIALS`がbashrcで未定義の環境では、登録で許可された場合に限り
`$WM_PROJECT_DIR/tutorials`とproviderのOpenFOAMルート直下の`tutorials`も確認します。

## 1ケースを実行する

```text
/cfd-mesh-run cases/case-a
/cfd-solve-run cases/case-a
/cfd-rerun cases/case-a
```

各commandは最初にplanを表示し、問題がなければ承認後にcontrollerだけを通して実行します。
`blockMesh`、`mpirun`、`wsl.exe`などをOpenCodeが直接呼ぼうとした場合は拒否してください。
snappyHexMesh設定の反復最適化は`/cfd-mesh-optimize cases/case-a`を使用します。

再計算では既存メッシュを保持します。`0.orig`または`0.org`ディレクトリがあれば`0`を復元します。
`0/alpha.water.orig`のようなフィールドテンプレートは保持し、生成済みの`0/alpha.water`だけを除去して
`setFields`等の登録済み初期化utilityを実行します。以前の時刻、processor、postProcessing、rootログは
previewに列挙されたものだけを削除します。

## 複数ケースを一括操作する

```text
/cfd-batch-preview mesh cases/case-a cases/case-b
/cfd-batch-run solve cases/case-a cases/case-b
```

全ケースを先にdry-runし、1件でもblocked/errorなら1件も実行しません。実行方針の指定がなければ、
指定順にケース単位で1 JOBずつ実行し、前JOBの正常終了後に次JOBを開始します。失敗時は後続を開始しません。
各ケースの中では、そのケースの`decomposeParDict`に従ってMPI並列計算できます。
並行計算、優先順位、キュー管理は外部のGUI/JOB schedulerへ任せます。

## utility単位で操作する

```text
/cfd-command-preview cases/case-a openfoam.checkMesh
/cfd-command-run cases/case-a openfoam.checkMesh
/cfd-export-allrun cases/case-a
/cfd-geometry-analyze cases/case-a
```

実行可能なutilityは`registry/commands.json`の固定allowlistだけです。solver-only Allrunは対象ケース内へ生成され、
メッシュ作成工程を含みません。

## 許可とデータ境界

作業フォルダ内のread、edit、glob、grep、listとcontrollerのdry-runは確認なしです。実CFD、メッシュ最適化、
復旧、複数ケース実行は承認を残します。作業フォルダ外、web検索、外部directory、未登録shell commandは
拒否または確認対象です。クラウドLLMを選ぶと会話とモデル入力はその提供者へ送られるため、PC外へ一切
送信できない要件ではOpenCodeにローカルモデルを設定してください。

controllerは設定済みbashrcだけを使い、インストール先を探索しません。初期化に失敗した場合は、表示された
profile、WSL distribution、bashrcを修正してから再実行してください。
