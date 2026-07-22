# schemas — pydantic 資料模型(全部 `extra="forbid"`)

跨層共用的資料形狀,一律嚴格驗證:

| 檔案 | 內容 |
|---|---|
| `models.py` | `Location`(pose/aliases/tags)、`RunRecord`/`ApprovalRequest`(批准流)、`DoctorResult`、`SceneAnalysis`(感知輸出:場景/物件/affordances/建議動作/信心/需批准旗標 —— 解析寬容但**安全旗標絕不 fail-open**) |
| `outputs.py` | 工具輸出:`RouteOutput`(execution_status + route_preview —— 誠實回報的載體)等 |

改欄位前看 `docs/operations/VERSIONING.md`:部分模型序列化進 config/locations/reports,
屬 public surface。
