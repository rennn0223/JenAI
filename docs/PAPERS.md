# PAPERS — 深讀清單(Agent × Robotics × Digital Twin)

> 每篇都是**真實存在、已驗證**的論文(2026-07 經網路查證 arXiv 編號/發表處)。
> 順序 = 建議閱讀順序;每篇附:一段摘要、跟你論文哪一章對話、讀時帶著什麼問題。

---

## 第一批:LLM 當機器人大腦的奠基三篇(文獻回顧 2.1 的骨幹)

### 1. SayCan — *Do As I Can, Not As I Say: Grounding Language in Robotic Affordances*
Ahn et al., CoRL 2022 · arXiv:2204.01691
**摘要**:LLM 有豐富語意知識但不知道機器人「當下做得到什麼」。SayCan 把 LLM 對每個候選技能的語意評分,乘上該技能的價值函數(affordance,由 RL 學出的成功機率),選乘積最高的技能執行。在真實廚房機器人上完成長程指令。
**跟你的對話**:你的「有界動作集」是 SayCan 分工的直系後代 —— LLM 不碰執行層,只在既有技能中選。差異:你用**孿生實際執行**取代 affordance 函數的評分近似。這是論文 2.1 的定位句。
**帶著讀**:affordance 評分 vs 孿生預演,各攔得住哪類錯誤?(答案會變成你 2.5 小結的論證。)

### 2. Code as Policies — *Language Model Programs for Embodied Control*
Liang et al., ICRA 2023 · arXiv:2209.07753
**摘要**:讓 LLM 直接生成可執行的 Python 策略碼(呼叫感知 API 與控制原語),以程式語言的組合性換取泛化;層級式生成(高階函數遞迴展開)。
**跟你的對話**:你的 `scaffold` 是它的工程化表親;差異是你把「生成的碼」放進 **人審 + colcon 驗證**閉環,而非直接執行 —— 這是安全立場的分歧點,值得在論文中明寫。
**帶著讀**:生成碼直接執行的風險他們怎麼處理?(幾乎沒處理 —— 這是你的切入點。)

### 3. Inner Monologue — *Embodied Reasoning through Planning with Language Models*
Huang et al., CoRL 2022 · arXiv:2207.05608
**摘要**:把環境回饋(成功偵測、場景描述、人類糾正)以文字持續餵回 LLM,形成閉環規劃;證明「聽得到結果」的 LLM 規劃器顯著強於開環。
**跟你的對話**:這是你 M6 決策迴圈「執行結果回饋至下一輪情境」那條箭頭的文獻根據;你的 SceneAnalysis/情境快照就是它的結構化版。
**帶著讀**:他們的回饋是自由文字,你的是結構化快照 —— 對小模型(qwen3:8b)哪種更穩?(直接影響你的 E1 設計。)

## 第二批:與你系統最像的近親(定位與差異化,2.1/2.2 後半)

### 4. ROSA — *Enabling Novel Mission Operations and Interactions with ROSA: The Robot Operating System Agent*
NASA JPL, arXiv:2410.06472 · [github.com/nasa-jpl/rosa](https://github.com/nasa-jpl/rosa)
**摘要**:JPL 開源的 ROS1/ROS2 自然語言 agent(LangChain + ReAct),把 ROS 操作抽象成工具函數,含參數驗證與約束執行等安全機制,用於檢查、診斷、操作機器人。
**跟你的對話**:**這是你最需要引用的「近親」**——審查委員一定會問「跟 ROSA 差在哪」。你的答案:ROSA 止於工具層安全(參數驗證);JenAI 加上執行前的**物理驗證**(Twin Gate)、分層安全鏈(watchdog/急停/夾限)、與誠實回報語意。讀它是為了精準寫出這段差異。
**帶著讀**:它的 safety mechanisms 清單,逐條對照你的安全鏈 —— 做成論文裡的對照表。

### 5. LM-Nav — *Robotic Navigation with Large Pre-Trained Models of Language, Vision, and Action*
Shah et al., CoRL 2022 · arXiv:2207.04429
**摘要**:零訓練組合三個預訓練模型:LLM 把指令拆成地標序列、VLM 把地標接地到拓撲圖節點、視覺導航策略執行 —— 真實 UGV 上完成公里級語言導航。
**跟你的對話**:同是 UGV + 語言導航,但它是拓撲圖航點,你是 metric 地圖 + Nav2 + 預演。你的 `/route 從A到B` 地標解析與它的 landmark extraction 異曲同工。
**帶著讀**:他們怎麼量測「指令→地標」的解析正確率?(方法可以直接搬進你的 E1。)

### 6. VLFM — *Vision-Language Frontier Maps for Zero-Shot Semantic Navigation*
Yokoyama et al., ICRA 2024 · arXiv:2312.03275
**摘要**:frontier 探索 + VLM 相似度評分:把「往哪個 frontier 走最可能找到目標物」交給視覺語言模型打分,零訓練在真實四足(Spot)上做語意目標導航。
**跟你的對話**:你的 PerceptionLoop affordance 走同一哲學(VLM 打分,不出控制);它證明這條路在實機可行,是你「VLM 判斷、幾何反射」分層的佐證。

## 第三批:孿生驗證 —— 你的核心貢獻所在的空隙(2.2 的關鍵引用)

### 7. *Digital Twin Enabled Runtime Verification for Autonomous Mobile Robots under Uncertainty*
arXiv:2412.09913
**摘要**:為自主移動機器人架構數位孿生,做**執行期**的可解釋監控與重規劃 —— 孿生不只離線模擬,而是上線跟著跑、驗證安全性質。
**跟你的對話**:**這是離你 Twin-Gated Execution 最近的工作**,也是「twin-in-the-loop 漸增但未與 LLM 代理結合」這句話(你論文 2.2 的定位)的直接證據。它做的是執行期監控;你做的是**執行前逐決策預演** —— 時間點不同、目的不同,這個差異就是你的新穎性所在,務必讀熟。
**帶著讀**:它的 runtime verification 性質怎麼定義?跟你的 G1–G5 判準有何映射?

### 8. *LLM-Based Adaptive Control Code Generation Framework with Digital Twin-Integrated Verification for Heterogeneous Robot Systems*
Applied Sciences (MDPI), doi:10.3390/app16083883
**摘要**:LLM 生成異質機器人控制碼,執行前經數位孿生做動力學驗證(pre-execution dynamics validation)與運動縮放。
**跟你的對話**:證明「LLM 輸出先過孿生」這個想法正在成形 —— 但它驗的是生成的控制碼,你驗的是**導航決策**;引它可以說明趨勢,同時劃清你的範圍。

## 第四批:對照組與評測(第五章討論 + E1 設計)

### 9. RT-2 — *Vision-Language-Action Models Transfer Web Knowledge to Robotic Control*
Brohan et al., CoRL 2023 · arXiv:2307.15818
**摘要**:把 VLM 直接微調成輸出動作 token 的端到端 VLA;網路知識遷移到操作任務。
**跟你的對話**:你的**反命題**。讀它是為了在論文裡誠實寫出 VLA 路線的優勢(泛化、靈巧)與代價(資料、不可驗證、不可攔截),然後論證為什麼無人載具的安全場景選你這條「離散決策 + 可預演」的路。

### 10. SafeAgentBench — *A Benchmark for Safe Task Planning of Embodied LLM Agents*
arXiv:2412.13178
**摘要**:評測具身 LLM agent 能否拒絕/安全地處理危險任務(300+ 含危險意圖的任務集,含拒絕率與安全違規指標)。
**跟你的對話**:你的 E1「該拒就拒、該問就問」評分維度可以直接借它的框架;引用它讓你的 eval 方法有據可依。

---

## 讀法建議(兩週節奏)

- 第 1–3 天:1→3(奠基,建立詞彙)
- 第 4–6 天:4、7(**最重要的兩篇** —— 近親與最近的孿生工作,寫定位段落時就看著它們寫)
- 第 7–10 天:5、6、9(導航與對照組)
- 第 11–14 天:8、10 略讀 + 回頭把 THESIS_DRAFT 第二章的【請確認】逐一落實

*所有 arXiv 編號經 2026-07 網路查證;引用格式進論文前請再以 arXiv/DBLP 頁面核對作者全名與正式發表處。*
