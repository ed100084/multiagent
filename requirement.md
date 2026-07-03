# Multi-Agent Software Delivery Pipeline — 需求規格

- **版本**：v0.1（2026-07-03）
- **狀態**：Draft — PoC 骨架已完成（pipeline-kit），待真實專案驗證
- **Owner**：Slash

---

## 1. 目標

建立一套**通用、引擎中立**的 multi-agent 軟體交付 pipeline 架構，使任何專案皆可套用，
並且 **Claude 或 Codex 任一方都不構成 single point of failure**。

### 1.1 核心原則

| # | 原則 | 說明 |
|---|------|------|
| P1 | Engine-agnostic | 角色（role）= 純 prompt + I/O 契約；引擎（Claude Code / Codex / 其他 CLI）只是可抽換的 runner |
| P2 | No SPOF | 每個 role 有 fallback chain，任一引擎故障（timeout / rate limit / auth / 服務中止）自動切換 |
| P3 | Artifact-driven handoff | Agent 間不靠對話 context 傳遞，靠檔案（spec → plan → diff → report）交接；可稽核、可中斷續作、可重跑 |
| P4 | 異質交叉驗證 | 寫 code 與 review code 強制使用不同引擎（cross-check），避免同模型 blind spot 自我認證 |
| P5 | Human gate | Plan approve 與 deploy 兩端必為人工決策；agent 產出一律走 PR，不直接進 main |
| P6 | 通用 kit + 專案 config 分離 | 通用邏輯放 kit（跨專案共用），專案差異全部收斂到一份 `pipeline.yaml` |

---

## 2. 架構

### 2.1 三層結構（引擎中立的關鍵）

```
Role 層     — prompt + I/O schema，永遠不含引擎特定語法
    ↓
Adapter 層  — 每引擎一支 wrapper，統一介面（4 個位置參數 + exit code）
    ↓
Engine 層   — claude -p / codex exec / gemini / 未來任何 CLI
```

### 2.2 Pipeline 角色分工

| Stage | Role | 預設引擎 | Mode | 產出 artifact |
|-------|------|----------|------|----------------|
| 1. Spec | 人工 / spec agent | — | — | `00-spec.md`（含 success criteria） |
| 2. Plan | architect | Claude（fallback: Codex） | ro | `10-plan.md` → **人工 approve** |
| 3. Implement | coder | Claude（fallback: Codex） | rw（限 worktree） | branch + `20-impl.md` |
| 4. Review | reviewer | Codex（fallback: Claude） | ro | `30-review.md`（含 `verdict: pass\|fail`） |
| 5. Test | tester | 任一 | ro | `40-test-report.md` |
| 6. Security | scanner | Codex 或 SAST 包裝 | ro | 弱點清單 |
| 7. Deploy gate | **人類** | — | — | approve / reject |

### 2.3 派工機制（agent 如何知道有工作）

| 機制 | 觸發者 | 適用 | 特性 |
|------|--------|------|------|
| 1. Orchestrator 派工 | 主 session LLM 語意比對 role description | Session 內互動開發 | 機率性，不保證觸發 |
| 2. Event-driven | Git hook / PR event / CI / Claude Code hooks | 正式 gate（review、security） | **確定性**，關鍵 gate 必用此機制兜底 |
| 3. Queue polling | Cron 排程啟動 headless agent 領取 task queue | 非同步批次、長工 | 需 claim 機制、冪等性、dead letter |

### 2.4 Artifact 契約（state machine）

```
pipeline/F-<id>-<name>/
├── 00-spec.md
├── 10-plan.md          # 人工 approve 後才進下一步
├── 20-impl.md          # branch name + diff summary + test 結果
├── 30-review.md        # verdict: pass|fail（機器可解析）
├── 40-test-report.md
└── STATUS              # current stage + owner

pipeline/state/engines.json   # 各 role 最後執行引擎（稽核 + cross-check 依據）
pipeline/logs/                # 每次執行完整 log
```

---

## 3. 引擎中立實作規格

### 3.1 Adapter 統一契約

```
輸入:  $1=role_prompt_file  $2=task_file  $3=workdir  $4=mode(ro|rw)
輸出:  stdout = 結果，exit code = 成敗
```

| 引擎 | ro 對應 | rw 對應 |
|------|---------|---------|
| Claude Code | `--permission-mode plan` | `--permission-mode acceptEdits` |
| Codex | `--sandbox read-only` | `--sandbox workspace-write` |

### 3.2 pipeline.yaml（每專案唯一自訂檔）

```yaml
project: <name>
test_cmd: "npm test"
lint_cmd: "npm run lint"

engines:
  claude: { adapter: adapters/claude.sh, health: "<ping cmd>" }
  codex:  { adapter: adapters/codex.sh,  health: "<ping cmd>" }

roles:
  architect: { engine: claude, fallback: [codex], mode: ro }
  coder:     { engine: claude, fallback: [codex], mode: rw }
  reviewer:  { engine: codex,  fallback: [claude], mode: ro,
               differ_from: coder }        # 強制異質 review

policy:
  cross_check: true
  retry: 2
  timeout_secs: 1800
  failover_on: [timeout, rate_limit, auth_error, non_zero_exit]
```

### 3.3 Dispatcher 行為

1. 由 cwd 向上尋找 `pipeline.yaml`
2. Engine chain = `role.engine` + `role.fallback`
3. cross_check：跳過 `differ_from` 指定 role 上次使用的引擎
4. Health check 失敗 → 跳下一個引擎
5. 每引擎 retry N 次；全部失敗 → exit 1，理由輸出 stderr（fail loud）
6. 成功後記錄 role→engine 至 state 檔（稽核軌跡）

### 3.4 Prompt 可攜性紀律

- Role prompt 只寫任務、輸入、輸出 schema、品質標準
- 禁用引擎特定指令（`spawn subagent`、`use Task tool` 等）
- 輸出強制結構化欄位（如 `verdict: pass|fail`），dispatcher 引擎無關解析
- 引擎差異全部隔離在 adapter 內

---

## 4. 合規要求（醫療環境）

| 項目 | 要求 |
|------|------|
| 責任歸屬 | 每個 PR 必有 human approver；commit 標註 agent 來源 |
| 稽核軌跡 | 引擎執行紀錄 + artifact 全程留存，對應 ISO 27001 變更管理 |
| 權限最小化 | reviewer / scanner 一律 read-only sandbox、不給網路 |
| Secret 管理 | CI 環境變數與 sandbox 權限按 stage 最小化 |
| Prompt injection | Review agent 讀取的 code 可能含惡意指令 → read-only 兜底 |

---

## 5. 風險與緩解

| 風險 | 緩解 |
|------|------|
| 機制 1 派工不確定性 | 關鍵 gate 用機制 2（hook/CI）確定性兜底 |
| Rubber-stamp 化（人工 review 退化） | 大 diff（> max_diff_lines）強制人工細看 |
| 最小公分母問題（prompt 通用 = 放棄引擎獨門能力） | Adapter 可加 engine-specific 前置注入，role prompt 本體保持中立 |
| 輸出品質不齊（不同引擎風格差異） | 嚴格 output schema，不靠自然語言慣例 |
| Dispatcher 成為新 SPOF | 無狀態 script、版控、任何機器可跑 |
| 成本失控（多 agent × 多 stage） | depth / timeout / 每日配額上限 |
| 觸發風暴（agent 產出觸發下一 event 成迴圈） | Bot commit 不觸發 workflow + depth 上限 |
| 雙引擎訂閱成本 | Failover 有效前提是兩邊帳號皆活，列入年度預算 |
| 過度工程 | 小改動走 fast-path（`scope: hotfix` 只跑 review+test） |

---


## 7. Roadmap

| 期程 | 內容 |
|------|------|
| 短期 | Kit 骨架真實專案 smoke test（建議 StudyPlan）；驗證 claude plan mode / codex sandbox 假設；|
| 中期 | Hook / CI 確定性 gate；test / security stage；pipeline.yaml schema 固化（跑 3 專案後收斂）；kit_version 防漂移 |
| 長期 | Quorum 模式（關鍵變更雙引擎 review，不一致升級人工）；queue 派工 + 排程；第三引擎 adapter；醫院內部導入含合規對照 |

---

## 8. 現況

- **已完成**：pipeline-kit v0（dispatcher + claude/codex/proxyapi adapters + 5 role prompts + yaml 範本）；
  council 模式（問題分派 -> 異質 panel 辯論 -> 仲裁 verdict，即 Roadmap 的 quorum 泛化版）
- **已驗證**（真實引擎，2026-07-03）：claude plan mode 唯讀、coder rw + allowlist、codex sandbox、
  cross_check + fail loud、CLIProxyAPI 全模型探測與分層、council 完整流程（consensus 路徑）
- **待驗證**：council split 路徑、真實專案（StudyPlan）end-to-end
- **刻意未做**：queue 派工、CI workflow —— 留待真實專案跑過後再加
