# jenai — 主套件

載具無關的任務層大腦:自然語言 → 安全閘控 → ROS2 動作。分層地圖(細節見
`docs/product/PROJECT_DIRECTION.md`、`docs/TECHNICAL_GUIDE.md`):

```
tui/ · webui/ · cli/ · mcp_server/   介面層(四個門,同一套批准哲學)
agent/ · providers/                  決策層(LLM;永不進即時迴路)
twin/                                閘門層(導航先在數位孿生預演)
tools/                               技能層(每個能力的純邏輯核心)
daemon/                              反射層(規則引擎,不依賴 LLM)
adapters/ · bridge/                  橋接層(ros2 CLI / rclpy sidecar)
config/ · schemas/ · state/ · doctor/ 橫切(設定、模型、狀態、健檢)
```

鐵律(`tests/unit/test_architecture.py` 在 CI 強制):反射層禁 import LLM 堆疊;
技能層以上禁載具字眼 —— 載具差異只准活在 `config` 的 `[vehicle]` profile。
