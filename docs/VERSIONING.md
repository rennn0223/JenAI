# VERSIONING — 版本契約(semver)

> v1.0 起生效的相容性承諾;v0.x 期間盡力遵守但保留破壞空間(semver 慣例)。

## Public surface(動這些 = 使用者會痛)

| 面向 | 內容 | 破壞性變更定義 |
|---|---|---|
| CLI | `JenAI` 子命令與參數(doctor/web/mcp/daemon/route/loc/config/providers/models/version) | 移除/改名命令或參數、改變輸出結構(`--json` 類) |
| Slash 指令 | docs/COMMANDS.md 全表 | 移除/改名指令、改變批准語意 |
| config schema | `~/.config/jenai/config.toml`(`[vehicle]` `[twin]` provider/model 綁定) | 欄位改名/刪除、預設值造成行為改變 |
| rules.toml | daemon 規則格式(topic/field/條件/action/`@perception`) | 同上 |
| locations.toml | 地點檔格式(座標/別名/tags) | 同上 |
| MCP 工具 | 工具名稱與參數 schema;`--allow-actions` 語意 | 改名、參數不相容、預設權限放寬 |
| bridge 協定 | JSON/stdio 訊息格式(bridge 是獨立 sidecar,可能被外部叫用) | 訊息欄位不相容 |

**不屬於 public surface**:Python 模組內部 API(`jenai.*` import 路徑)、WebUI HTML/端點、
TUI 畫面排版。這些任何版本都可能改。

## 版本規則

- **MAJOR**:上表任一項破壞性變更;或安全語意改變(批准範圍、閘門行為)一律 MAJOR
- **MINOR**:新功能、新指令、新 config 欄位(有安全預設值)
- **PATCH**:修 bug、文件、內部重構;**安全鏈行為不可在 PATCH 改變**

## 棄用與遷移

1. 棄用先警告一個 MINOR 版(啟動時提示 + release notes),下一個 MAJOR 才移除
2. config/rules/locations 欄位改動必須附自動遷移:載入舊格式 → 警告 + 就地升級,不得直接報錯
3. 安全預設只能收緊不能放寬(例:新動作預設需批准;WebUI auth 不得預設關閉)

## Release 流程

驗收標準見 CLAUDE.md(review → CI → 實測 → 注釋 → 結構 → 文件 → PR+merge+tag+release)。
tag `vX.Y.Z` 必須等於 pyproject `version`(release workflow 強制檢查)。
