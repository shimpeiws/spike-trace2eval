# R1のTrace2Eval最小実験計画

作成日：2026-11-09。状態：計画。実装、環境構築、独立Agentでの再実行は未着手。

## 1. 目的と判断基準

coding-workerの保存済み実行履歴を使い、独立したCoding Agentに渡せる評価ケースを1件生成する。対象はR1のみとする。

実験では次を確認する。

1. 元のタスクとGit管理下の開始状態を復元できる。
2. 評価Agentに渡す入力の出所を検査し、実行途中で得た解法や結果の混入を防げる。
3. coding-worker本体を変更せず、ケース生成から独立Agentの成果物評価まで進められる。

当時のマシン全体の完全複製や、公開済み解法による学習データ汚染の否定は判断基準に含めない。復元したソースと近似実行環境の成立を検証する。

## 2. 論文との関係

[Trace2Eval論文の本文と付録A.2](https://arxiv.org/html/2611.06506v1)では、会話から目的と明示要件を抽出する。読取結果や編集前内容から初期ファイルを復元し、不足部分は推定または公開資料で補う。セッション中の生成物は初期状態から除外する。依存関係も時系列で初期状態と後付けに分ける。

復元環境では元のツール列を再生し、記録結果との一致度と人手確認で復元を検証する。これは元環境全体の同一性の証明ではない。成果物評価には人手で調整したrubricを使う。

本PoCでは以下を独自に簡略化する。論文の完全再現とは呼ばない。

| 論文の構成 | R1での扱い |
| --- | --- |
| 会話からの要求復元 | 保存済みJobのIssue snapshotを直接採用する |
| 不足ファイルの推定・生成 | 固定SHAのGitファイルを使い、推定生成しない |
| 全ツール列のreplayと環境修復 | 初期状態の検査、セットアップ、必要な検証に限定する |
| 複数Agentによる復元 | 決定的な抽出処理と人手レビューで代替する |
| 多数ケースの選定とrubric校正 | 1件の要求充足と再実行可能性だけを確認する |

初期状態とセッション中の生成情報を分離する考え方を採用する。入力の出所制限と機械検査は本PoC独自の設計とする。論文の公開実装は調査時に取得できず、実装コードとの対応は未確認。

## 3. 対象ケースR1

| 項目 | 調査で確認した内容 |
| --- | --- |
| 対象 | [shimpeiws/yuurei #195](https://github.com/shimpeiws/yuurei/issues/195) |
| 元の要求 | タスクファイルをプロジェクトに作らず文字列タスクを実行し、同じ内容のファイル入力とtask digestを一致させる |
| 開始SHA | `99cd09ab71b0ba2807f1d39d0a26dfe042e89bac` |
| Worker run ID | `20261008T033410Z-7f2a94ec` |
| 実行日時 | 2026-10-08、約7分 |
| Agent / Harness | OpenCode、旧adapter名 `dsh`、OpenCode版 `1.18.34` |
| セッション | 1セッション。plannerやconvergeを含めない |
| Trace | `events.jsonl`、6,588イベント、2,254,759 bytes |
| ツール操作 | 固有IDで85操作。bash 50、edit 21、read 13、write 1 |
| 検証 | Workerの5 checksがすべてexit 0 |
| 独立資料 | 元Issue、[PR #223](https://github.com/shimpeiws/yuurei/pull/223)、対象Git履歴、実行前readiness記録 |

保存証跡はprivateなcoding-workerリポジトリ内にある。アクセス権を持つ実装者が、次の相対パスを入力として指定する。

```text
eval/evidence/issue-34/
  README.md
  SHA256SUMS
  runs/v03-bounded-devflow-run-yuurei-195/20261008T033410Z-7f2a94ec/
```

照合資料は `eval/jobs/issue-34/task.json` と `eval/jobs/issue-34/readiness.md`。証跡を記録したcommitは `28a21f6ffc7de906ceeda8100fbd6a2ae1239bac`。これらの資料自体は本リポジトリへコピーしない。

### 確認済みの整合性

- JSONLのseqは1から6,588まで連続する。
- `tool.call` は328イベントあるが、状態更新をまとめると85操作になる。85操作すべてに対応する `tool.result` がある。
- Job、manifest、`checkout.ready` の開始SHAが一致する。
- seq 94の `git status` はdetached HEADかつcleanを示す。
- 保存Jobと当時のv2テンプレートから再生成したプロンプトは、seq 9の4,066 bytesと完全一致する。
- prepared taskとJobのIssue snapshotが一致する。
- `SHA256SUMS` に列挙された33ファイルがすべて一致する。

これらは2026-11-09の読み取り調査結果である。将来の抽出処理でも再検査し、過去の報告だけを根拠に受理しない。

## 4. 保存アーキテクチャと復元可能性

coding-workerはprepared taskをJobへ変換し、固定SHAをcloneしてdetached checkoutする。作業用checkoutと証跡は別ディレクトリに置かれる。Job、manifest、正規化イベント、結果、patch、検証出力が保存される。

OpenCodeのツールイベントがファイル操作やコマンドの記録になる。OS全体の監査ログではない。イベントはredactionやサイズ制限の影響を受けるため、存在と完全性を別々に検査する。

現行coding-workerではセッションやmanaged runtimeを終了時に削除する経路がある。過去セッションをAPIやDBから必ず再取得できるとは扱わず、R1の保存ファイルを入力にする。

以下の `observed` はイベントと同runのJob・manifestに直接記録された情報を表す。Gitと実行前記録による補完は根拠欄で区別する。

| 情報 | 分類 | 根拠と限界 |
| --- | --- | --- |
| Workerへの要求・AC・制約 | observed | `job.snapshot.json`、seq 9 |
| Issue以前の人間の会話 | missing | 保存証跡には含まれない |
| 初期タスクプロンプト | observed | seq 9と当時のv2テンプレートの一致 |
| Harnessのsystem promptと自動注入コンテキスト全体 | missing | 最終的なモデル入力全体は保存されていない |
| リポジトリ内の指示ファイル | observed、一部Git補完 | seq 89の読取結果と開始SHAのファイル |
| 途中の追加指示 | missing | 識別された人間の追加指示はないが、role情報が粗く厳密な不在証明はできない |
| リポジトリと開始SHA | observed | Job、manifest、seq 2 |
| detached・clean開始 | observed | seq 94 |
| Git管理下の初期ファイル全体 | Trace単体ではmissing | 指定SHAのGitオブジェクトから取得可能 |
| OS系統 | inferred | パス形式からmacOSと推定。OS版は不明 |
| Nodeとpnpm | observed | seq 2103でNode 26.8.2、pnpm 11.6.0 |
| 依存関係とセットアップ | 一部observed、Git補完 | seq 2156のinstall結果と開始SHAのlockfile |
| テストコマンド | observed | Jobのchecksとseq 6576以降 |
| 環境変数、PATH全体、locale | missing | 完全な環境スナップショットなし |
| 推論サービスへの依存 | observed | 元実行はOpenCode Goを利用。認証値は復元しない |

開始SHAの `mise.toml` はNode 24を指定するが、実測は26.8.2だった。元実行の近似には実測を優先し、相違を記録する。依存パッケージのinstallはセッション途中なので、初期ソースへ `node_modules` を混入させない。

## 5. 情報漏洩を防ぐ設計

### 実際に確認した漏洩源

| 出所 | 含まれる後知恵 |
| --- | --- |
| seq 3544のedit結果 | 具体的な修正内容 |
| seq 4145のテスト失敗 | 実装後に判明した失敗箇所 |
| seq 4802の依存CLI調査 | 調査を経て得た問題理解 |
| seq 5231のテスト成功 | 修正後の成功結果 |
| seq 5852付近のdiff | 修正済みコード |
| seq 6564の最終回答 | 解法と検証結果の要約 |
| 元patch、PR、その後のGit履歴 | 元の実装と後続修正 |

正規化された初期プロンプトも `role: assistant` と記録されている。roleだけでは入力と解法を分けられない。標準exportもeventsとresultを残すため、そのまま評価Agentへ渡さない。

### 評価入力の許可リスト

評価Agentへの入力は、次の出所だけから生成する。

- 保存JobのIssue、AC、タスク制約、checks、予算。
- 固定開始SHAのソース。
- 解法を含まない、明示的に選んだ環境バージョンとセットアップ手順。

全Traceは評価側で検査してよいが、その自由要約を評価入力へ追加しない。説明文を追加するときは初期情報の出所を紐付ける。根拠がなければ生成を拒否する。

元Trace、patch、result、review、planは評価Agentから見えない場所に保持する。ソースは指定commitの内容だけから構成し、将来のcommit、remote、元作業ディレクトリを渡さない。依存取得後の実行では外部検索と解法取得の経路を制限する。

入力ファイルの許可リスト、出所、hashを検査する。patchとの文字列一致検査は補助にとどめる。一般的なコードの一致は漏洩の証明にならない。

この設計が検査するのは生成入力への混入である。公開済み解法をモデルが学習済みでないことは証明しない。

## 6. 実装方式と入出力

| 方式 | 評価 |
| --- | --- |
| A：既存Trace直接解析 | 採用。JobとGitを主入力にすれば、本体を変更せず対象1件を処理できる |
| B：既存exportまたはAPIから取得 | Job形式とhash情報は再利用する。標準exportは解法が残り、過去セッションAPIは残存保証がないため主経路にしない |
| C：今後の計測追加 | R1では不要。将来ケースで重要情報が欠けた場合に検討する |

入力は証跡ディレクトリ、対象Gitオブジェクト、環境方針とする。ローカルの絶対パスは引数で指定し、公開設定へ固定しない。

出力構成は暫定とし、実装時に対象1件に必要な範囲へ絞る。

```text
case/
  case.json
  task.md
  environment.json
  setup.sh
  source/
```

`case.json` はケースID、repository、開始SHA、要求、AC、制約、checks、予算、入力hashを保持する。`environment.json` は環境の観測値、採用値、不明点、元環境との差を保持する。`setup.sh` は依存セットアップだけを行う。

元証跡、詳細provenance、漏洩検査結果、評価用テスト、再実行結果は評価Agentへ渡すディレクトリの外に置く。公開計画と非公開の実験データを混同しない。

## 7. 実装する単位と完了条件

### 単位1：保存証跡の検査と初期条件の抽出

Job、run ID、開始SHA、checksum、イベント連番、call/result対応、truncationを検査する。Jobの初期情報だけからタスクを生成し、当時のv2プロンプトとの照合を行う。

完了条件は、同じ入力から同じケース内容とhashが得られ、根拠不足や不一致を明示して停止できること。

### 単位2：開始ソースと近似環境の準備

開始commitのファイルだけを取得する。Node 26.8.2、pnpm 11.6.0、開始commitのlockfileを使う。初期ソースと依存セットアップによる生成物を分ける。

完了条件は、ソースが指定commitに一致し、セットアップと既存checksが隔離環境で実行できること。OSやキャッシュ等の差分を記録する。元ツール列の完全replayは要求しない。

### 単位3：入力の漏洩検査と要求に基づく評価

評価Agentのマウント、入力ファイル、Git履歴、ネットワーク経路を検査する。初期要求に対応する振る舞いを、元patchから独立して確認する。

実行前記録では開始SHAも既存の5 checksを通過している。既存checksの成功だけを課題解決の証拠にしない。以下を確認する評価を追加する。

- プロジェクトにタスクファイルを作らず文字列タスクを実行できる。
- 同じ内容のファイル入力とtask digestが一致する。
- タスクの出所記録が実態と矛盾しない。

Issueはstdinまたは同等inline形式を許容する。採点で元実装固有の構造や文字列表現を要求しない。対象の既存テストはfixture runtimeを使うため、検証自体に実モデルの推論は不要。

完了条件は、課題未解決の開始状態と要求を満たす成果物を区別でき、評価入力に後知恵を含めないこと。元patchを使う正例確認を行う場合も、評価側だけで扱う。

### 単位4：独立Agentで1回実行

初期要求と開始ソースだけを渡して1回実行し、成果物、検証結果、使用環境、予算、失敗理由を保存する。元のモデル固定指示などHarness固有条件を変更した箇所を記録する。

成功は独立Agentが必ず解くことではない。ケース生成、実行、採点が成立し、Agentの失敗と環境不備を区別できることを完了条件にする。

実装規模は対象1件で数百行、1〜3日を目安とする未検証の見積もり。依存取得とOS差で変わる。汎用フレームワークは作らない。

## 8. 実装時に確定する点

| 項目 | 推奨する初期方針 |
| --- | --- |
| OS | 元と同じmacOS系統を優先。Linux対応は別検証 |
| Node | 宣言値24より実測26.8.2を優先 |
| 依存関係 | lockfile固定。事前キャッシュは許容し、条件を記録 |
| Harness条件 | タスク要件と分離し、独立Agent向け変更を明示 |
| 評価基準 | 初期Issueの許容範囲から定義し、元実装に固定しない |
| データ公開 | 今回は文書のみ。Traceや生成ケースの公開は内容と権利を確認した別の判断 |

## 9. 今回の範囲外

- coding-worker本体、運用設定、進行中Workerの変更。
- 新しい収集用Agent実行や追加計測。
- R1以外のケース、planner・convergeを跨ぐ復元。
- 全ツール列のreplay、論文のrubric全体の実装。
- 多数モデル比較、性能ランキング、学習データ汚染がないという主張。
- Trace、初期プロンプト全文、元patch、認証情報の公開。

## 10. 公開資料

- [Trace2Eval論文](https://arxiv.org/abs/2611.06506)
- [yuurei Issue #195](https://github.com/shimpeiws/yuurei/issues/195)
- [yuureiの開始commit](https://github.com/shimpeiws/yuurei/tree/99cd09ab71b0ba2807f1d39d0a26dfe042e89bac)
- [yuurei PR #223](https://github.com/shimpeiws/yuurei/pull/223)

PRは独立した照合資料だが解法を含むため、評価Agentへの入力にはしない。元Traceとreadiness記録の検査には、privateな保存元へのアクセスが別途必要。
