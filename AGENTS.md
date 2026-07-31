# JenAI — AI 開發代理共用規範

本檔是 Codex、Claude Code、Cursor 與其他 AI 開發工具的共同工程規範。特定工具的
設定檔只能補充角色，不得覆寫本檔、現行架構或已接受的 ADR。

## 開始工作前

依序閱讀：

1. `CONTEXT.md`：產品領域、正式術語與使用者可見語言。
2. `docs/ARCHITECTURE.md`：現行產品邊界、模組責任、seam 與依賴方向。
3. 與任務直接相關的 ADR、測試及操作文件。
4. 目前 Git diff、分支與測試狀態。

工程事實的優先順序為：

```text
原始碼與可重現測試
→ 已接受的 ADR 與 ARCHITECTURE
→ 任務規格與正式操作文件
→ Git history、Issue 與 PR
→ 模型推論或外部研究摘要
```

過期設計、未確認想法及模型記憶不得覆蓋現行工程事實。

## 協作角色

- **AI Agent = SWE**：負責被指派範圍內的分析、實作、測試、審查、文件與 release 工作。
- **使用者 = Product Owner／客戶端**：負責產品方向、必要批准、使用回饋及需要 GUI／現場的
  人工作業，不應被要求代寫程式。
- Isaac Sim GUI、場景 Replay 與實體載具周邊操作若無可靠 automation interface，由使用者
  執行；Agent 必須提供短而明確的步驟，並在完成後接手其餘驗證。
- 多個 AI 透過 task、plan、Git diff、測試與 review finding 交接，不靠彼此的私有記憶或
  無限對話維持狀態。

## 產品與架構鐵律

- JenAI 是高階機器人決策與 Workflow Agent。LLM 理解意圖並選擇已註冊 Capability，
  不直接控制速度、轉向、關節、局部避障或即時安全迴路。
- 正常長任務由確定性 Workflow 負責順序、有限重試、取消、證據、完成判定及返航；
  不得在每個導航點重新詢問 LLM。
- `workflows/` 不得依賴 ROS 2、Nav2、Isaac Sim、模型 provider、TUI、WebUI 或 CLI。
- 所有產品移動入口必須通過共用 Navigation Gateway、批准／政策及結果驗證，不得在
  TUI、WebUI、MCP、daemon、Runtime 或一般 script 重寫第二套導航流程。唯一例外是
  [ADR 0007](docs/adr/0007-simulation-differential-control-arm.md) 明確限定的 Isaac Sim
  `R1_bridge_nav2` 差分對照組；它只屬於 acceptance instrumentation，不是 Capability、
  產品入口、實體載具路徑或成功證據。
- Capability 與 Workflow 使用平台無關語言；載具差異只存在於 vehicle profile 與 adapter。
- 安全預設、急停、批准、隔離、watchdog 與誠實回報行為只准收緊，不得倒退。
- LLM 輸出一律視為不可信輸入，必須通過 schema、Capability Registry、參數、狀態、
  policy 與批准檢查。
- 程式正常結束不等於任務成功。Task outcome 必須由 Nav2 結果、終點姿態、感測證據、
  coverage、取消／政策或返航結果支撐。
- 缺少證據時必須回報未驗證、部分完成、不可用、阻擋或失敗；不得偽裝成功。
- 純 Isaac Sim 證據不得延伸宣稱為實體安全、跨載具泛化或物理充電成功。

## 使用者可見語言

- 所有中文介面、訊息與正式操作文件使用臺灣繁體中文。
- 程式識別字、ROS 2 名稱、CLI、路徑、數字與單位維持英文原文。
- 簡體中文只能存在於隱藏輸入別名或相容性 regression fixture，不得出現在輸出。

## 修改流程

修改前：

1. 先確認問題屬於 domain、application、adapter、UI 或驗收哪一層。
2. 讀現有 interface 與測試；優先深化既有 module，不建立重複 abstraction。
3. 對 bug 先建立能重現外部行為的 regression test。
4. 若需求會改變公開 interface、產品邊界或 completion contract，先更新／新增 ADR。

修改後：

1. 執行受影響的單元、整合與架構測試。
2. 執行格式、lint、strict type check 與完整測試門檻。
3. 對 ROS 2／Nav2／TUI 變更依 `docs/validation/TEST.md` 驗證受影響的真實鏈路。
4. 更新正式文件與限制；不得把未執行項目寫成已通過。
5. 檢查無死碼、重複功能、臨時檔、祕密、論文或實驗 artifact 被提交。
6. 先完成 Code Review 並修正 blocking findings，才建立 PR。

常用本機品質閘門：

```bash
env -u PYTHONPATH uv run ruff format --check .
env -u PYTHONPATH uv run ruff check .
env -u PYTHONPATH uv run mypy src
env -u PYTHONPATH uv run pytest
```

ROS app 執行時保留 ROS `PYTHONPATH`：

```bash
source /opt/ros/jazzy/setup.bash
source /home/nvidia/IsaacSim-ros_workspaces/jazzy_ws/install/setup.bash
uv run JenAI
```

不要在已 source ROS 的 app shell 中移除 `PYTHONPATH`；這會使 ROS 2 CLI 找不到其 Python
套件。只有執行 venv 單元測試時才使用 `env -u PYTHONPATH`。

## TUI 與 Isaac Sim 驗證政策

四條測試線不可混為一談：

1. **TUI 行為測試**：用 Textual `run_test()`／Pilot 與 fake adapter 驗證輸入、批准、
   queue、取消及 rendering。
2. **Agent routing 測試**：驗證自然語言選擇哪個 Capability 與參數，不證明車輛移動。
3. **Live Isaac HIL**：不用 TUI 或 LLM，直接走共用 Capability／Navigation Gateway，
   驗證 ROS 2、Nav2、Isaac Sim、終點、取消、停止及 evidence artifact。
4. **完整產品驗收**：真 TUI＋自然語言＋批准＋Nav2＋Isaac Sim；用於 release acceptance，
   不以畫面看似完成代替 task receipt 與機器人狀態。

Isaac navigation differential 是 ADR 0007 下的獨立 simulation-only 診斷實驗，不屬於上述
一般 Live HIL 產品路徑。R1 可在完整 identity、明確 motion confirmation、watchdog、T0/T1、
終點 evidence 與 cleanup gate 下直接觀測 bridge→Nav2；R2 仍必須通過 Navigation Gateway。
這項例外不得被 Agent、TUI、WebUI、MCP、daemon、Runtime、NXDog 或實體載具重用。

Live HIL 使用：

```bash
./scripts/isaac_nav2.sh restart
uv run python scripts/isaac_hil_acceptance.py --help
```

- 一般試用若車輛未卡牆、定位健康、`/clock` 前進且舊 goal 已清除，不必 Stop／Play；
  可先 `/dock` 或直接 restart Nav2。
- 車輛卡牆、模擬時間重設、定位明顯漂移、殘留活動 goal，或正式固定基準要求相同起點時，
  才請操作員 Stop／Play。
- 每次 Stop／Play、導航 profile 變更或正式驗收前都要 restart Nav2。
- 第一個 gate 失敗時先保存診斷並停止；不得任意重啟、調參或改碼來把同一次測試變成 PASS。
- pytest、mock bridge 或同 domain Twin 結果不得宣稱為 live、隔離或實體驗證。

完整 runtime、ownership、狀態機與錯誤流程見
`docs/workflow/CURRENT_WORKFLOW.md`。
