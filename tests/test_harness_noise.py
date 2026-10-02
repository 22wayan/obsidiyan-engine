from __future__ import annotations

from obsidiyan.sources.harness_noise import (
    is_machine_authored,
    is_pasted_dialog,
    strip_noise,
)


def test_task_notification_is_removed() -> None:
    text = "echte Frage\n<task-notification><status>done</status></task-notification>"
    assert "task-notification" not in strip_noise(text)
    assert "echte Frage" in strip_noise(text)


def test_command_wrapper_removed_but_name_kept() -> None:
    text = (
        "<command-name>/model</command-name>"
        "<command-message>model</command-message>"
        "<command-args>claude-fable-5</command-args>"
    )
    out = strip_noise(text)
    assert out == "/model"


def test_compaction_summary_is_machine_authored() -> None:
    """Der wichtige Fall: sonst wird eine Modell-Zusammenfassung dem Nutzer zugeschrieben."""
    assert is_machine_authored(
        "This session is being continued from a previous conversation that ran out of context."
    )


def test_real_user_text_is_not_machine_authored() -> None:
    assert not is_machine_authored("mach weiter wo wegen rate limit aufgehoert wurde")
    assert not is_machine_authored("ja und plane auch wie ich die Kundenseiten manage")


def test_pasted_dialog_is_detected() -> None:
    """Ein ganzes Gespraech im Nutzer-Feld: der Inhalt ist echt, die Aussage ist es nicht."""
    pasted = (
        "Soll ich Topstep nehmen?\n\nChatGPT:\nStarke Frage.\n\nDu:\nHm wie gehe ich vor?"
    )
    assert is_pasted_dialog(pasted)
    assert is_machine_authored(pasted)


def test_single_mention_is_not_a_pasted_dialog() -> None:
    assert not is_pasted_dialog("ChatGPT:\nkurzes Zitat, sonst nichts")
    assert not is_machine_authored("ich habe ChatGPT gefragt was es davon haelt")
