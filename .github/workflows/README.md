# workflows — CI/CD

| Workflow | 觸發 | 內容 |
|---|---|---|
| `ci.yml` | PR、push main | `test` job：ruff → pytest（coverage 進 job summary）→ **安全鏈覆蓋閘（fail-under=90）**；`build` job：`uv build` → fresh `uv tool` 生命週期（wheel 唯一性、`JenAI version`、`jenai --help`、無設定 doctor、uninstall）→ dist artifact |
| `release.yml` | 推 `v*` tag；或人工 dispatch | 驗 tag、trigger SHA、remote tag 與 pyproject 版本一致 → lint／完整測試／安全 coverage／dependency audit → reproducible build → sdist 敏感檔掃描 → matching constraints、CycloneDX SBOM、SHA256SUMS、provenance 與 SBOM attestations → fresh wheel lifecycle → draft／人工發布 |
| `isaac-hil.yml` | 人工 `workflow_dispatch`，只在 `self-hosted+jenai-isaac` runner | 精確確認字串後才允許 live route；驗 route、cancel、hard stop、可選 Twin verdict，永遠上傳 JSON artifact。push／PR／schedule 不會觸發 |

目前 repository 是 private；只有已授權且完成 `gh auth login` 的協作者能以
`gh release download` 取得 assets，未授權使用者沒有公開下載通道。Release consumer
應同時下載同一版的 `jenai-X.Y.Z-py3-none-any.whl`、
`jenai-X.Y.Z-constraints.txt` 與 `SHA256SUMS`，驗證後才執行 `uv tool install --constraints`；
完整命令見 [QUICKSTART](../../docs/QUICKSTART.md) 與 [ROLLBACK](../../docs/ROLLBACK.md)。
GitHub attestation 驗證是新 release 實際發布該證據後的選配第二層，不能套用到缺少
attestation 的舊版。release workflow 亦拒絕把 DOCX／PDF／PPTX、thesis source/media、
credentials、agent workspace 或實驗 artifact 包進 sdist；論文不屬 GitHub release 交付物。

CI 無 ROS —— 測試套件設計成不依賴 ROS（`tests/unit/fake_bridge.py`）。
Isaac HIL 的一般 CI 只測 runner 邏輯；沒有 self-hosted artifact 前不得寫成 live gate 已通過。
