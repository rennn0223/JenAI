"""Deterministic natural-language reporting for the Robot Capability Card."""

from __future__ import annotations

from jenai.capabilities import (
    CapabilityMaturity,
    RobotCapabilityCard,
    build_robot_capability_card,
)
from jenai.capability_localization import limitation_zh
from jenai.config.models import AppConfig

_CAPABILITY_REQUESTS = (
    "who are you",
    "introduce yourself",
    "what can you do",
    "what can this robot do",
    "capability",
    "capabilities",
    "limitation",
    "limitations",
    "你是誰",
    "介紹你自己",
    "自我介紹",
    "你能做什麼",
    "可以做什麼",
    "有哪些能力",
    "能力",
    "成熟度",
    "限制",
)

_MATURITY_ZH = {
    CapabilityMaturity.IMPLEMENTED_VALIDATED: "已實作且完成產品驗證",
    CapabilityMaturity.IMPLEMENTED_UNVALIDATED: "已實作，尚未完成產品驗證",
    CapabilityMaturity.INTERFACE_ONLY: "僅有介面，尚未完成實作",
    CapabilityMaturity.CONCEPT: "概念規劃",
}
_MATURITY_EN = {
    CapabilityMaturity.IMPLEMENTED_VALIDATED: "implemented and product-validated",
    CapabilityMaturity.IMPLEMENTED_UNVALIDATED: "implemented; product validation pending",
    CapabilityMaturity.INTERFACE_ONLY: "interface only; implementation pending",
    CapabilityMaturity.CONCEPT: "concept",
}


def is_capability_card_request(text: str) -> bool:
    """Return whether plain language asks for configured identity or capabilities."""
    lowered = text.strip().lower()
    return bool(lowered) and any(term in lowered for term in _CAPABILITY_REQUESTS)


def _prefers_chinese(text: str) -> bool:
    return any("\u3400" <= char <= "\u9fff" for char in text)


def _capability_lines_zh(card: RobotCapabilityCard) -> list[str]:
    lines: list[str] = []
    for item in card.capabilities:
        approval = "執行前需核准" if item.requires_approval else "不需移動核准"
        evidence = "、".join(item.completion_evidence) or "未登錄"
        lines.append(
            f"- {item.capability_id}｜{_MATURITY_ZH[item.maturity]}｜{approval}\n"
            f"  {item.summary_zh} "
            f"完成時必須具備：{evidence}；結果語意：{item.success_outcome}。"
        )
        lines.extend(f"  限制：{limitation}" for limitation in item.limitations_zh)
    return lines


def _capability_lines_en(card: RobotCapabilityCard) -> list[str]:
    lines: list[str] = []
    for item in card.capabilities:
        approval = "approval required" if item.requires_approval else "no motion approval"
        evidence = ", ".join(item.completion_evidence) or "none registered"
        lines.append(
            f"- {item.capability_id} | {_MATURITY_EN[item.maturity]} | {approval}\n"
            f"  {item.summary} Required completion evidence: {evidence}; "
            f"outcome: {item.success_outcome}."
        )
        lines.extend(f"  Limitation: {limitation}" for limitation in item.limitations)
    return lines


def capability_card_report(config: AppConfig, *, language_hint: str = "") -> str:
    """Render configured claims without inventing live availability or success."""
    card = build_robot_capability_card(config)
    if _prefers_chinese(language_hint):
        capabilities = "\n".join(_capability_lines_zh(card))
        limitations = (
            "\n".join(f"- {limitation_zh(item)}" for item in card.limitations) or "- 未登錄"
        )
        return (
            f"我是 JenAI，設定中的機器人是 {card.display_name}（{card.robot_id}）。\n"
            f"平台：{card.platform_type}；部署模式：{card.deployment_mode}。\n"
            f"{card.description}\n\n"
            "已登錄能力與成熟度：\n"
            f"{capabilities}\n\n"
            "整體已知限制：\n"
            f"{limitations}\n\n"
            "誠實邊界：以上是設定與產品證據的能力宣告，不代表本次即時可用，"
            "也不代表任務已成功；目前狀態需另做只讀量測，動作結果需依完成證據判定。"
        )

    capabilities = "\n".join(_capability_lines_en(card))
    limitations = "\n".join(f"- {item}" for item in card.limitations) or "- None registered."
    return (
        f"I am JenAI. The configured robot is {card.display_name} ({card.robot_id}).\n"
        f"Platform: {card.platform_type}; deployment mode: {card.deployment_mode}.\n"
        f"{card.description}\n\n"
        "Registered capabilities and maturity:\n"
        f"{capabilities}\n\n"
        "Known limitations:\n"
        f"{limitations}\n\n"
        "Evidence boundary: these are configured, product-evidence claims—not proof that a "
        "capability is live now or that a task succeeded. Live state and task completion "
        "require their own measurements."
    )
