from jenai.bridge._safety_order import halt_in_order


def test_halt_commands_zero_before_and_after_navigation_cancel() -> None:
    events: list[str] = []

    def cancel_navigation() -> bool:
        events.append("cancel")
        return True

    canceled = halt_in_order(
        lambda count: events.extend(["zero"] * count),
        cancel_navigation,
        pulses=3,
    )

    assert canceled is True
    assert events == ["zero", "zero", "cancel", "zero", "zero", "zero"]
