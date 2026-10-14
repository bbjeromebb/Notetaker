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


def test_whisper_writes_flemish_euh_as_u():
    """Uit de echte test: 'Euh, kan je mij morgen, euh, even bellen?' werd 'U, ... U, ...'."""
    assert cleanup.needs_llm("U, kan je mij morgen, U, even bellen?")
    llm = FakeLLM("Kan je mij morgen even bellen?")
    assert clean("U, kan je mij morgen, U, even bellen?", llm) == "Kan je mij morgen even bellen?"
    for polite in ("Kan u mij bellen?", "Dank u, meneer.", "Wilt u, als het kan, bellen?"):
        assert not cleanup.needs_llm(polite)
        assert clean(polite, FakeLLM("fout")) == polite


def test_whisper_variants_from_real_test_reach_the_model():
    """Run 4 met gemma3:12b: Whisper schreef 'Uw,' en 'Nee, wacht,' met leestekens ertussen."""
    assert cleanup.needs_llm("Uw, kan je mij morgen? Uw, even bellen?")
    assert cleanup.needs_llm("We vertrekken om 3 uur. Nee, wacht, om 4 uur.")
    assert not cleanup.needs_llm("Is uw dossier klaar?")
    out = clean("We vertrekken om 3 uur. Nee, wacht, om 4 uur.", FakeLLM("We vertrekken om 4 uur."))
    assert out == "We vertrekken om 4 uur."


def test_u_filler_removed_before_the_model():
    """Run 5: gemma3:12b liet 'Uw,' aan het begin staan."""
    assert cleanup.strip_u_fillers("Uw, kan je mij morgen? Uw, even bellen?") == "Kan je mij morgen? even bellen?"
    llm = FakeLLM("Kan je mij morgen even bellen?")
    assert clean("Uw, kan je mij morgen? Uw, even bellen?", llm) == "Kan je mij morgen even bellen?"
    assert "Uw" not in llm.calls[0][1]


def test_invented_placeholder_is_removed():
    """Run 6: gemma3:12b schreef '⟦S0⟧' achter een zin zonder snippets."""
    llm = FakeLLM("Kan je mij morgen even bellen? ⟦S0⟧")
    assert clean("Euh, kan je mij morgen? Euh, even bellen?", llm) == "Kan je mij morgen even bellen?"
    assert "⟦" not in llm.calls[0][0] and "⟦" not in llm.calls[0][1]


def test_real_snippet_placeholder_still_explained():
    out = cleanup.clean_dictation("euh stuur mijn agenda link door", llm=FakeLLM("Stuur ⟦S0⟧ door."),
                                  model="m", effort="low", style="default", dictionary=[],
                                  replacements={}, snippets={"mijn agenda link": "https://cal.example"})
    assert out == "Stuur https://cal.example door."
