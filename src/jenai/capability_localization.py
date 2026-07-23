"""Small built-in Traditional Chinese vocabulary for capability reports."""

from __future__ import annotations

_LIMITATION_ZH = {
    "Charging engagement and charging state are not verified.": (
        "尚未驗證實際接合充電座與充電狀態。"
    ),
    "This is known-location exploration, not frontier SLAM.": (
        "這是已知地點探索，不是 frontier SLAM。"
    ),
    "Charging feedback is unavailable in Isaac Sim.": "Isaac Sim 無法提供充電狀態回授。",
    "Docking currently verifies pose only; charging feedback is unavailable in Isaac Sim.": (
        "Dock 目前只驗證終點姿態；Isaac Sim 無法提供充電狀態回授。"
    ),
    "Dock is an approach-only capability; charging feedback is unavailable.": (
        "Dock 目前僅驗證接近姿態，無法驗證充電回授。"
    ),
}


def limitation_zh(text: str) -> str:
    return _LIMITATION_ZH.get(text, text)
