import pytest

from bamboozle.engine import Translator
from bamboozle.translate import Bamboozletranslate, process_forward, process_reverse


@pytest.mark.parametrize("translator_type", [Translator, Bamboozletranslate])
def test_single_pass_handles_nested_terms_and_literals(translator_type):
    translator = translator_type({"alpha": "omega alpha", "alpha beta": "delta"})
    original = "alpha beta; alpha; omega alpha; ALPHA BETA."
    encoded = translator.forward(original)
    assert encoded == "delta; omega alpha; __SCM_LIT_omega alpha__; DELTA."
    assert translator.reverse(encoded) == original


@pytest.mark.parametrize("text", ["alpha_name", "alpha\u0301", "αalpha", "alpha2"])
def test_identifier_boundaries_preserved(text):
    assert Translator({"alpha": "beta"}).forward(text) == text


@pytest.mark.parametrize("text", ["__SCM_LIT_beta__", "__scm_lit_broken"])
def test_legacy_protocol_rejects_raw_marker_input(text):
    with pytest.raises(ValueError):
        Translator({"alpha": "beta"}, marker_version=1).forward(text)


@pytest.mark.parametrize(
    "text", ["__SCM_LIT_unknown__", "__SCM_LIT_beta", "__SCM_LIT_"]
)
def test_damaged_literal_markers_rejected(text):
    with pytest.raises(ValueError, match="literal marker"):
        Translator({"alpha": "beta"}).reverse(text)


def test_legacy_string_functions_have_no_shared_ledger_side_effect(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    dictionary = {"alpha": "beta"}
    assert (
        process_forward("alpha beta", dictionary, "sample.txt")
        == "beta __SCM_LIT_beta__"
    )
    assert process_reverse("beta __SCM_LIT_beta__", dictionary) == "alpha beta"
    assert list(tmp_path.iterdir()) == []
