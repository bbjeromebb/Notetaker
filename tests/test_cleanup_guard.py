from notetaker import cleanup

from test_core import FakeLLM

TERMS = ["Leiedal", "Kortrijk", "RUP", "MER", "GECORO"]


def clean(raw, llm):
    return cleanup.clean_dictation(raw, llm=llm, model="m", effort="low", style="default",
                                   dictionary=TERMS, replacements={"lei dal": "Leiedal"}, snippets={})


def test_short_clean_sentence_skips_the_model():
    """Uit de echte test: Whisper levert korte zinnen al correct; het model maakte ze slechter."""
    llm = FakeLLM("Leiedal, Kortrijk, RUP, MER, GECORO.")
    assert clean("Ja, prima.", llm) == "Ja, prima."
    assert clean("Kom je mee?", llm) == "Kom je mee?"
    assert llm.calls == []


def test_model_used_for_fillers_and_only_spoken_terms_sent():
    llm = FakeLLM("Het RUP van Kortrijk.")
    assert clean("euh het rup van kortrijk", llm) == "Het RUP van Kortrijk."
    assert "exact zo: Kortrijk, RUP" in llm.calls[0][1] and "Leiedal" not in llm.calls[0][1]


def test_model_that_returns_the_term_list_is_ignored():
    assert clean("euh kom je mee", FakeLLM("Leiedal, Kortrijk, RUP, MER, GECORO.")) == "Kom je mee."


def test_model_that_answers_instead_of_cleaning_is_ignored():
    assert clean("euh hoe laat is het", FakeLLM("Het is drie uur in de namiddag.")) == "Hoe laat is het."


def test_model_that_drops_content_is_ignored():
    raw = "euh kan je het verslag van de stuurgroep nog eens nalezen voor vrijdag"
    out = clean(raw, FakeLLM("Kan je het verslag nog eens nalezen voor vrijdag?"))
    assert "stuurgroep" in out


def test_self_correction_may_drop_words():
    raw = "we vertrekken om drie uur nee wacht om vier uur"
    assert clean(raw, FakeLLM("We vertrekken om vier uur.")) == "We vertrekken om vier uur."


def test_near_terms_are_corrected():
    assert cleanup.fix_near_terms("Stuur dat naar Leidal.", TERMS) == "Stuur dat naar Leiedal."
    assert cleanup.fix_near_terms("Dat is meer werk.", TERMS) == "Dat is meer werk."
    assert cleanup.fix_near_terms("Kortryk en Gecoro", TERMS) == "Kortrijk en GECORO"
    assert clean("Stuur dat naar Leidal.", FakeLLM("x")) == "Stuur dat naar Leiedal."


def test_needs_llm():
    assert not cleanup.needs_llm("Bel me morgen terug.")
    assert cleanup.needs_llm("euh bel me")
    assert cleanup.needs_llm("eerste zin komma tweede zin")
    assert cleanup.needs_llm(" ".join(["woord"] * 30))


def test_unfaithful_rule():
    assert cleanup.looks_unfaithful("kom je mee", "Leiedal, Kortrijk.")
    assert not cleanup.looks_unfaithful("ja euh ja", "Ja.")
    assert cleanup.looks_unfaithful("iets", "")
