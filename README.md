# spike-trace2eval

coding-workerの既存実行Traceから、独立したCoding Agentに渡せる評価ケースを1件生成するためのPoCです。[Trace2Eval論文](https://arxiv.org/abs/2611.06506)を参考にします。

**現在は計画のみです。抽出器、実行環境、評価処理は未実装で、再実行も未検証です。**

## 最初の対象

R1として、[yuureiのIssue #195](https://github.com/shimpeiws/yuurei/issues/195)に対するcoding-workerの既存実行を使用します。

- タスク：タスク内容をstdinまたは同等のinline形式で受け取り、同じ内容のファイル入力とdigestを一致させる。
- 開始commit：`99cd09ab71b0ba2807f1d39d0a26dfe042e89bac`。
- 証跡：単一セッション、6,588イベント、初期Job、開始SHA、ツール操作85件、5種類の検証結果が保存されている。
- 既知の制約：OSとHarness内部指示を含む実行環境全体は復元できない。元の解法は[PR #223](https://github.com/shimpeiws/yuurei/pull/223)として公開済み。

元Traceの保存先はprivateです。このリポジトリにはTrace、初期プロンプト全文、元patch、認証情報を収録しません。上記は2026-11-09の読み取り調査で確認した要約であり、公開資料だけからすべてを追試できる状態ではありません。

## この実験で確認すること

1. 保存済みJobと固定Git SHAから、元の要求と作業開始時のソースを復元できるか。
2. 評価Agentへの入力を初期情報だけから構成し、解法や成功結果の混入を検査できるか。
3. coding-worker本体を変更せず、独立Agentが実行できるケースを生成できるか。

初期入力はJobと開始commitから決定的に生成します。全Traceを要約してタスクを作る方法は採用しません。元Traceと成果物は評価Agentから隔離し、評価側の証跡として扱います。

## 計画

[R1のPoC計画](docs/poc-plan.md)に、調査根拠、復元可能性、論文との違い、漏洩対策、実装単位、完了条件をまとめています。

今回の登録範囲はこのREADMEと計画文書です。複数ケースへの拡張、汎用フレームワーク、coding-workerへの追加計測、性能ランキングは対象外です。
