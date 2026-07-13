# QUICKSTART — 零基礎上手手冊

> 這份寫給**完全沒碰過這個專案、甚至不太熟終端機**的人:照著做,
> 20 分鐘內從一台空電腦走到「跟 JenAI 講第一句話」。
> 看完想深入:開發者看 [TECHNICAL_GUIDE](TECHNICAL_GUIDE.md),
> 指令查 [COMMANDS](COMMANDS.md),接真機器人照 [ONBOARDING](ONBOARDING.md)。

## 0. JenAI 是什麼?我需要準備什麼?

JenAI 是一個**用聊天操作機器人的終端程式**——你打「帶我去機械系館」,
它負責解析、請你批准、然後指揮機器人。沒有機器人也能用:聊天、規劃、
查詢功能照常,機器人相關指令會誠實告訴你「後端不可用」。

| 你想做到 | 需要準備 |
|---|---|
| 先玩起來(聊天/規劃) | 一台 Linux 或 macOS 電腦 + 模型來源(下面二選一) |
| 模型來源 A:本地(免費、斷網可用) | [Ollama](https://ollama.com) + `ollama pull qwen3.6:35b` |
| 模型來源 B:雲端 | 一把 API 金鑰(NVIDIA build.nvidia.com 或 OpenAI) |
| 控真機器人 | 車上有 ROS 2(建圖導航照 [ONBOARDING](ONBOARDING.md),那是另一段旅程) |

## 1. 裝 uv(唯一必裝工具)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

裝完重開終端機,打 `uv --version` 有版本號就成功。
(uv 會自動幫你管 Python,**不用**自己裝 Python。)

## 2. 取得 JenAI

```bash
git clone https://github.com/rennn0223/JenAI ~/JenAI && cd ~/JenAI
uv sync
```

`uv sync` 跑完沒有紅字就緒。

## 3. 第一次啟動 → 設定精靈

```bash
uv run JenAI
```

第一次會進**橘色的設定精靈**,只問三步:

1. **選供應商**:本地 Ollama 按 `1`,NVIDIA 雲端按 `2`(每個選項旁有提示)
2. **連線細節**:全部**直接按 Enter 用預設值**就能動;金鑰欄位填的是
   環境變數**名稱**(如 `NVIDIA_API_KEY`)——不小心貼了金鑰本體也沒關係,
   精靈會自動把它安全搬進 `.env`(權限 0600)
3. **地點檔**:按 Enter 用預設

之後想重來:`uv run JenAI onboard`(會先備份舊設定,金鑰和地點都保留)。

## 4. 健檢

```bash
uv run JenAI doctor
```

- 每項 `pass` 或 `warn` = 正常(沒接機器人時 ros/nav 是 warn,**這是設計**)
- 有 `fail` 才需要處理,每個 fail 下面直接附修法

## 5. 講第一句話

```bash
uv run JenAI
```

進到 TUI(有一隻會動的臘腸狗就是對了)之後:

- **直接打字**就是對話:「你好,你會做什麼?」
- 打 **`/`** 會彈出指令選擇表:↑↓ 選、Tab 補完(格式會顯示成提示,照著填)
- **Shift+Tab**(或打 `/mode`)切換三種模式:審批(預設)/規劃(只想不做)/自動
- 隨時 **`/help`** 看全部指令、**`/quit`** 離開
- 記住一件事就好:**`/stop` 永遠有效**——任何時刻、不用批准、立刻停車

## 6. (選讀)接上機器人後的第一次導航

車端 ROS 2 + 建圖照 [ONBOARDING](ONBOARDING.md) 完成後:

```
/loc add here 充電站        ← 在現場幫位置取名字
/route 從 充電站 到 門口     ← 批准卡按 1(是)/2(是,別再問)/3(否),看著它走
```

WebUI(手機也能用):`uv run JenAI web`——啟動時會**把所有能開的網址
直接印給你**(本機/SSH/區網),照著開即可;紅色 STOP 鈕永遠在右上角。

## 7. 常見狀況對照表

| 你看到 | 意思與解法 |
|---|---|
| `unavailable` / `ROS2 not detected` | 誠實回報,不是壞掉——沒接機器人本來就這樣;聊天照常 |
| 模型沒回應 / provider error | 本地:`ollama serve` 有沒有在跑?雲端:`.env` 裡金鑰對嗎?`JenAI doctor` 會指出來 |
| WebUI 打不開 | 用啟動時印出的網址(含 token);SSH 遠端照輸出裡的 `ssh -L` 那行做 |
| WebUI 出現 401 | token 重啟會換——回終端機拿新網址,或啟動時 `--token 自訂值` 固定 |
| 想整個重新設定 | `uv run JenAI onboard`(自動備份,不會弄丟金鑰/地點) |
| 打 `jenai` 說找不到指令 | 那是選配的啟動器:`ln -sf ~/JenAI/scripts/jenai ~/.local/bin/jenai` 裝一次;或一律用 `uv run JenAI` |
| 金鑰改了 `.env` 卻沒生效 | shell 已 `export` 的同名變數會**蓋過** `.env`——`unset` 它或改 export 的那份 |
| 打錯字進了奇怪狀態 | Esc 中斷目前動作;`/clear` 清畫面;都救不了就 `/quit` 重進 |

## 下一步

- 全部指令規格:[COMMANDS](COMMANDS.md)
- 想懂它怎麼運作、怎麼改程式:[TECHNICAL_GUIDE](TECHNICAL_GUIDE.md) → [CODE_TOUR](CODE_TOUR.md)
- 想接真車跑導航:[ONBOARDING](ONBOARDING.md)(doctor 的 nav 區段就是你的進度條)
