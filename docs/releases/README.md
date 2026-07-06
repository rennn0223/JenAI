# releases — 版本手寫 release notes(單一事實來源)

`vX.Y.Z.md` 與 tag 同名。兩條發佈路(見 `.github/workflows/release.yml`):
tag push → 草稿 → `gh release edit vX.Y.Z --notes-file docs/releases/vX.Y.Z.md
--draft=false` 人工發佈;或 workflow 手動 dispatch(輸入 tag)→ 由 workflow
建 tag 並以本目錄的 notes 檔直接發佈(dispatch 即人工授權,無檔即失敗)。
notes 放這裡的目的:和程式碼一樣走 PR review,並永久版本化在 repo 裡。

寫法照歷版風格(zh-TW):第一行粗體 hook、分節講清楚改了什麼與為什麼、
「驗證」節誠實列出測過什麼、文末 Full Changelog 連結。
