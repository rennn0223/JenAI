# doctor — 環境健檢

`JenAI doctor`(CLI)與 `/doctor`(TUI)的實作。`checks.py` 分區檢查:
environment(python/uv/venv)、config、ros2 CLI、**nav**(map/AMCL/laser/
Nav2/cmd_vel 訂閱者 —— WARN 級 + 修法指向 `docs/ONBOARDING.md`)、provider、
locations、webui、twin(啟用時探孿生 domain)。

原則:誠實回報 —— 沒有的後端就 warn/fail 並給修法,絕不假裝 pass。
`--json` 供機器讀(`{overall, items[], checked_at}`)。
