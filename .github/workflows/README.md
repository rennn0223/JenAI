# workflows — CI/CD

| Workflow | 觸發 | 內容 |
|---|---|---|
| `ci.yml` | PR、push main | `test` job:ruff → pytest(coverage 進 job summary)→ **安全鏈覆蓋閘(fail-under=90)**;`build` job:`uv build` → `uvx` 全新環境裝 wheel 跑 `jenai --help`(抓漏列依賴)→ dist artifact |
| `release.yml` | 推 `v*` tag | 驗 tag==pyproject 版本 → 重跑 lint+測試 → build + wheel 冒煙測試 → **草稿** release 附 wheel/sdist;concurrency 防同 tag 競爭。發佈:`gh release edit vX.Y.Z --notes-file … --draft=false` |

| `isaac-hil.yml` | 人工 `workflow_dispatch`，只在 `self-hosted+jenai-isaac` runner | 精確確認字串後才允許 live route；驗 route、cancel、hard stop、可選 Twin verdict，永遠上傳 JSON artifact。push／PR／schedule 不會觸發 |
CI 無 ROS —— 測試套件設計成不依賴 ROS(`tests/unit/fake_bridge.py`)。
Isaac HIL 的一般 CI 只測 runner 邏輯；沒有 self-hosted artifact 前不得寫成 live gate 已通過。
