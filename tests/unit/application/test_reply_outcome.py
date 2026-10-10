"""ReplyOutcome: 返答の文が、決まった行動と合っているか（docs/design/159）。"""

from ailoveshen.application.use_cases.reply_outcome import ALREADY, LATER, NOW, ReplyOutcome

NOW_BED = ReplyOutcome("accepted", title="ベッドを置く", what="placed(bed, home)", level="job", timing=NOW)
LATER_JOB = ReplyOutcome(
    "accepted", title="石を集める", what="have(cobblestone, 32)", level="job", timing=LATER,
    when_note="今の中目標「原木」のあとにやる。",
)


def test_something_done_now_cannot_be_said_to_wait():
    for said in ("朝になったら置くね", "あとで置くね", "家ができたら置くね", "終わってからやるよ"):
        assert NOW_BED.mismatch(said), said
    assert NOW_BED.mismatch("今すぐ置くね！") == ""
    assert NOW_BED.mismatch("ベッド置いて寝るね！") == ""
    assert ReplyOutcome("accepted", timing=ALREADY).mismatch("ちょうど今やってるよ") == ""


def test_something_done_later_cannot_be_said_to_be_done_right_away():
    for said in ("今すぐ集めるね", "さっそくやるよ", "今から集めるね"):
        assert LATER_JOB.mismatch(said), said
    assert LATER_JOB.mismatch("原木のあとに集めるね！") == ""


def test_a_decline_or_nothing_cannot_promise_an_action():
    declined = ReplyOutcome("declined", decline_reason="unsafe")
    assert declined.mismatch("あとで置くね！")
    assert declined.mismatch("危ないから、今回は見送るね") == ""
    assert ReplyOutcome("none").mismatch("覚えておくよ！") == ""


def test_the_facts_say_when_and_why():
    assert "今すぐやる" in NOW_BED.facts()
    assert "今の中目標「原木」のあと" in LATER_JOB.facts()
    declined = ReplyOutcome("declined", decline_reason="wrong", detail="ベッドは持っていない")
    text = declined.facts()
    assert "今の様子と合わない" in text and "ベッドは持っていない" in text and "やるとは言わない" in text


def test_the_fallback_line_is_built_from_the_outcome_only():
    assert NOW_BED.fallback("neko") == "nekoさん、「ベッドを置く」、今やるね！"
    assert LATER_JOB.fallback("neko") == "nekoさん、「石を集める」は今の中目標「原木」のあとにやる。"
    assert ReplyOutcome("declined", decline_reason="limit").fallback("neko").startswith("nekoさん、ごめんね、頼みの上限")
    assert ReplyOutcome("withdrawn").fallback("") == "取り下げたね、リストから外したよ！"
