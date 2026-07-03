# pipeline-kit — AI Agent 操作指南

本文件的讀者是**在使用者專案裡工作的 AI agent**（Claude Code、Codex CLI 等）。
人類導向的完整說明在同目錄 `README.md`；本文件只講「怎麼正確地用」與「不可以做什麼」。

Kit 位置（本機固定路徑）：`~/projects/multiagent/pipeline-kit`
以下以 `$KIT` 代稱。所有指令都在**專案根目錄**執行。

## 這是什麼

多引擎 agent 派工器。兩種模式：

- **Pipeline**：spec → plan（architect）→ 實作（coder）→ 交叉 review（reviewer，gate）。
  每階段由 `pipeline.yaml` 決定引擎鏈，dispatcher 自動 health check、failover、
  記錄引擎（cross-check：reviewer 強制與 coder 不同模型 family，避免自己審自己）。
- **Council**：一個問題平行丟給多個異質 family 模型 → 匿名互看辯論 → 仲裁者
  逐主張比對，輸出 consensus/split。適合選型、架構取捨、診斷類**決策問題**。

## 專案接入（一次性；若專案根已有 pipeline.yaml 則跳過）

```bash
cp $KIT/pipeline.yaml.example ./pipeline.yaml
```

只需編輯開頭四行（`project` / `language` / `test_cmd` / `lint_cmd`），
引擎目錄與角色鏈**不要動**（已含配額策略與 cross-check 設定）。
dispatcher 從 cwd 往上找 `pipeline.yaml`，找到的那層即專案根；
所有產物寫在該層 `pipeline/` 下。

建議在專案的 `.gitignore` 加 `pipeline/logs/`；`pipeline/` 其餘產物
（spec/plan/impl/review/state/council）**要 commit**，它們是稽核軌跡。

## Pipeline 工作流

```bash
mkdir -p pipeline/F-001            # 每個 feature 一個編號目錄
echo "<需求描述>" > pipeline/F-001/00-spec.md

$KIT/run-agent.sh architect pipeline/F-001/00-spec.md > pipeline/F-001/10-plan.md
# ⛔ HUMAN GATE：plan 必須經人類核准才能進下一步，不得自行放行
$KIT/run-agent.sh coder     pipeline/F-001/10-plan.md > pipeline/F-001/20-impl.md
$KIT/run-agent.sh reviewer  pipeline/F-001/20-impl.md > pipeline/F-001/30-review.md
```

- task 檔內容用「指示 + 範圍」（檔案路徑、branch、commit range），
  **不要把大 diff 全文塞進去**——prompt 走 argv，有 ARG_MAX 上限（已知未修）。
- coder 是 rw 模式，會用 `test_cmd`/`lint_cmd` 自我驗證；其餘角色唯讀。

## Council 工作流

```bash
echo "<要辯論的問題，含背景與限制>" > question.md
$KIT/run-council.sh question.md              # 產物在 pipeline/council/<run_id>/
$KIT/run-council.sh question.md --rounds 2   # 爭議大時加深辯論
```

結論讀 `pipeline/council/<run_id>/90-verdict.md`（逐主張標注支持度）。

## Exit codes（機器判讀，勿用 grep 猜）

| code | 意義 | agent 應對 |
|---|---|---|
| 0 | 成功 / council consensus | 繼續 |
| 1 | 引擎鏈全滅 | 讀 `pipeline/logs/` 最新檔，回報人類；勿無腦重試 |
| 2 | 設定錯誤（缺 config/role/檔案） | 修正後重跑 |
| 3 | reviewer `verdict: fail` / council split | 見下方硬性規則 |

## 硬性規則（違反即破壞這套機制的意義）

1. **不得繞過 gate**。reviewer exit 3 = review 沒過，不是「引擎故障」：
   不得換引擎重跑求 pass、不得改寫 30-review.md、不得當成功繼續。
   正確流程：讀 30-review.md 的 Findings → 修正 → 重跑 reviewer。
   連續 fail 兩輪 → 停下，升級人類。
2. **council split（exit 3）→ 升級人類**，附上 90-verdict.md 的分歧點；
   不得自行挑一邊當結論。
3. **不修改 kit 本身**（`$KIT/roles/*.md`、`dispatcher.py`、adapters）。
   專案層客製只允許動專案根的 `pipeline.yaml`。
4. **配額策略**：ro 類工作不得直接呼叫 `codex` CLI（business 訂閱、
   月配額單帳號直扣）；GPT 系流量走引擎鏈裡的 `gpt-proxy`（雙帳號輪替）。
   引擎鏈已按此排序，照鏈走即可，不要手動指定引擎。
5. **敏感資料**（病歷、個資、內部帳密等）只能派 `local-llm`（地端）角色，
   不得進任何雲端引擎。
6. `pipeline/state/engines.json` 是 cross-check 依據，**不得手改**。

## 故障排除

- `FATAL: pipeline.yaml not found` → 不在專案內，或未做一次性接入。
- 某引擎被 skip（health check failed）→ 正常，failover 會接手；
  proxy 引擎全掛時檢查 `~/.config/pipeline-kit/proxyapi.env` 與 proxy 主機
  （http://192.168.88.115:8317）。
- `engine output has no valid verdict line` → 該引擎輸出不合規，dispatcher
  已自動換下一個；若整鏈都不合規（exit 1），回報人類。
- 前提：`claude`/`codex` CLI 已登入、`python3-yaml` 已裝（僅新機器需處理）。

## 新專案 CLAUDE.md / AGENTS.md 建議引用段落

```markdown
## 多引擎派工（pipeline-kit）
涉及「新 feature 實作流程」或「技術選型/架構決策」時，先讀
~/projects/multiagent/pipeline-kit/AGENTS.md 並遵守其硬性規則。
```
