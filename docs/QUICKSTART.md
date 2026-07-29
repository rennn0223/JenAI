# QUICKSTART — 零基礎上手手冊

> 這份寫給**完全沒碰過這個專案、甚至不太熟終端機**的人：照著做，
> 從一台空電腦走到「跟 JenAI 講第一句話」。實際完成時間會受下載、模型與 ROS 環境
> 影響；正式冷啟動研究完成前不承諾固定分鐘數。
> 看完想深入:開發者看 [TECHNICAL_GUIDE](TECHNICAL_GUIDE.md),
> 指令查 [COMMANDS](COMMANDS.md),接真機器人照 [ONBOARDING](ONBOARDING.md)。

## 0. JenAI 是什麼?我需要準備什麼?

JenAI 是一個**高階 Robot Decision Agent**。你用自然語言描述任務，LLM 從已註冊能力中
選擇合適的 Workflow；Workflow 再以確定性流程呼叫 ROS 2／Nav2、限制重試、保存證據、
檢查完成條件。LLM 不負責速度、轉向、局部避障，也不必在每個巡檢點重新思考。

沒有機器人也能用聊天、規劃與查詢；機器人後端不可用時會誠實回報，不會假裝成功。

| 你想做到 | 需要準備 |
|---|---|
| 先玩起來(聊天/規劃) | 一台 Linux 或 macOS 電腦 + 模型來源(下面二選一) |
| 模型來源 A:本地(免費、斷網可用) | [Ollama](https://ollama.com) + `ollama pull qwen3.6:35b` |
| 模型來源 B:雲端 | 一把 API 金鑰(NVIDIA build.nvidia.com 或 OpenAI)；該請求文字與 vision 圖片會送到 provider，敏感場域先讀 [SECURITY](../SECURITY.md) |
| 控真機器人 | 車上有 ROS 2(建圖導航照 [ONBOARDING](ONBOARDING.md),那是另一段旅程) |

## 1. 裝 uv(唯一必裝工具)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

裝完重開終端機,打 `uv --version` 有版本號就成功。
(uv 會自動幫你管 Python,**不用**自己裝 Python。)

## 2. 取得並安裝 JenAI

目前 repository 是 public；v2.5.1 Release 公開提供 wheel、matching constraints、CycloneDX SBOM、`SHA256SUMS`，以及 build provenance 與 SBOM 的 Sigstore bundles。只有資產實際出現在 Release 且 checksum／attestation 驗證通過，才視為已發布與可驗證。

選定正式版本後，命令會下載該版 wheel、matching constraints 和 checksum，先驗證兩個
安裝檔再交給 `uv`。只有 asset 清單確實含 wheel、同版 constraints、`SHA256SUMS` 的
release 才適用；例如歷史 `v1.1.4` 缺少後兩項，不代表供應鏈檢查已通過。
目前穩定版為 `v2.5.1`；若使用後續版本，也必須先確認資產完整。
這段流程以 Linux／Ubuntu 為目標；macOS 仍是 Experimental，須自行安裝 GNU coreutils
並將 `sha256sum` 換成 `gsha256sum`，且不因此取得相同驗證等級。

```bash
read -r -p "JenAI release version (without v): " VERSION
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "invalid version"; exit 2; }
INSTALL_DIR="$HOME/Downloads/jenai-$VERSION"
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"
BASE_URL="https://github.com/rennn0223/JenAI/releases/download/v${VERSION}"
curl --fail --location --remote-name \
  "$BASE_URL/jenai-${VERSION}-py3-none-any.whl"
curl --fail --location --remote-name \
  "$BASE_URL/jenai-${VERSION}-constraints.txt"
curl --fail --location --remote-name "$BASE_URL/SHA256SUMS"
grep -Fq " *jenai-${VERSION}-py3-none-any.whl" SHA256SUMS
grep -Fq " *jenai-${VERSION}-constraints.txt" SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS
uv tool install \
  --constraints "jenai-${VERSION}-constraints.txt" \
  "./jenai-${VERSION}-py3-none-any.whl"
uv tool update-shell
```

只有 repository 在該版發布時為 public、release 說明明確標示 attestation 已發布、
且 assets 實際包含 `.sigstore.json` bundles 時，checksum 通過後才可選配第二層驗證；
這一步須使用支援 `gh attestation` 的已登入 GitHub CLI：

```bash
gh attestation verify "jenai-${VERSION}-py3-none-any.whl" \
  --repo rennn0223/JenAI
gh attestation verify "jenai-${VERSION}-constraints.txt" \
  --repo rennn0223/JenAI
```

重開終端機後，`JenAI version` 應顯示所選版本。wheel 會把 `JenAI` 與 `jenai` 都安裝成
使用者層級命令；不需要每次進 repo、輸入 `uv run`、建立 shell script 或 symlink。

若 `~/.local/bin/jenai` 早已是 repo `scripts/jenai` 的 symlink，先用
`ls -l ~/.local/bin/jenai` 確認目標，再移除該 symlink，否則它會與 wheel 的小寫 entry point
衝突。兩種啟動方式只能選一種。

只有要修改程式碼、且有 repository 權限的人才走 source checkout。請先完成 `gh auth login`
與 Git credential 設定，固定到已審閱的 tag／完整 commit SHA；此路徑是 Supported，但沒有
release wheel 同等的隔離安裝驗證：

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
uv sync
uv run JenAI
```

## 3. 第一次啟動 → 設定精靈

```bash
JenAI
```

第一次會進**橘色的設定精靈**,只問三步:

1. **選供應商**:本地 Ollama 按 `1`,NVIDIA 雲端按 `2`(每個選項旁有提示)
2. **連線細節**:全部**直接按 Enter 用預設值**就能動;金鑰欄位填的是
   環境變數**名稱**(如 `NVIDIA_API_KEY`)——不小心貼了金鑰本體也沒關係,
   精靈會自動把它安全搬進 `.env`(權限 0600)
3. **地點檔**:按 Enter 用預設

之後想重來:`JenAI onboard`(會先備份舊設定,金鑰和地點都保留)。

## 4. 健檢

```bash
JenAI doctor
```

- 每項 `pass` 或 `warn` = 正常(沒接機器人時 ros/nav 是 warn,**這是設計**)
- 有 `fail` 才需要處理,每個 fail 下面直接附修法

## 5. 講第一句話

```bash
JenAI
```

進到 TUI(有一隻會動的臘腸狗就是對了)之後:

- **直接打字**就是對話:「你好,你會做什麼?」
- 打 **`/`** 會彈出指令選擇表:↑↓ 選、Tab 補完(格式會顯示成提示,照著填)
- **Shift+Tab**(或打 `/mode`)切換三種模式:審批(預設)/規劃(只想不做)/自動
- 隨時 **`/help`** 看全部指令、**`/quit`** 離開
- 記住一件事就好:**`/stop` 永遠有效**——任何時刻、不用批准、立刻停車

## 6. (選讀)接上 Isaac Sim／機器人後跑第一個完整 Workflow

車端 ROS 2 + 建圖照 [ONBOARDING](ONBOARDING.md) 完成後，先把地點綁到目前地圖：

```bash
JenAI site status
JenAI site map-identity
JenAI site init ~/.config/jenai/site-profile.toml \
  --site-id isaac-warehouse \
  --name "Isaac Sim Warehouse" \
  --scene "Isaac Sim 5.1.0 Warehouse"
# 審閱 home_location、dock_location、patrol_areas 與 required；草稿不會自行啟用
JenAI site activate ~/.config/jenai/site-profile.toml
JenAI site validate
```

若已有有效的 active Site Profile，只需跑 `site status` 與 `site validate`。啟用時會重新計算
locations 檔指紋；匯入檔自己寫的 `active`／`validated` 不會被信任。

若 `site status` 顯示舊版 Profile 缺少 locations identity，請保持已驗證地圖運作並執行
`JenAI site validate --repair`。這個命令只有在 live map 與原本記錄的 map identity 完全相符時
才會重新綁定目前 locations 檔；不會默默接受另一張地圖。

先在 TUI 測一個已知地點：

```
/loc list
請導航到 map_left_up，到達後核對位置與朝向誤差。
```

確認單點流程正常後，直接用自然語言啟動完整語意區域巡檢：

```
巡檢目前 Site Profile 中的所有必要區域，每個觀察點保存照片；
完成後回到 home，證據不足或有異常時要求人工確認。
```

Agent 只選一次 `area_patrol_workflow_tool`。正常的區域排序、Nav2 導航、照片保存、有限重試、
回到 home 與最終報告都由 Workflow 自己完成；未解的高階事件才回到 LLM。

- `/patrol A, B xN photo`：使用者指定順序的 waypoint 巡邏。
- `/explore ...`：在合格已知點位間做有界、低重複巡遊，不保證完整覆蓋。
- 語意區域巡檢：覆蓋 Site Profile 中所有 `required = true` 區域並依證據判定完成。

報告與照片存於 `~/.config/jenai/reports/`；`JenAI data export`、`prune`、`harden` 會用同一套
資料生命週期管理它們。

WebUI(手機也能用):`JenAI web`——啟動時會**把所有能開的網址直接印給你**
(本機/SSH/區網),照著開即可;紅色 STOP 鈕永遠在右上角。

## 7. 常見狀況對照表

| 你看到 | 意思與解法 |
|---|---|
| `unavailable` / `ROS2 not detected` | 誠實回報,不是壞掉——沒接機器人本來就這樣;聊天照常 |
| 模型沒回應 / provider error | 本地:`ollama serve` 有沒有在跑?雲端:`.env` 裡金鑰對嗎?`JenAI doctor` 會指出來 |
| WebUI 打不開 | 用啟動時印出的網址(含 token);SSH 遠端照輸出裡的 `ssh -L` 那行做 |
| WebUI 出現 401 | token 重啟會換——回終端機拿新網址,或啟動時 `--token 自訂值` 固定 |
| 想整個重新設定 | `JenAI onboard`(自動備份,不會弄丟金鑰/地點) |
| 打 `JenAI` 說找不到指令 | 先跑 `uv tool update-shell` 並重開終端機；仍失敗就用 `uv tool dir --bin` 確認該目錄已在 `PATH` |
| 金鑰改了 `.env` 卻沒生效 | shell 已 `export` 的同名變數會**蓋過** `.env`——`unset` 它或改 export 的那份 |
| 打錯字進了奇怪狀態 | Esc 中斷目前動作;`/clear` 清畫面;都救不了就 `/quit` 重進 |

## 8. 更新、回滾與解除安裝

更新與回滾是同一個安全流程：從目標 release 重新下載 wheel、同版 constraints 與
`SHA256SUMS`，照第 2 節驗證，最後把安裝命令改成：

```bash
uv tool install --force \
  --constraints "jenai-${VERSION}-constraints.txt" \
  "./jenai-${VERSION}-py3-none-any.whl"
JenAI version
JenAI doctor
```

不要混用不同 release 的檔案，也不要把移動中的 `main` 當回滾來源。完整驗收流程見
[ROLLBACK](operations/ROLLBACK.md)。從本機 wheel 安裝時，`uv tool upgrade jenai` 不會自動選取下一個
GitHub release，因此仍應使用上述已驗證的 `--force` 流程。

要解除安裝，先查看與匯出本機資料，再移除 Python package：

```bash
JenAI data status
JenAI data export "jenai-data-$(date +%F).tar.gz"
JenAI data purge --dry-run
uv tool uninstall jenai
```

`uv tool uninstall` 只移除 `JenAI`／`jenai` 程式與 tool environment，**不會刪除**
`~/.config/jenai`。匯出不含 `.env`／`config.toml`，預設 purge 亦保留它們與 locations；
若要在確認路徑後清除資料，依 [DATA_LIFECYCLE](operations/DATA_LIFECYCLE.md) 的明確選項操作。

## 下一步

- 全部指令規格:[COMMANDS](COMMANDS.md)
- 想懂它怎麼運作、怎麼改程式:[TECHNICAL_GUIDE](TECHNICAL_GUIDE.md) → [CODE_TOUR](CODE_TOUR.md)
- 想接真車跑導航:[ONBOARDING](ONBOARDING.md)(doctor 的 nav 區段就是你的進度條)
- 想備份、保留或清除本機資料:[DATA_LIFECYCLE](operations/DATA_LIFECYCLE.md)
