# ADOPTION_MODEL — 採用、成本與責任模型

## 已選方向

JenAI 採 **Apache-2.0 開源核心＋專案型整合／訓練／維護服務**。現在只有 best-effort
community support，沒有付費方案或 SLA；在實際量出工時與服務成本前不公告價格。

開源核心包含 TUI／CLI、ROS2 工具、批准與 stop、Twin Gate 介面、Vehicle Profile、
測試與 runbook。未來可收費的不是鎖住安全修補，而是場域 adapter、地圖／Nav2 整合、
私有模型接入、導入訓練、驗收報告與約定時段的維護。

## 導入成本表（每個客戶必填）

| 成本項 | 單位 | 估算輸入 | 驗收輸出 |
|---|---:|---|---|
| 主機／GPU | 台／月 | 型號、折舊或租用費、功耗 | 固定模型的 E4 延遲與可用率 |
| 模型 | token 或 GPU-hour | provider 單價、平均 prompt/output、任務量 | 每 100 任務模型成本 |
| ROS／Nav2 接線 | 工程人日 | graph 完整度、topic/action 差異 | doctor 全綠、固定任務通過 |
| Vehicle Profile／adapter | 工程人日 | profile-only 或需程式 adapter | diff LOC、`VEHICLE_POC` 結果 |
| Twin／場景 | 工程人日 | 地圖、禁區、感測器、場景版本 | G1–G5 與場景 artifact |
| 驗收／安全 | 工程人日 | 測試次數、場地、e-stop 人員 | incident 表、stop／cancel 指標 |
| 教育訓練 | 人次×小時 | 新手／熟手比例 | 冷啟動成功率與卡點 |
| 維護 | 人時／月 | ROS、模型、場景更新頻率 | 更新、回滾、事件處理紀錄 |

總持有成本以「硬體＋模型＋首次整合＋驗收＋訓練＋期間維護」計算；不能只報開源授權為
零。尚無實際客戶資料時保持空白，不以假價格製造 ROI。

## 責任分界

| JenAI 專案負責 | 部署／載具方負責 |
|---|---|
| 高階能力選擇、批准流程、誠實結果、audit、軟體 stop／限速 | 底層控制器、感測器、Nav2 調校、硬體 e-stop、現場風險評估 |
| 支援矩陣內的安裝與軟體回歸 | DDS／網路隔離、憑證與 secrets、作業系統修補 |
| 已登錄 adapter 的介面契約 | 載具物理極限、禁區、地圖與安全操作員 |

JenAI 不是 safety PLC 或經認證的功能安全元件。實體事故、法規與場域放行不能轉嫁給
LLM 或模擬結果。

## 商務 gate

1. 未完成 `USABILITY_STUDY` 前，不承諾節省百分比或回收期。
2. 未完成對應 `VEHICLE_POC` 前，不報價為「支援該載具」，只能報評估／整合 PoC。
3. 付費支援上線前，必須定義回應時段、嚴重度、排除事項、人力容量與終止／資料刪除。
4. 單一維護者仍是營運風險；第二位維護者能完成 release＋Isaac 故障演練後才可承諾
   長期維護。
