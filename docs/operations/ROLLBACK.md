# ROLLBACK — 安裝、升級、回滾與解除安裝手冊

## 變更版本前

1. 停止進行中的任務，確認載具靜止；實體部署使用硬體 emergency stop。
2. 關閉 TUI、WebUI、daemon 與背景 benchmark。
3. 先執行 `JenAI data status`，再依 [DATA_LIFECYCLE](DATA_LIFECYCLE.md) 匯出需要保留的
   sessions／pending runs／locations／reports／traces／audit。匯出不包含 `.env`、
   `config.toml` 或 `config.toml.bak-*`；不要把機密或場域資料上傳到 issue。
4. 記錄目前的 `JenAI version` 與 `JenAI doctor` 輸出。

## Release wheel：安裝、升級與回滾的共同流程

目前 repository 是 public；v2.2.0 Release 公開提供 wheel、matching constraints、CycloneDX SBOM、`SHA256SUMS`，以及 build provenance 與 SBOM 的 Sigstore bundles。只有資產實際出現在 Release 且 checksum／attestation 驗證通過，才視為已發布與可驗證。

wheel、constraints 與
`SHA256SUMS` 必須來自**同一個 release**，且 asset 清單必須實際包含它們；例如既有
`v1.1.4` 缺少後兩項，不可宣稱已通過這套驗證。流程以 Linux／Ubuntu 為目標；macOS
仍是 Experimental，須自行安裝 GNU coreutils、改用 `gsha256sum`，且不因此取得相同
驗證等級。提示輸入 tag 去掉 `v` 的版本號：

```bash
read -r -p "Target JenAI version (without v): " VERSION
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
uv tool install --force \
  --constraints "jenai-${VERSION}-constraints.txt" \
  "./jenai-${VERSION}-py3-none-any.whl"
uv tool update-shell
JenAI version
JenAI doctor
```

只有 repository 在該版發布時為 public、release 說明明確標示已發布 GitHub
attestation，且 assets 實際包含 `.sigstore.json` bundles 時，才可在 checksum 通過後
選配執行；private 或舊 release 沒有 attestation 時不能執行或聲稱這一層已驗證：

```bash
gh attestation verify "jenai-${VERSION}-py3-none-any.whl" \
  --repo rennn0223/JenAI
gh attestation verify "jenai-${VERSION}-constraints.txt" \
  --repo rennn0223/JenAI
```

`uv tool install --force` 只替換 tool environment，不會刪除 `~/.config/jenai`。若目前是
從本機 wheel 安裝，`uv tool upgrade jenai` 不會替你選取下一個 GitHub release；應重跑
上述流程，留下目標版本與 checksum 驗證紀錄。

若 `~/.local/bin/jenai` 是 source checkout 的 `scripts/jenai` symlink，先以 `ls -l` 確認
後移除該 symlink，再安裝 wheel。不要用 `ln -sf` 覆蓋 uv 管理的 entry point。wheel 安裝
本身同時提供 `JenAI` 和 `jenai`，不需要 symlink。

## 已審閱原始碼版本

原始碼安裝只支援精確 tag 或完整 commit SHA，不使用移動中的 branch：

```bash
read -r -p "Reviewed tag (vX.Y.Z) or full commit SHA: " REF
if [[ ! "$REF" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ \
   && ! "$REF" =~ ^[0-9a-fA-F]{40}$ ]]; then
  echo "REF must be vX.Y.Z or a full 40-character commit SHA"
  exit 2
fi
git clone https://github.com/rennn0223/JenAI.git ~/JenAI-rollback
cd ~/JenAI-rollback
SOURCE_SHA="$(git rev-parse --verify "${REF}^{commit}")"
git switch --detach "$SOURCE_SHA"
test "$(git rev-parse HEAD)" = "$SOURCE_SHA"
printf "Source commit: %s\n" "$SOURCE_SHA"
uv tool install --force .
JenAI version
```

依 [SUPPORT_MATRIX](SUPPORT_MATRIX.md)，pinned source install 是 Supported，但尚未等同 release
wheel 的隔離生命週期驗證，且依賴解析可能隨時間變動。正式交付仍優先 wheel + constraints。

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

## 解除安裝與資料保留

安全的預設流程是先匯出、只預覽 purge，再解除安裝 package：

```bash
JenAI data status
JenAI data export "jenai-data-$(date +%F).tar.gz"
JenAI data purge --dry-run
uv tool uninstall jenai
```

解除安裝會移除大小寫兩個 command 與隔離 tool environment，但刻意保留
`~/.config/jenai`。若要刪除預設生成資料，先重裝／尚未解除安裝時執行：

```bash
JenAI data purge --dry-run
JenAI data purge --yes
```

預設 purge 清除 sessions、pending runs、reports、traces 與 audit（含 SQLite sidecars）；
locations、`config.toml`、`config.toml.bak-*`、`.env` 都保留。只有在讀過
[DATA_LIFECYCLE](DATA_LIFECYCLE.md)、確認列出的絕對路徑與備份後，才使用各自的
`--include-locations`、`--include-config`、`--include-config-backups`、
`--include-credentials` 選項。這是邏輯刪除，不是 SSD 或備份的鑑識級清除。

## 版本切換後驗收

- 無 ROS：版本、help、onboard/doctor、provider 連線與 TUI 啟動。
- Isaac Sim：依 `docs/validation/TEST.md` 跑一個唯讀檢查、一個批准流程、一個 `/route`、取消與
  `/stop`。
- 實體載具：先在架空／低速／隔離場地做 smoke test，硬體 stop 操作者在場。

若舊版無法讀取新版設定，使用 setup wizard 產生的備份，或在保留 `.env` 與 locations
的前提下重新執行 `JenAI onboard`。不要手動修改 audit SQLite schema。
