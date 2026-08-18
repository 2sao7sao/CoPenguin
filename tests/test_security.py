from feishu_computer_agent.config import Settings
from feishu_computer_agent.models import ChatType, InboundMessage
from feishu_computer_agent.security import AccessController, RiskClassifier


def test_access_controller_denies_by_default() -> None:
    message = InboundMessage(
        message_id="m1",
        chat_id="c1",
        chat_type=ChatType.DIRECT,
        sender_open_id="ou_unknown",
        text="hello",
    )

    assert not AccessController(Settings()).is_allowed(message)


def test_access_controller_allows_configured_open_id() -> None:
    message = InboundMessage(
        message_id="m1",
        chat_id="c1",
        chat_type=ChatType.DIRECT,
        sender_open_id="ou_owner",
        text="hello",
    )
    settings = Settings(feishu_allowed_open_ids=frozenset({"ou_owner"}))

    assert AccessController(settings).is_allowed(message)


def test_risk_classifier_marks_shell_and_destructive_commands() -> None:
    classifier = RiskClassifier()

    assert classifier.classify("shell: ls").value == "high"
    assert classifier.classify("please rm -rf /").value == "critical"
    assert classifier.classify("open my calendar and summarize today").value == "medium"
