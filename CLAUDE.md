# JenAI — 專案工作規範

回覆使用中文(zh-TW);程式碼、識別字、CLI 指令維持英文。

## 角色分工

- **Agent = SWE**:負責實作、測試、審查、文件、release 全流程。
- **使用者 = 客戶端**:負責驗測與使用回饋,不寫程式。驗證以 **Isaac Sim 為主**
  (DGX Spark 工作站 GUI);實體操作(車邊、場域)屬選配/交接下一屆。
  需要人工操作的事項一律交給使用者,列清楚步驟。
- 分層待辦見 `docs/product/V1_GATE.md`。

## 驗收標準(Definition of Done)— 每次改動都要走完

1. **Code review**:改完程式碼先跑 `/code-review`(inline,不 spawn subagent),修完 findings
2. **CI/CD 跑通**:`test` + `build` job 全綠
3. **功能真的能用**:照 `docs/validation/TEST.md` 實測受影響的鏈路,不能只靠單元測試綠
4. **補注釋**:新程式碼補上必要注釋(講不變量與 why,不講 what)
5. **結構整潔**:不留冗餘功能、死碼、用不到的檔案
6. **文件修齊**:TECHNICAL_GUIDE / COMMANDS / TEST.md 等對齊實況
7. 全過 → **PR → merge → tag → release**(notes 用 zh-TW,照歷版風格)

## 測試與執行環境(三種組合,別搞混)

- 跑測試:`env -u PYTHONPATH uv run pytest`(ROS 的 PYTHONPATH 會遮蔽 venv 依賴)
- 跑 app:`source /opt/ros/jazzy/setup.bash` 後直接 `uv run JenAI …`(**保留** PYTHONPATH)
- 壞組合:source ROS 又 unset PYTHONPATH → ros2 CLI 會 exit 1

## 鐵律(見 docs/product/PROJECT_DIRECTION.md)

- LLM 永不進即時迴路;反射層永不依賴 LLM 與網路
- 技能層以上不得出現載具字眼;載具差異全收在 vehicle profile
- 每層失敗誠實回報,不得偽裝成功;安全鏈行為不可倒退
