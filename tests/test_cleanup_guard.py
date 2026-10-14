from notetaker import cleanup

from test_core import FakeLLM

TERMS = ["Leiedal", "Kortrijk", "RUP", "MER", "GECORO"]


def clean(raw, llm):
    return cleanup.clean_dictation(raw, llm=llm, model="m", effort="low", style="default",
                                   dictionary=TERMS, replacements={}, snippets={})


def test_only_spoken_terms_reach_the_model():
    llm = FakeLLM("Kom je mee?")
    clean("kom je mee", llm)
    assert "Leiedal" not in llm.calls[0][1] and "termen" not in llm.calls[0][1]
    llm = FakeLLM("Het RUP van Kortrijk.")
    clean("het rup van kortrijk", llm)
    assert "exact zo: Kortrijk, RUP" in llm.calls[0][1]


def test_model_that_returns_the_term_list_is_ignored():
    out = clean("kom je mee", FakeLLM("Leiedal, Kortrijk, RUP, MER, GECORO."))
    assert out == "Kom je mee."


def test_model_that_answers_instead_of_cleaning_is_ignored():
    assert clean("hoe laat is het", FakeLLM("Het is drie uur in de namiddag.")) == "Hoe laat is het."


def test_normal_cleanup_is_kept():
    assert clean("euh kom je euh mee", FakeLLM("Kom je mee?")) == "Kom je mee?"
    assert clean("stuur dat naar lei dal", FakeLLM("Stuur dat naar Leiedal.")) == "Stuur dat naar Leiedal."


def test_unfaithful_rule():
    assert cleanup.looks_unfaithful("kom je mee", "Leiedal, Kortrijk.")
    assert not cleanup.looks_unfaithful("ja euh ja", "Ja.")
    assert cleanup.looks_unfaithful("iets", "")
