# tools — 技能層:每個能力的純邏輯核心

命名慣例:`*_core.py` 是可單測的純邏輯;`*_agent_tools.py` 把它包成
/run 代理的工具(帶批准點)。**調度只寫一次**的單一出處都在這層:

| 檔案 | 職責 |
|---|---|
| `safety.py` | **急停語意唯一出處**:`halt_robot`/`arm_watchdog`,TUI/WebUI/MCP/daemon 四介面共用 |
| `nav_live.py` | bridge 版導航(回饋/逾時/取消/餵 watchdog);**`navigate_with_fallback` = nav2-vs-CLI 調度唯一出處**,所有導航入口(含 daemon)都過它 → Twin Gate 掛在這裡 |
| `skills.py` | 任務技能:`parse_patrol`/`run_patrol`(循環+每點觀察+失敗續行)、`find_dock` |
| `perception.py` | `PerceptionLoop`:持續相機→VLM→`SceneAnalysis`;**只觀察不動作**,建議動作一律過批准/規則 gating |
| `route_core.py` / `mission_core.py` / `drive_core.py` | 路由解析與執行、多步任務步進、自然語言駕駛解析 |
| `ros2_core.py` | topics/echo/schema/pub/drive 核心;**`[vehicle]` 硬限速夾在執行路徑上,LLM 碰不到夾限值** |
| `vision_core.py` | `capture_and_analyze`(相機→VLM 的唯一出處) |
| `shell_core.py` | shell 執行 + 風險評估(批准卡的素材) |
| `ros2_pkg_core.py` | **自然語言 → ROS2 (ament_python) 套件**:確定性 boilerplate(package.xml/setup.py 永遠可 build)+ LLM 寫 node 主體;`render_package` 純函數可單測;`--build` 生成即 colcon 驗證。`JenAI scaffold` 的核心 —— 從 control agent 邁向 development copilot |
| `decision_core.py` | **M6 決策腦**:`ContextSnapshot` → 封閉動作集單選 `Decision`;越界/幻覺目的地/解析失敗一律降級 refer_to_human —— 無自由文字可達致動 |
| `decision_eval.py` | `JenAI eval`(論文 E1):場景庫 → per-family accuracy / refer rate / unsafe rate |
| `user_skills.py` | 檔案定義技能:`skills/*.toml` → 新 slash 指令;與內建同一張批准卡,保留字拒載 |
| `registry.py` / `summaries.py` / `tracking.py` / `approval_formatters.py` | 工具註冊、輸出摘要、nav 事件關聯、批准卡文案 |

鐵律:此層以上不得出現載具字眼(CI 強制)—— 載具差異收在 `config` 的 `[vehicle]`。
