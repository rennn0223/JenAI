# bridge — rclpy 常駐 sidecar(反射層核心)

venv 看不到 rclpy、ROS 的 PYTHONPATH 又會遮蔽 venv 依賴,所以 ROS 即時能力
走**獨立程序**:`ros_bridge.py` 由 `/usr/bin/python3`(source ROS 後)執行,
與 venv 之間講 newline-delimited JSON over stdio。

| 檔案 | 職責 |
|---|---|
| `ros_bridge.py` | **系統 Python** 下的 rclpy 節點:pose、latched `/map` 單格唯讀採樣、唯讀 nav_plan、nav_send/feedback/cancel、**drive_to_pose（legacy internal bring-up；非 JenAI 高階產品能力，無 Nav2 的 odom→cmd_vel 閉環直驅）**、**halt(先歸零→取消→再歸零)**、**watchdog(client 斷線 >6s 自主停車)**、capture_frame、watch。**此檔絕不 import jenai**(跑在 venv 外);測試用 `tests/unit/fake_bridge.py` 講同一套協定 |
| `_avoidance.py` / `_drive_control.py` / `_safety_order.py` | stdlib-only 的避障判定、直接駕駛狀態機、深度 freshness 與急停順序;bridge 當 sibling import、venv 測試當 package import |
| `_protocol.py` | stdlib-only newline-JSON op dispatcher：集中 request defaults、**精確型別／finite／範圍驗證**與 node method mapping；字串數字、truthy 字串、NaN/Inf、零或負安全超時皆在觸及 ROS 前拒絕；同時支援 system-Python sibling import 與 package 單測 |
| `_wire.py` / `_server.py` | typed newline-JSON frame codec 與 stdin request server；frame 驗證、錯誤隔離、慢速唯讀工作執行緒化 |
| `_watchdog.py` / `_navigation_state.py` | stdlib-only 的 client-liveness watchdog 與導航結果/active-state 判斷;不需 rclpy 即可完整單測 |
| `client.py` | venv 側 asyncio typed client:無 shell interpolation 的 argv spawn(含 ROS_DOMAIN_ID 隔離,供孿生)、request/future 配對與取消清理、reader 故障傳播、唯讀 Nav2 規劃、事件 callback 隔離、configure_safety(watchdog 武裝失敗 = 啟動失敗)。所有致動／watchdog helper 送出前先驗證型別與物理範圍；pose/map/plan/cancel/halt 回應再做精確型別與一致性驗證；字串 `"false"` 不得被當成成功，halt 必須明確回 `halted=true` |

安全語意:這層**不依賴 LLM 與網路**(CI 架構測試強制)—— 上層全掛,
watchdog 照樣停車。

正式產品導航只接受 `route_adapter=nav2` 並走受監督 bridge；`drive_to_pose` 僅供 bridge bring-up 與回歸測試，`NavigationGateway` 會拒絕高階任務使用 `route_adapter=odom`。sidecar、協定或 watchdog 任一不可用時，goal 會被拒絕並回報
`unavailable`；不得降級為無法證明取消結果的 `ros2 action send_goal` 子程序。
