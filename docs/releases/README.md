# releases — 版本手寫 release notes(單一事實來源)

`vX.Y.Z.md` 與 tag 同名。tag push 後 workflow 建**草稿** release,
發佈走人工閘:`gh release edit vX.Y.Z --notes-file docs/releases/vX.Y.Z.md
--draft=false`。notes 放這裡的目的:和程式碼一樣走 PR review,
並永久版本化在 repo 裡(GitHub release 頁面之外多一份可 diff 的存檔)。

寫法照歷版風格(zh-TW):第一行粗體 hook、分節講清楚改了什麼與為什麼、
「驗證」節誠實列出測過什麼、文末 Full Changelog 連結。
