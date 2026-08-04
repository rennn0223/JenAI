# EPIC-0001 — Navigation

- State: Completed
- Type: Product foundation

## Goal

讓 JenAI 透過共用高階 navigation path 執行已批准的目標，並以 Nav2 結果、終點 Evidence、
取消與 STOP 誠實回報產品結果。

## Deliverables

- Capability Registry 與共用 Navigation Gateway；
- Site／map／location binding；
- Approval、policy、bounded retry、cancel 與 STOP；
- Nav2 terminal result、final pose verification 與 Task Outcome／Receipt；
- TUI、WebUI、MCP、daemon 不直接建立第二套產品 navigation path。

## Definition of Done

既有 v2.6.0 產品基線能走共用 Gateway，區分 completed lifecycle 與 verified outcome，STOP
不依賴 LLM，且模擬 Evidence 不被外推為實體安全。未完成的 navigation 診斷研究不影響此
產品 Epic 的 completed 狀態。

## Non-goals

- Robot Runtime single-authority migration；
- 未知地圖探索；
- 實體安全認證；
- 所有載具的物理泛化。

## Dependencies

ROS 2／Nav2 或等價高階 API、Site Profile、Capability contracts 與操作員安全程序。
