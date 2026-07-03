# pipeline-kit — engine-agnostic multi-agent delivery pipeline

角色（role）= 純 prompt + I/O 契約；引擎（Claude/Codex/...）= 可抽換 runner。
綁定與 failover 全在各專案的 `pipeline.yaml`。

## 結構
```
pipeline-kit/
├── run-agent.sh          # pipeline 入口: run-agent.sh <role> <task_file> [--workdir DIR]
├── run-council.sh        # 議會入口: run-council.sh <question_file> [--rounds N] [--id NAME]
├── dispatcher.py         # 引擎解析 / health check / failover / cross-check / 日誌
├── council.py            # 議會模式：異質 panel 辯論 -> 仲裁 verdict（詳下）
├── adapters/
│   ├── claude.sh         # claude -p（ro=plan mode, rw=acceptEdits）
│   ├── codex.sh          # codex exec（ro=read-only, rw=workspace-write）
│   └── proxyapi.sh       # 任何 OpenAI-compatible endpoint（CLIProxyAPI），僅 ro
├── roles/                # engine-agnostic role prompts
│   ├── architect.md      # spec -> plan（唯讀）
│   ├── coder.md          # plan -> 實作 + 自測（可寫）
│   ├── reviewer.md       # 交叉驗證 review，輸出含 "verdict: pass|fail"
│   ├── panelist.md       # 議會成員：獨立作答 / 交叉批判修訂
│   └── moderator.md      # 議會仲裁：輸出 "verdict: consensus|split"
└── pipeline.yaml.example # 複製到專案根目錄改名 pipeline.yaml
```

## 安裝（WSL2 Ubuntu 24.04）
```bash
git clone <this> ~/pipeline-kit          # 或直接放置
sudo apt install python3-yaml            # 或 pip install pyyaml --break-system-packages
cd <project> && ~/pipeline-kit/init-project.sh   # 一鍵接入：pipeline.yaml（自動偵測
```                                      # 語言/test/lint）+ CLAUDE.md 引用 + gitignore
前提：`claude` 與 `codex` CLI 已登入、在 PATH 上。

## 使用
```bash
cd <project>
echo "重構 login API 的錯誤處理" > pipeline/F-001/00-spec.md
~/pipeline-kit/run-agent.sh architect pipeline/F-001/00-spec.md > pipeline/F-001/10-plan.md
# 人工審 plan 後：
~/pipeline-kit/run-agent.sh coder     pipeline/F-001/10-plan.md > pipeline/F-001/20-impl.md
~/pipeline-kit/run-agent.sh reviewer  pipeline/F-001/20-impl.md > pipeline/F-001/30-review.md \
  || echo "REVIEW FAILED"   # dispatcher 強制解析 verdict：fail -> exit 3、缺 verdict 行 -> 換引擎
```

## Council 模式（問題分派 -> 異質辯論 -> 驗證過的結論）
把一個問題平行派給多個**異質 family** 的引擎，互看匿名答案辯論修訂，
最後由仲裁者逐主張比對出經交叉驗證的結論；實質分歧升級人工（P5 human gate）。

```bash
cd <project>   # pipeline.yaml 需有 council: 區塊（見 example）
~/pipeline-kit/run-council.sh question.md            # exit 0=consensus, 3=split, 1=失敗
~/pipeline-kit/run-council.sh question.md --rounds 2 # 加深辯論輪數
```

流程與機制：
1. panel 引擎先 health check、**同 family 自動去重**（異質性是重點）；
   健康異質成員 < `min_panel` -> fail loud
2. r1 各自獨立作答（平行）
3. r2..rN 每人看到其他成員**匿名化**（Panelist A/B/C）的答案，
   交叉批判後完整重寫自己的答案 —— 匿名避免品牌偏見；
   修訂輪失敗的成員沿用上一輪答案（carry forward）
4. moderator 仲裁：逐主張比對共識/分歧，輸出 `verdict: consensus|split` +
   `confidence:`（機器可解析）；輸出不合規自動換下一個 judge
5. artifact 全留存於 `pipeline/council/<id>/`（question、各輪各引擎答案、
   tasks、verdict、meta.json 含匿名代號對照 —— 可稽核）

## Runtime state（每專案，git 可追蹤）
- `pipeline/state/engines.json` — 各 role 最後一次由哪個引擎執行（稽核 + cross-check 依據）
- `pipeline/logs/<ts>-<role>-<engine>-<attempt>.log` — 每次執行完整 stdout/stderr

## Dispatcher 行為
1. 由 cwd 向上尋找 pipeline.yaml
2. engine chain = role.engine + role.fallback
3. `cross_check: true` 且 role 有 `differ_from: <role>` 時，
   跳過該 role 上次使用引擎的**同 family 引擎**（保證異質 review）。
   同一模型的不同接入方式（codex CLI vs 經 CLIProxyAPI 的 codex）必須設
   相同 `family`，否則會發生同模型自審
4. 引擎可設 `env: {KEY: VALUE}`，dispatcher 會傳給 adapter
   （例如同一支 proxyapi.sh 以 `PROXYAPI_MODEL` 跑不同模型）
5. 每引擎先跑 health cmd（未定義則視為存活），失敗跳下一個
6. 每引擎最多 retry 次；全部失敗 -> exit 1，理由印在 stderr（fail loud）

## Adapter 環境變數
dispatcher 會把 pipeline.yaml 的 `test_cmd` / `lint_cmd` 以
`PIPELINE_TEST_CMD` / `PIPELINE_LINT_CMD` 環境變數傳給 adapter；
claude.sh 在 rw 模式據此組 `--allowedTools`（headless 無人可回應權限提示，
不 allowlist 則 coder 跑不了測試）。

## 已知假設 / 待驗證
- claude `--permission-mode plan` 視為唯讀；若你的版本行為不同，改 adapters/claude.sh
- coder 若需要 test/lint 以外的指令（如 `npm install`），仍會被擋 —— 屬刻意最小權限，
  需要時在 claude.sh 的 ALLOW 陣列加項
- health cmd 會實際消耗一次極小 API 呼叫；不想耗用可將 health 欄位留空
- 未實作：quorum（雙引擎同時 review）、queue 派工、CI workflow —— 刻意留到跑過真實專案後再加

## 已通過的邏輯測試（mock adapter）
- 首選引擎失敗 x retry -> failover 到 fallback ✅
- cross_check 封鎖 coder 同引擎、fallback 也失敗時 fail loud ✅
- state 檔正確記錄 role -> engine ✅

## 驗證進度（2026-07-03，真實引擎 smoke test）

### agentic CLI
- ✅ claude `--permission-mode plan` headless 唯讀（architect 跑完 git status 乾淨）
- ✅ claude coder rw：acceptEdits + `--allowedTools`（PIPELINE_TEST_CMD/LINT_CMD）真實跑通 test/lint
- ✅ codex reviewer：verdict 合規、state 正確；read-only sandbox 實測擋下寫檔
- ✅ family cross-check + fail loud：codex 掛時 reviewer 正確拒絕 claude 自審、exit 1

### CLIProxyAPI（proxyapi.sh，http://192.168.88.115:8317）
- ✅ 全 27 個模型探測完成（23 個 chat 候選中 20 個可呼叫）
- ✅ architect 經 proxy 真實跑通且輸出合規：gpt-5.5、deepseek-v4-pro、qwen3.6（GB10 地端）
- ✅ reviewer verdict 合規：codex-auto-review、gemini-pro-agent
- ❌ kimi-k2.7-code 跑 reviewer 回 agent 式開場白、無 verdict ⇒ opencode 層不派 gate 角色
- ✅ coder(rw) 派 proxy 引擎正確 exit 1 fail loud（text-only by design）
- ✅ health 改為對「該模型」1-token 呼叫（共用 /v1/models 檢查擋不住單一模型 503）

### 上游來源分層（詳見 pipeline.yaml.example 註解）
| 層級 | 模型 | 策略 |
|---|---|---|
| 穩定 | claude-*（Claude 訂閱）、gpt-5.5 / codex-auto-review（2× Codex 訂閱，proxy 自帶輪替） | 主力與 gate 首選；codex CLI 直呼單扣 business 月配額 ⇒ 降為各鏈殿後 |
| 中等 | gemini-* / gpt-oss-120b（Antigravity，單一 gmail 配額） | 異質備援、量大低風險角色 |
| 易爆 | deepseek / glm / kimi / minimax / qwen3.7-*（opencode go，qwen3.7 實測 503） | 僅深度備援；不設 health；不派 gate |
| 地端 | `-mtp` 系列（GB10，一次只載一個模型） | 唯一可碰敏感資料的引擎 |

### Council 模式（2026-07-03 新增並實測）
- ✅ 完整流程真實跑通：panel=[claude-proxy, gpt-proxy, gemini-pro]、1 輪辯論、
  judge=claude-proxy，問題為 PostgreSQL timestamp/timestamptz 選型
- ✅ 辯論有實質效果：r2 各成員的 Rebuttals 明確吸收/反駁彼此論點
- ✅ 仲裁合規：逐主張標注支持度（3/3、僅 A 提出）、verdict: consensus、exit 0
- 未測：split 路徑（exit 3）、rounds>=2、panel 成員中途故障的 carry forward

### 下一步
- 套用到 StudyPlan（~/projects/studyplan）→ zeroshot/tutti 對照評估（requirement.md §6）
- council split 路徑與故障注入測試
