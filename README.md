# JenAI

> An agentic AI terminal assistant for ROS2-based robot systems.

JenAI 是一套以 terminal 為核心的 AI Agent 操作介面，專為機器人開發者設計。它整合大型語言模型、ROS2 工具鏈、視覺理解能力與 human-in-the-loop 批准機制，讓你能以自然語言規劃、執行、監控機器人任務。

---

## 核心能力

- **自然語言任務規劃與執行**：以 `/plan` 與 `/run` 驅動 agent 完成多步驟任務
- **可見但不洩露私密思維的執行進度**：TUI 即時顯示理解、工具執行、驗證與完成階段；狀態查詢的量測數字由工具確定性產生，LLM 不得改寫
- **ROS2 整合**：topics 探索、schema 解析、echo 監看、pub 控制
- **即時導航**：`/route`、`/mission` 走 Nav2，剩餘距離即時顯示、Esc 真的取消 goal（rclpy bridge）
- **有界自主巡遊**：`/explore` 在已儲存安全點位間低重複率隨機導航，具時間、目標數與連續失敗上限；自然語言 `/run` 亦可觸發同一能力
- **地點管理**：`/loc add here <名字>` 抓機器人當下位置存檔，邊走邊建地圖點位
- **視覺理解**：`/vision image <路徑>` 分析圖片；`/vision camera` 直接抓相機畫面問「你看到什麼」
- **持續感知**：`/perception start` 相機→VLM 定頻迴圈，輸出結構化場景分析（affordances 可觸發 daemon 規則；只觀察，動作一律走批准）
- **模型雲地隨切**：`/provider`、`/model` 即時切換 NVIDIA 雲端／本機 Ollama，含編號快選
- **緊急停止**：TUI `/stop`、WebUI 紅色 STOP 鈕、MCP `stop` 工具——取消導航 + 送零速度,不需批准、忙碌中也能搶佔；bridge 端 watchdog 在 client 斷線時自動停車
- **Human-in-the-loop 批准機制**：敏感操作一律暫停等待人工核准，Enter 批准、Esc 拒絕
- **TUI + WebUI 雙介面**：terminal 優先；WebUI 有對話 console、即時地圖、手機批准
- **daemon 常駐模式**：`jenai daemon` 規則觸發（如電量低回充），預設只通報、明確授權才動作
- **MCP server**：`jenai mcp` 把機器人工具開放給 Claude Code／Desktop 等 MCP client（預設唯讀，`--allow-actions` 才開放導航）
- **權限三模式**：TUI Shift+Tab 循環切換「審批／規劃／自動」——裸自然語言依模式路由（規劃模式只產計畫不執行；自動模式只自動批准有界、非 host 的 P0/P1 動作並明示，HOST_COMMAND/P2 仍逐次詢問），急停與硬限速在任何模式都不放鬆
- **Development copilot**：`JenAI scaffold "<描述>"` 自然語言生成 ROS2 套件（boilerplate 確定性生成 + LLM 寫 node 主體 + 送出前審閱；`--build` 生成即 colcon 驗證）；`skills/*.toml` 檔案定義技能擴充 slash 指令
- **決策核心與評測**：`decision_core` 有界動作集單選決策（越界一律降級 refer）+ `JenAI eval` E1 場景評測（accuracy／unsafe rate／refer rate）
- **巡邏日報**：`/report` 確定性日報 + LLM 摘要（離線誠實降級），`/report list` 回看歷次

---

## 快速入門

```bash
# 開發環境
uv sync
uv run pytest

# 首次執行（自動進入 setup wizard）
uv run JenAI

# 重新設定（先備份 config；保留 .env 與 locations）
uv run JenAI onboard

# 環境健康檢查
uv run JenAI doctor

# 啟動 WebUI 監控中心
uv run JenAI web
```

### 在新機器上安裝（建議：不可變 Release wheel）

目前 `rennn0223/JenAI` 是 **private repository**。只有已獲授權、且已用 GitHub CLI
登入的協作者能從 Releases 安裝；未獲 repository 權限的使用者目前沒有公開下載通道，
不能把匿名 `curl` 當成可交付的安裝方式。若未來改為公開發行，才另提供公開下載流程。

新版 workflow 會為 private release 產生 CycloneDX SBOM 與 `SHA256SUMS`；只有這些 assets 實際出現在該 GitHub Release 時才視為已發布，且 private path 不會產生或宣稱 GitHub artifact attestations。

已授權協作者請選定正式版本，同時下載該版本的 wheel、constraints 與 `SHA256SUMS`。
三者必須是**同一個 release**；constraints 固定該版通過發布閘的依賴解析，checksum 用來
確認下載資產與同一份 manifest 一致。private path 缺少簽署的 provenance，不能據此證明
來源真實性或抵禦 manifest 與資產一同遭替換。只有 asset 清單實際包含這三項的 release
才適用此流程；例如既有
`v1.1.4` 缺少 constraints 與 checksum，不能推定已受這套供應鏈閘驗證；目前請使用已通過此流程的 `v2.0.1` 或後續資產完整版本。

下列已驗證的 copy-paste 流程以 Linux／Ubuntu 為目標，使用系統提供的 GNU
`sha256sum`。macOS 在 [SUPPORT_MATRIX](docs/SUPPORT_MATRIX.md) 仍是 Experimental；可自行
安裝 GNU coreutils 並把命令換成 `gsha256sum`，但不因此取得與 Linux 相同的驗證等級。
在提示中輸入 release tag 去掉 `v` 後的版本號，例如 `vX.Y.Z` 就輸入 `X.Y.Z`：

```bash
read -r -p "JenAI release version (without v): " VERSION
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "invalid version"; exit 2; }
command -v gh >/dev/null || { echo "GitHub CLI (gh) is required"; exit 2; }
gh auth status
INSTALL_DIR="$HOME/Downloads/jenai-$VERSION"
mkdir -p "$INSTALL_DIR"
gh release download "v${VERSION}" --repo rennn0223/JenAI --dir "$INSTALL_DIR" --pattern "jenai-${VERSION}-py3-none-any.whl" --pattern "jenai-${VERSION}-constraints.txt" --pattern "SHA256SUMS"
cd "$INSTALL_DIR"
grep -Fq " *jenai-${VERSION}-py3-none-any.whl" SHA256SUMS
grep -Fq " *jenai-${VERSION}-constraints.txt" SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS
uv tool install \
  --constraints "jenai-${VERSION}-constraints.txt" \
  "./jenai-${VERSION}-py3-none-any.whl"
uv tool update-shell
```

只有 repository 在該版發布時為 **public**、release 說明明確標示已發布 GitHub
attestation，且 assets 實際包含 `.sigstore.json` bundles 時，才可在 checksum 通過後
選配第二層驗證（電腦須安裝支援 `gh attestation` 的新版 GitHub CLI 並已登入）：

```bash
gh attestation verify "jenai-${VERSION}-py3-none-any.whl" \
  --repo rennn0223/JenAI
gh attestation verify "jenai-${VERSION}-constraints.txt" \
  --repo rennn0223/JenAI
```

這是 public release 才可能具有的選配第二層驗證；private 或舊 release 沒有 attestation 時，不能執行或宣稱這一層已通過。

重開 shell 後，`JenAI version` 與 `jenai --help` 都應可執行。wheel 會建立大小寫兩個
entry point，不需要另寫 shell wrapper 或 symlink。若曾把 repo 的 `scripts/jenai` 連到
`~/.local/bin/jenai`，要先依[下節](#repo-開發啟動腳本非一般安裝方式)移除舊連結，不能與
wheel 的小寫 entry point 共存。

有三個檔案**不在 repo 裡**（使用者設定／機密），換機器後要重建：

| 檔案（`~/.config/jenai/`） | 怎麼來 |
|---|---|
| `config.toml`（provider／model） | 首次 `JenAI` 自動跑 setup wizard 建立 |
| `.env`（API 金鑰） | 手動一行（見下方「API 金鑰」）；JenAI 啟動時自動載入 |
| `locations.toml`（地點） | 依 [`locations.example.toml`](locations.example.toml) 填 |

需要從原始碼安裝時，只使用已審閱的**精確 tag 或完整 commit SHA**，並記錄解析出的
commit。此路徑在支援矩陣列為 Supported，尚未等同 release wheel 的隔離環境驗證：

```bash
read -r -p "Reviewed tag (vX.Y.Z) or full commit SHA: " REF
if [[ ! "$REF" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ \
   && ! "$REF" =~ ^[0-9a-fA-F]{40}$ ]]; then
  echo "REF must be vX.Y.Z or a full 40-character commit SHA"
  exit 2
fi
git clone https://github.com/rennn0223/JenAI.git ~/JenAI
cd ~/JenAI
SOURCE_SHA="$(git rev-parse --verify "${REF}^{commit}")"
git switch --detach "$SOURCE_SHA"
test "$(git rev-parse HEAD)" = "$SOURCE_SHA"
printf "Source commit: %s\n" "$SOURCE_SHA"
uv tool install .
uv tool update-shell
```

開發者要讓原始碼修改立即生效，則保留 repo 工作流：`uv sync` + `uv run JenAI`；
CI／重播已鎖定環境時使用 `uv sync --frozen`。一般使用者不需要 clone repo。

- **ROS2 是選配**：沒裝 ROS 的機器，`/ros*`、`/drive`、`/route` 會誠實回報 unavailable（不會崩），聊天／`/plan` 照常；控真車才需要 ROS2 Jazzy + Nav2。
- 需要網路 + 金鑰（或本機 Ollama）才能實際呼叫模型；雲端 provider 會收到該請求的 prompt/任務上下文，vision 會送出完整選定圖片或相機幀。敏感場域請用本機 endpoint，詳見 [SECURITY.md](SECURITY.md)。

### Repo 開發啟動腳本（非一般安裝方式）

啟動腳本 [`scripts/jenai`](scripts/jenai) 是 source checkout 專用的開發便利工具，會在
「還沒有可用的 ROS 環境」時 source ROS2（已經 source 好的環境——包括其他發行版——
會被尊重，不會疊加）、確保 uv，再用 `uv run` 啟動。它不是 wheel 的一部分。只在
**未安裝 wheel tool** 時才可選擇連到 PATH：

```bash
mkdir -p ~/.local/bin
ln -s "$PWD/scripts/jenai" ~/.local/bin/jenai
jenai            # source ROS2 → 進 TUI
jenai doctor     # 環境檢查
jenai web        # WebUI 儀表板
# 覆寫路徑：JENAI_DIR=/path/to/JenAI ROS_SETUP=/opt/ros/humble/setup.bash jenai
```

這個連結與 `uv tool install` 所管理的 `~/.local/bin/jenai` 同名。切換成 wheel 前先執行
`ls -l ~/.local/bin/jenai`，確認它確實指向 repo 的 `scripts/jenai` 後再移除該 symlink；
不要用 `ln -sf` 蓋掉 wheel entry point。wheel 使用者若要 ROS2，
先在同一個 shell 執行 `source /opt/ros/<distro>/setup.bash`，再直接執行 `JenAI`。

### 更新、回滾與解除安裝

更新與回滾都使用目標版本的 wheel、matching constraints 與 `SHA256SUMS` 重新驗證，再以
`uv tool install --force --constraints ... <wheel>` 替換 tool；不要混用不同 release 的
檔案，也不要從移動中的 `main` 回滾。完整可執行流程見 [ROLLBACK](docs/ROLLBACK.md)。

解除安裝前可先安全匯出本機資料；package 移除**不會刪除** `~/.config/jenai`：

```bash
JenAI data status
JenAI data export "jenai-data-$(date +%F).tar.gz"
JenAI data purge --dry-run
uv tool uninstall jenai
```

匯出內容、預設 purge 保留項目與明確完整清除選項見
[DATA_LIFECYCLE](docs/DATA_LIFECYCLE.md)。

**API 金鑰用 `.env`（建議）**：把 provider 金鑰放在 `~/.config/jenai/.env`
（`chmod 600`，跟 `config.toml` 同目錄），**JenAI 啟動時自動載入**——不論用
`jenai`、`uv run JenAI` 還是 venv script 啟動都一樣。shell 已 export 的變數
優先於檔案內容。這比寫在 `.bashrc` 好——不受「互動 shell 才載入」限制。
setup 欄位預期填 `NVIDIA_API_KEY` 這類變數名稱;若誤貼 key 本體,v0.25.1 起會
自動搬到權限 `0600` 的 `.env`,不再把 secret 留在 `config.toml`：

```bash
printf 'NVIDIA_API_KEY=nvapi-…\n' > ~/.config/jenai/.env && chmod 600 ~/.config/jenai/.env
# 覆寫路徑：JENAI_ENV_FILE=/path/to/.env jenai
```

### 載具設定（`[vehicle]`）

Vehicle Profile 是載具差異的第一個設定邊界。若新平台已提供相同高階 capability schema，
通常只需調整 topic、訊息封裝與速限；若介面或運動能力不同，仍需薄 adapter，並依
[`VEHICLE_POC`](docs/VEHICLE_POC.md) 重新驗收，不能由設定檔推定物理泛化：

```toml
[vehicle]
type = "ackermann"          # ackermann | diff | quadruped
domain_id = 20              # 實體載具部署 domain；目前程序仍由 ROS_DOMAIN_ID 決定控制哪側
cmd_vel_topic = "/cmd_vel"
cmd_vel_stamped = false     # true 時發 TwistStamped
camera_topic = "/camera/image_raw"   # /vision camera 與 MCP camera_look 預設
max_linear = 1.0            # m/s — 執行期硬限速(LLM/使用者給再大都會被夾住)。
max_angular = 2.0           # rad/s — 以上為安全預設;依你的車實測後再調
                            # (例:Leatherback 用 2.0 / 0.53)
```

### 使用本地 Ollama

Ollama 提供 OpenAI 相容端點，設定要點：

- `base_url` = `http://localhost:11434/v1`（**要有 `/v1`**）
- model 用純 tag（例如 `qwen3.6:35b`，**不要** `ollama/` 前綴）
- `api_key_env` 留空即可（本地 keyless，不需金鑰）

設定檔位置：`~/.config/jenai/config.toml`。

---

## 文件導覽

| 文件 | 說明 |
|---|---|
| [docs/README.md](docs/README.md) | **完整文件庫索引**：依開始使用、ROS 2、開發、驗證、產品與歷史分類 |
| [docs/QUICKSTART.md](docs/QUICKSTART.md) | **零基礎上手手冊**：從安裝、設定到第一句對話；尚未以正式冷啟動研究保證完成時間 |
| [docs/TECHNICAL_GUIDE.md](docs/TECHNICAL_GUIDE.md) | **從零到有技術指南**：建置、架構、每個模組做什麼、擴充方式（開發新人先讀這份） |
| [docs/ONBOARDING.md](docs/ONBOARDING.md) | **機器人上線手把手**：裸 ROS2 → 建圖 → 定位 → Nav2 → 第一次 `/route`（`jenai doctor` 的 nav 檢查就是進度條） |
| [docs/COMMANDS.md](docs/COMMANDS.md) | CLI + slash 命令完整規格 |
| [docs/EVIDENCE_LEDGER.md](docs/EVIDENCE_LEDGER.md) | **單一證據表**：論文、README 與簡報共用的正式數字與限制 |
| [docs/archive/design/](docs/archive/design/README.md) | **設計歸檔**：v0.1 規劃文件，只供歷史追溯 |

---

## 技術棧

- **語言**：Python 3.12+（依賴由 [uv](https://docs.astral.sh/uv/) 管理；`uv.lock` 固定開發／CI 解析，tool 安裝遵循套件版本範圍）
- **TUI**：Textual；**WebUI**：stdlib `http.server`（零額外依賴）；**CLI**：Typer
- **LLM Provider**：`openai` SDK 打任何 OpenAI 相容端點（NVIDIA NIM 雲端／Ollama 地端）
- **Agent Framework**：openai-agents SDK（多-agent handoffs、本地 tracing）
- **ROS2**：Jazzy；Nav2（Smac Hybrid-A* + RPP，阿克曼）；rclpy 走獨立 bridge 子程序（系統 Python）
- **MCP**：官方 `mcp` SDK（FastMCP，stdio transport）

---

## 狀態（v2.1.0，2026-07）

> ✅ **安全鏈**：緊急停止（TUI `/stop`／WebUI STOP 鈕／MCP `stop`／daemon `halt`，免批准可搶佔、跨程序 cancel-all）、bridge watchdog（client 斷線自主停車）、執行期硬限速（`[vehicle]`）、HITL 編號審批卡、daemon 明確授權 gating、權限模式的自然語言路由例外網。
>
> ✅ **操作面**：串流聊天、`/plan`／`/run` 多-agent、忙碌時 FIFO 指令排隊（`/queue` 管理）、ROS2 工具全套、`/drive` 自然語言控車、`/route` 即時導航（Nav2／odom 直驅雙路徑，剩餘距離+Esc 真取消）、depth stop-and-go 局部繞障（資料逾時即停）、`/mission`／`/patrol`（循環巡邏+每點 VLM 拍照回報）／`/explore`（已知點位有界隨機巡遊）／`/dock`、`/loc add here|gps` 現場建點與 GPS 註冊、`/vision image|camera`、`/perception` 持續感知、`/report` 巡邏日報、`/model`／`/provider` 雲地即時切換。
>
> ✅ **介面**：Claude 風格 TUI（會動的吉祥物+權限三模式 Shift+Tab）、多頁 WebUI（Console／Camera／Status／API，token 認證+手機批准+即時地圖+STOP）、MCP server、daemon 常駐、`skills/*.toml` 檔案定義技能。全部走同一套共用原語（導航調度、急停、相機分析、地點載入各只有一份）。
>
> ✅ **Copilot 與決策腦**：`JenAI scaffold` 自然語言生成 ROS2 套件（`--build` 生成即驗證閉環）；`decision_core` 有界動作決策 + `JenAI eval` E1 評測；ROS Developer 依 live graph 完成 topic/schema 發現，並以單一 `ros_drive_verified` 能力原子化基準里程計、一次性受批准動作、自動停止與後驗回授，不持有任意 shell。
>
> ✅ **確定性快速路徑**：明確且純唯讀的自然語言狀態查詢（位置、LaserScan、Nav2 readiness）直接使用與 Agent 共用的受記錄 ROS2 工具，不等待本地 35B 模型；含導航、巡邏、建議或其他決策／致動語意的混合要求仍走完整 LLM、批准與執行邊界，不會因關鍵字分流遺失動作。
>
> ✅ **工程**：完整自動化測試套件（無 ROS 的 CI 可全跑）、Python 3.12／3.13／3.14 CI 矩陣與三道檢查（執行邊界覆蓋倒退檢查+架構鐵律+wheel 冒煙）、rclpy bridge 協定有純 stdlib fake、批准中斷可跨重啟恢復、誠實回報原則貫穿每條路徑。
>
> ✅ **執行邊界與數位分身驗證**（[TWIN_SETUP.md](docs/TWIN_SETUP.md)）：Agent 被限制在觀察、決策、能力、權限與執行驗證五類邊界內；導航可選擇先在數位分身預演，依 G1 碰撞／G2 超時／G3 禁區／G4 終點偏差／G5 Nav2 失敗輸出 pass／block／refer。數位分身是執行驗證的一種機制，不是 Agent 的全部。
>
> 🚧 **研究證據狀態**（見 [EVIDENCE_LEDGER](docs/EVIDENCE_LEDGER.md)）：
>
> - E2 是固定目標集的配對描述性再分析；A／B 為決定性政策推導，C 為舊 live observed。
> - E3 僅驗證 ROS_DOMAIN_ID=42 的隔離 mock fixture。
> - B4 是事後固定並可重建的 102 份 report subset，不支持精確 20 小時暴露量或零安全事件。
> - E1、E4 與 A6 另依 ledger 所列邊界解讀；跨載具物理泛化、實體 Sim-to-Real 與使用者效率比較仍是後續工作。

---

## License

JenAI is licensed under the [Apache License 2.0](LICENSE).
