# ROLLBACK — 升級與回滾手冊

## 回滾前

1. 停止進行中的任務，確認載具靜止；實體部署使用硬體 emergency stop。
2. 關閉 TUI、WebUI、daemon 與背景 benchmark。
3. 備份 `~/.config/jenai/`，但不要把 `.env` 或場域資料上傳到 issue。
4. 記錄目前的 `JenAI version` 與 `JenAI doctor` 輸出。

## uv tool 安裝

從 GitHub release 下載目標版本 wheel，然後：

```bash
uv tool install --force /path/to/jenai-X.Y.Z-py3-none-any.whl
JenAI version
JenAI doctor
```

要回到目前 repo 版本：

```bash
cd ~/JenAI
uv tool install --force .
JenAI version
```

## 原始碼開發模式

```bash
cd ~/JenAI
git status --short
git switch --detach vX.Y.Z
uv sync --frozen
uv run JenAI version
uv run JenAI doctor
```

不要在有未提交修改的工作樹直接切版本。回到最新版使用 `git switch main` 與
`git pull --ff-only`。

## 驗收

- 無 ROS：版本、help、onboard/doctor、provider 連線與 TUI 啟動。
- Isaac Sim：依 `docs/TEST.md` 跑一個唯讀檢查、一個批准流程、一個 `/route`、取消與
  `/stop`。
- 實體載具：先在架空／低速／隔離場地做 smoke test，硬體 stop 操作者在場。

若舊版無法讀取新版設定，使用 setup wizard 產生的備份，或在保留 `.env` 與 locations
的前提下重新執行 `JenAI onboard`。不要手動修改 audit SQLite schema。
