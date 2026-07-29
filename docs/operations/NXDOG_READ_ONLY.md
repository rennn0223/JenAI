# NXDog 唯讀整合

> 狀態：Experimental／observation-only
>
> Vendor reference:
> `nexuni/nxdog-developer-kit@9cc558172993b6ed9ee239f2e4e8f5e971740d24`

本整合讓 `JenAI doctor` 讀取 NXDog 公開 developer kit 範例後端的健康、地圖、
odom、速度與充電狀態。它不註冊任何移動 Capability，也不呼叫導航、停止、速度、
姿態、地圖切換或充電控制。

## 架構位置

```text
JenAI doctor
    │
    ▼
NXDogObserver
    │ bounded GET-only HTTP
    ▼
NXDog example backend :5088
    │
    ▼
nxnav／platform ROS 2 observations
```

`NXDogObserver` 是一個深 module：caller 只需取得一份 typed snapshot；六個 vendor
endpoint、輸入驗證、timeout、partial failure 與 evidence limitations 都封裝在 module
內。正式 HTTP adapter 與測試 adapter 使用同一個 interface。

## 前置條件

1. 依 Nexuni developer kit 在 Jetson Orin NX 的 ROS 2 Foxy 環境建立並啟動
   `examples/backend/http-api-server`。
2. 變更 developer kit 文件中的預設 SSH 密碼，並改用 SSH key。
3. 將 TCP `5088` 限制在隔離的機器人網路，只允許 JenAI host 連入。
4. 不得將 `5088` 暴露至公網、port forwarding、共享 Wi-Fi 或未受信任 VPN peers。
5. 確認本次使用的 developer kit、robot firmware 與 nxnav interface 版本。

公開範例使用 plain HTTP、全域 CORS、`0.0.0.0` bind，且沒有 authentication。JenAI
可以誠實觀察它，但不會把該 transport 宣稱為 production-safe。

JenAI 明確停用 ambient HTTP／HTTPS proxy，避免機器人流量被 shell 或系統代理攔截。
所有重新導向（包含同 host、外部 host 與相對路徑）都會 fail closed，不會跟隨至
`/stop` 或其他非 allowlist endpoint。

## 設定

不要把 NXDog IP commit 到 repository。在 JenAI host 的 shell 或本機 `.env` 設定：

```bash
export JENAI_NXDOG_API_URL="http://192.168.123.18:5088"
```

URL 必須：

- 使用 `http` 或 `https`。
- 指向 server root，不含額外 path、query 或 fragment。
- 不得將帳號或密碼嵌入 URL。

未設定 `JENAI_NXDOG_API_URL` 時，NXDog adapter 完全停用，既有 doctor 與 ROS/Nav2
流程不受影響。

## 執行

```bash
JenAI doctor --json
```

完整 doctor 才會存取 NXDog；TUI startup 使用的快速 doctor 不會執行外部網路探測。

目前只會送出以下 GET：

```text
/nav_health
/get_ready_flag
/current_map
/odom
/velocity
/is_charging
```

六個 endpoint 會並行收集，但不是同一時刻的原子快照。Observation 會記錄 JenAI 的
collection start、completion 與 duration；vendor payload 本身沒有 source timestamp，
因此不可將跨 endpoint 欄位視為同一個 robot state。`all_endpoints_valid` 表示六項都
通過驗證；`complete` 只是相容別名，不代表狀態具原子性或 freshness。

每個 endpoint 獨立驗證。單一 endpoint 失敗時，其他有效證據仍會保留，並以穩定分類
回報：`transport`、`http_status`、`redirect_rejected`、`invalid_payload` 或
`internal_adapter_error`。Doctor 保留分類，不會把壞 JSON 或程式錯誤誤報為網路故障。

若 `/current_map` 與 `/odom.map` 的 map group name 不同，Doctor 會警告兩份無 timestamp
觀察不一致；它不會自行選一份作為正式 map identity。

## 證據語意

| 觀察 | 可以支持 | 不能支持 |
|---|---|---|
| `nav_health.alive` | nxnav heartbeat 最近可見 | localization、action server 或導航 ready |
| `ready_flag` | 範例 client 已建立必要物件 | Nav2／nxnav 已接受 goal |
| `current_map` | vendor map group name | cryptographic map identity |
| `odom` | vendor 回傳的 map pose | freshness、covariance、實際世界 ground truth |
| `velocity` | vendor 回傳的速度值 | 已確認停止；response 沒有 source timestamp |
| `is_charging` | backend 目前以正電池電流判定 charging | 充電接點、功率、持續充電或電池增加 |

分享 Doctor JSON、log 或 artifact 前必須先遮蔽：

- 私有 map group name 與 map tile。
- pose／velocity 座標。
- NXDog host、port 與完整 base URL。

因此本階段不得宣稱：

- NXDog 已支援 JenAI navigation／patrol。
- `/stop` 已確認車體停止。
- 已完成自動充電。
- map name 與 Site Profile map digest 等價。
- HTTP 回傳成功代表實體動作成功。

## 明確禁止的 endpoint

本 adapter 具有固定 GET allowlist，無法用來呼叫：

```text
/navigate
/stop
/pause
/resume
/set_cmd_vel
/set_initialpose
/map
/charging
/auto_charging_stop
/set_sport_action
```

這些動作需等待 hardened companion adapter、全域 command ownership、取消確認、
fresh velocity evidence 與實體 acceptance。

## 下一階段 gate

進入實體 motion 前，至少需要：

1. Vendor API／firmware version contract。
2. `/stop` cancel acknowledgement 與底盤停止語意。
3. `/set_cmd_vel` 硬限速、更新頻率與 watchdog。
4. timestamped odom／velocity 及 frame contract。
5. authenticated transport 與 single active command lease。
6. map bundle 的穩定 content identity。
7. 一條不依賴 LLM 的 physical stop acceptance runner。

完成上述契約前，NXDog 在
[`SUPPORT_MATRIX`](SUPPORT_MATRIX.md) 中只列為唯讀 experimental adapter。
