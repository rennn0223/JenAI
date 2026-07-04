# tests/unit — 單元測試

一個 `test_<模組>.py` 對一個功能面;幾個特別的:

| 檔案 | 為什麼特別 |
|---|---|
| `fake_bridge.py` | **不是測試**:純 stdlib 的 bridge 假程序,講與 `ros_bridge.py` 相同的 JSON/stdio 協定 —— bridge/daemon 測試不需要 ROS 的關鍵 |
| `test_architecture.py` | 架構鐵律(反射層禁 LLM import、技能層以上禁載具字眼)—— 違反 = CI 紅 |
| `test_bridge_client.py` / `test_daemon.py` / `test_twin_gate.py` | 含**故障注入**(V1_GATE A4):啟動失敗、預演中斷、halt 失敗 → 全部驗證誠實降級 |
| `test_review_fixes.py` | 歷次 review findings 的回歸鎖 |
| `test_tui.py` | Textual `app.run_test()` + `handle_user_text()` 驅動真 App |

慣例:安全鏈路徑(estop/watchdog/gate/approval)的覆蓋有 CI 倒退閘守著,
改這些模組請同步補測試。
