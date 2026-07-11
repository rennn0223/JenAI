# bridge — rclpy 常駐 sidecar(反射層核心)

venv 看不到 rclpy、ROS 的 PYTHONPATH 又會遮蔽 venv 依賴,所以 ROS 即時能力
走**獨立程序**:`ros_bridge.py` 由 `/usr/bin/python3`(source ROS 後)執行,
與 venv 之間講 newline-delimited JSON over stdio。

| 檔案 | 職責 |
|---|---|
| `ros_bridge.py` | **系統 Python** 下的 rclpy 節點:pose、nav_send/feedback/cancel、**drive_to_pose(無 Nav2 的 odom→cmd_vel 閉環直驅,選配 depth stop-and-go detour)**、**halt(先歸零→取消→再歸零)**、**watchdog(client 斷線 >6s 自主停車)**、capture_frame、watch。**此檔絕不 import jenai**(跑在 venv 外);測試用 `tests/unit/fake_bridge.py` 講同一套協定 |
| `_avoidance.py` / `_safety_order.py` | stdlib-only 的避障判定、深度 freshness 與急停順序;bridge 當 sibling import、venv 測試當 package import |
| `_watchdog.py` / `_navigation_state.py` | stdlib-only 的 client-liveness watchdog 與導航結果/active-state 判斷;不需 rclpy 即可完整單測 |
| `client.py` | venv 側 asyncio client:spawn(含 ROS_DOMAIN_ID 隔離,供孿生)、request/future 配對、事件路由、configure_safety(watchdog 武裝失敗 = 啟動失敗) |

安全語意:這層**不依賴 LLM 與網路**(CI 架構測試強制)—— 上層全掛,
watchdog 照樣停車。
