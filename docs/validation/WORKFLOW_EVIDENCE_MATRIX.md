# Runtime Workflow Claim-to-Evidence Matrix

> 適用版本：v2.6.0
>
> 目的：把 [CURRENT_WORKFLOW](../workflow/CURRENT_WORKFLOW.md) 的主要產品主張對應到
> 現行實作、回歸測試與 runtime 證據。

## 狀態定義

| 狀態 | 意義 |
|---|---|
| `ENFORCED` | 有現行實作與自動回歸測試，違反時會 fail closed 或產生明確非成功結果 |
| `PARTIALLY_ENFORCED` | 只有部分鏈路自動檢查，仍需要人工、另一條測試線或缺少統一關聯 |
| `DOCUMENTED_ONLY` | 有正式操作政策或紀錄，但 runtime 無法自行保證 |
| `NOT_FOUND` | 找不到可信的現行實作或測試 |

Runtime 證據只能證明其實際執行範圍。Unit／fake 測試不能升格為 live Isaac PASS，
dirty TUI session 也不能取代 clean HIL artifact。

## 矩陣

| Claim | Implementation | Enforcing test | Runtime evidence | 狀態 | 已知缺口 |
|---|---|---|---|---|---|
| 明確 Slash 指令不需 LLM 重新理解 | `jenai.tui.command_dispatch.COMMAND_HANDLERS`、`JenAITuiApp._handle_command` | `test_tui_route_shows_card_and_resolves`、`test_tui_dock_routes_to_tagged_location` | `TUI_LIVE_ACCEPTANCE_2026-07-26.md` 的 `/route`、`/dock` | `ENFORCED` | Full TUI transcript 未封存 |
| 自然語言唯讀狀態只有在 `inspect_state` Capability 已註冊時才走 deterministic fast path，且不移動 | `JenAITuiApp._handle_plain_language`、`jenai.agent.run_agent.run_task`、`_show_state_inspection` | `test_plain_language_routes_by_mode`、`test_read_only_fast_path_requires_registered_inspection_capability`、`test_state_request_with_non_actuation_constraint_remains_read_only` | 2026-07-26 唯讀自然語言場次 | `ENFORCED` | 只涵蓋已註冊反射意圖，不代表所有自然語言皆具確定性 |
| 所有高階導航經共用 Navigation Gateway | `jenai.tools.navigation_gateway.NavigationGateway`、`execute_navigation` | `test_owned_gateway_arms_watchdog_before_start_and_closes`、`test_gateway_blocks_an_unregistered_navigation_capability` | 2026-07-26 HIL route checks | `ENFORCED` | 實體載具 adapter 尚未由此證據驗證 |
| Nav2 success 後仍以停止後新鮮 TF 驗證終點 | `jenai.tools.nav_live._verify_nav2_arrival`、`navigate_live` | `test_navigate_live_rejects_nav2_success_outside_endpoint_tolerance`、`test_navigate_live_prefers_post_stop_pose_over_optimistic_feedback`、`test_navigate_live_requires_fresh_map_pose_after_nav2_success` | `isaac-hil-live-v251-prepr-20260726.json`，兩個 route 均儲存位置／朝向誤差 | `ENFORCED` | 只證明 Isaac Nova Carter profile |
| Endpoint mismatch 不得宣告成功 | `jenai.task_results.navigation_output_result`、`jenai.state.task_receipts.classify_outcome`、`_verify_nav2_arrival` | `test_navigate_live_rejects_nav2_success_outside_endpoint_tolerance`、`test_completed_run_with_failed_tool_is_not_reported_as_success` | HIL 失敗樣本與成功樣本均保留 | `ENFORCED` | 無 |
| 端點恢復只能在取消獲確認後有限重送，且每次嘗試均保留結構化證據 | `jenai.tools.nav_live.navigate_live` 的 endpoint recovery contract、`RouteOutput.navigation_attempts`、HIL `navigation_attempts` evidence | `test_navigate_live_retries_one_confirmed_near_endpoint_stall`、`test_navigate_live_does_not_retry_without_confirmed_nav2_cancellation`、`test_navigate_live_bounds_a_failed_endpoint_recovery_retry`、`test_route_goal_preserves_navigation_attempts_in_hil_evidence` | 2026-07-26 pre-PR `map_left_down` 一次有界恢復 | `ENFORCED` | 實體底盤尚未驗證 |
| HIL 第一個 motion failure 後停止後續 goal | `jenai.acceptance.isaac_hil._run_live_goals`、`_run_optional_cancel_exercise` | `test_live_hil_aborts_remaining_motion_after_first_failed_goal`、`test_failed_route_plan_withholds_live_goals`、`test_scan_failure_withholds_live_goals` | `isaac-hil-live-v251-endpoint-recovery-20260726.json` | `ENFORCED` | 無 |
| HIL 無論成功或例外都執行 final halt | `jenai.acceptance.isaac_hil._run_live` 的 `finally`、`_finalize_live_run` | `test_live_hil_aborts_remaining_motion_after_first_failed_goal`、`test_live_hil_records_unconfirmed_final_halt_as_failure` | pre-PR live artifact 的 `final_halt=pass` | `ENFORCED` | software halt 不是硬體 E-stop |
| HIL bridge shutdown 必須寫入結果 | `jenai.acceptance.isaac_hil._finalize_live_run` | `test_live_hil_aborts_remaining_motion_after_first_failed_goal`、`test_live_hil_records_unconfirmed_final_halt_as_failure` | pre-PR live artifact 的 `bridge_shutdown=pass` | `ENFORCED` | 無 |
| HIL artifact 即使 setup／執行失敗也會落盤 | `jenai.acceptance.isaac_hil.run_isaac_hil` 的 `finally` | `test_setup_failure_is_preserved_in_artifact`、`test_artifact_is_append_only_by_default` | `docs/validation/EVIDENCE_LEDGER.md` 列出的成功與失敗 artifact | `ENFORCED` | artifact 不包含完整 TUI transcript |
| Live 執行前需精確確認字串 | `IsaacHilOptions.validate` | `test_live_execution_requires_exact_confirmation` | HIL runbook 與 Actions workflow | `ENFORCED` | 操作員仍需確認場景安全 |
| Scan 必須有可用空間覆蓋且 timestamp 前進 | `jenai.acceptance.isaac_hil._inspect_scan_quality`、`_evaluate_scan_quality` | `test_scan_quality_accepts_live_shaped_forward_scans_and_records_only_summary` 與各 malformed／stale／timeout 測試 | pre-PR artifact：10/10、362 bins、51.7% valid-finite | `ENFORCED` | scan timestamp 前進不等於獨立證明 `/clock` topic 前進 |
| `/clock` 必須持續前進 | 無獨立 `/clock` advancement gate；scan timestamp gate 提供間接證據 | `test_scan_quality_rejects_stale_timestamps_and_changing_frames` | 操作員於實測確認 Play；artifact 儲存 scan timestamp span | `PARTIALLY_ENFORCED` | runner 應新增獨立 `/clock` 雙樣本／期間 gate |
| Replay 後必須 restart Nav2 | `scripts/isaac_nav2.sh restart` 提供單一入口，但無法偵測 GUI Replay | `test_parameter_override_is_rendered_before_nav2_launch`、`test_start_failure_cleans_the_owned_tmux_session` 只驗 restart 本身 | 2026-07-26 TUI／HIL 紀錄明載 Replay 後 restart | `DOCUMENTED_ONLY` | 需要操作員遵守；runtime 不知道 Stop／Play 事件 |
| Nav2 profile 必須與 0.05 m／0.15 rad completion contract 對齊 | `scripts/isaac_nav2.sh`、`scripts/render_nav2_params.py` | `test_default_goal_tolerances_match_the_verified_endpoint_contract`、`test_parameter_override_is_rendered_before_nav2_launch` | 2026-07-26 vtheta15／AMCL／pre-PR HIL | `ENFORCED` | profile 只對 Nova Carter 模擬經驗證 |
| 緊急停止先清 queue 與 pending approvals，再停止移動 | `JenAITuiApp._invalidate_pending_intent_for_emergency_stop`、`ApprovalFlowMixin._reject_pending_approvals_for_emergency_stop`、停止執行路徑 | `test_tui_stop_variants_preempt_and_clear_queue`、`test_tui_natural_language_stop_preempts_and_clears_queue`、`test_tui_emergency_stop_irreversibly_rejects_pending_approvals` | 2026-07-26 TUI 移動中 `/stop` | `ENFORCED` | daemon 入口仍須以自己的回歸測試維持同一語意 |
| TUI、WebUI 與 MCP 的直接急停皆產生 terminal outcome、`emergency_stop` action、audit 與 task receipt | `jenai.state.emergency_stop`、`RobotCommandsMixin._show_stop`、`webui._do_stop`、`mcp_server._stop_robot` | `test_tui_stop_command_halts_robot`、`test_web_stop_records_terminal_outcome_receipt_and_audit`、`test_mcp_stop_labels_unconfirmed_navigation_cancel` | `WEB-STOP-20260730` 工程補充保存真 WebUI route→Nav2 cancel→STOP 的兩個 terminal runs；raw artifact 留本機，metadata 不足故不是正式 HIL | `ENFORCED` | 新證據只驗 WebUI；daemon 目前使用獨立 audit path，TUI／MCP／WebUI 跨介面 live correlation 仍未自動化 |
| 明確否定 `stop`／`halt`／`cancel`／`停止`／`停下`／`停車`／`急停` 時不得觸發 provider-free 急停 | `jenai.tools.emergency_stop.is_emergency_stop_request` 的否定 grammar | `test_negated_or_informational_stop_text_does_not_trigger_reflex`、`test_explicit_stop_request_uses_emergency_stop_reflex` | 無獨立 live 語意 artifact | `ENFORCED` | grammar 只承諾已支援同義詞，不宣稱涵蓋所有自然語言否定表達 |
| 急停 audit scope 依部署模式區分模擬控制與實體載具控制 | `jenai.tools.emergency_stop.emergency_stop_effect_scope`、`EffectScope.ROBOT_CONTROL`、Agent 與直接介面共用 recorder | `test_emergency_stop_run_records_deployment_aware_effect_scope`、`test_tui_stop_command_halts_robot`、`test_tui_physical_robot_control_approval_defaults_to_no` | 尚無實體載具急停 artifact | `ENFORCED` | `robot_control` 僅證明 audit／policy 分類正確，不等於實體 E-stop 驗證 |
| Software stop 區分零速度命令發布、Nav2 取消確認與車體停止觀察 | `HaltReceipt.zero_velocity_command_published`、`motion_stop_observed`、`halt_receipt_evidence` 與各介面共用訊息 | `test_halt_receipt_preserves_confirmed_zero_and_cancel_evidence`、`test_emergency_stop_run_does_not_claim_observed_motion_stop_without_evidence` | 既有 Isaac HIL 另有停止後漂移證據；一般 `/stop` 回執本身沒有 | `ENFORCED` | bridge publish 完成不證明 DDS 接收、controller 套用或實體煞車；實體平台仍需獨立運動／硬體 E-stop 證據 |
| TUI busy submissions 依 FIFO 且 pending approval 期間不偷跑 | `JenAITuiApp._enqueue_submission`、`_start_next_queued` | `test_tui_busy_submissions_run_in_fifo_order`、`test_tui_queue_waits_for_pending_approval` | 無獨立 live queue artifact | `ENFORCED` | live Isaac 只驗最小 smoke，不應故意堆疊移動命令 |
| 批准預設拒絕，Esc 永遠拒絕 | `jenai.tui.widgets.approval_card.ApprovalCard` | `test_tui_p2_host_approval_is_one_shot_and_defaults_to_no`、各 `rejects_on_escape` 測試 | 2026-07-26 受監督批准 | `ENFORCED` | 批准仍是人工責任 |
| Task outcome 與 run lifecycle 分離 | `jenai.state.task_receipts.classify_outcome`、`build_task_receipt` | `test_completed_task_without_explicit_outcome_is_partial`、`test_run_store_preserves_outcome_set_by_domain_tool` | TUI `/report task` 與稽核 `run_finished` | `ENFORCED` | 無 |
| 每個 TUI task receipt 以 `run_id` 儲存且更新同一 run 不重複 | `jenai.state.task_receipts.TaskReceiptStore.save` | `test_task_receipt_store_roundtrip_and_run_store_auto_save` | 2026-07-26 TUI receipt／audit | `ENFORCED` | HIL 與 TUI 是兩條測試線，尚無單一 bundle 自動關聯兩者 |
| Full TUI acceptance 必須把輸入、批准、receipt、Nav2 與 final pose 關聯 | TUI run／receipt 內部有 `run_id`；HIL artifact 另有獨立 `run_id` | TUI、receipt、Nav2 各自有測試，無一個跨層 correlation test | `TUI_LIVE_ACCEPTANCE_2026-07-26.md` 為人工整理 | `PARTIALLY_ENFORCED` | 尚無統一 scenario ID／goal ID／receipt bundle |
| Dock 只證明抵達 approach pose，不宣稱充電 | `navigation_output_result`、Dock task outcome／摘要 | `test_tui_dock_routes_to_tagged_location`、task receipt outcome tests | 2026-07-26 `/dock` 實測 | `ENFORCED` | Isaac 無 charging signal，不能驗物理充電 |
| 同 domain Twin 不得宣稱隔離 | `IsaacHilOptions` 與 `_twin_isolation_check` | `test_same_domain_pure_sim_bypasses_only_twin_rehearsal`、`test_require_twin_needs_live_execution` | pre-PR HIL `twin_isolation=skip` | `ENFORCED` | separated-domain Twin 尚未正式重跑 |

## 尚未關閉的產品缺口

1. **獨立 `/clock` gate**：現行 scan timestamp gate 能阻止靜止／陳舊感測資料，但
   `CURRENT_WORKFLOW` 所寫的 `/clock` 前進仍沒有直接 machine check。
2. **Replay 事件可觀察性**：Isaac GUI 的 Stop／Play 不會通知 JenAI，因此「Replay 後
   restart Nav2」目前是 operator contract。
3. **Full TUI bundle**：task receipt、TUI timeline、Nav2 goal/result 與 final pose 尚未由
   一個 scenario runner 以共同 ID 封存。
4. **場景 identity**：Nova Carter 範例由 GUI 選單載入，完整 USD path 未固定；正式
   baseline 仍依操作員選對場景。

這些缺口不否定既有 HIL runner。優先作法是深化現有 acceptance module 與 artifact schema，
而不是建立第二套 Navigation Gateway 或另一個 Agent framework。
