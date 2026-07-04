# twin — Twin Gate:數位孿生執行閘(論文核心)

啟用 `[twin]` 後,每個導航目標先在 Isaac Sim 孿生場景(隔離的
`ROS_DOMAIN_ID`)完整預演,判定後才准碰實體:

- **G1 碰撞 / G3 禁區** → `block`(硬安全違規)
- **G2 逾時 / G4 終點偏差 / G5 Nav2 失敗**、孿生連不上 → `refer`(交人裁決;
  daemon 自主路徑無人可問 → 一律視為 block)
- **閘門啟用時絕不靜默放行**;預設關閉 = 零行為變化

`gate.py`:`TwinGate` 預演管線 + 一次性 `rehearse_goal`。掛載點在
`tools/nav_live.py` 的 `navigate_with_fallback`(所有導航入口共用)。
判準全用 ROS topics 計算,不碰 Isaac 內部 API。場景建置見
`docs/TWIN_SETUP.md`;它攔的是「執行層錯誤」,與 HITL(意圖層)正交 ——
論證見 `docs/SAFETY_CASE.md`。
