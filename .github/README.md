# .github — CI/CD

| Workflow | 觸發 | 內容 |
|---|---|---|
| `workflows/ci.yml` | PR、push main | `test` job:ruff → pytest(coverage 進 job summary)→ **安全鏈覆蓋閘(fail-under=90)**;`build` job:`uv build` → `uvx` 全新環境裝 wheel 跑 `jenai --help`(抓漏列依賴)→ dist artifact |
| `workflows/release.yml` | 推 `v*` tag | 驗 tag==pyproject 版本 → 重跑 lint+測試 → build + wheel 冒煙測試 → **草稿** release 附 wheel/sdist;concurrency 防同 tag 競爭。發佈:`gh release edit vX.Y.Z --notes-file … --draft=false` |

CI 無 ROS —— 測試套件設計成不依賴 ROS(`tests/unit/fake_bridge.py`)。
