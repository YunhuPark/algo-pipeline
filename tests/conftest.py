import pytest


@pytest.fixture(autouse=True)
def _no_real_cover_copy_llm_call(monkeypatch):
    """``ScriptAssembler.assemble()`` can call an LLM to write cover/hook/CTA
    copy so editorial repair feedback can actually change that text on retry.

    Tests must stay fast, free, and network-independent. Default every test
    to the deterministic template fallback (as if the LLM call failed) unless
    a test explicitly monkeypatches ``_generate_cover_copy`` again to exercise
    the LLM-driven path.
    """
    monkeypatch.setattr(
        "src.qa.script_assembler._generate_cover_copy",
        lambda *args, **kwargs: None,
    )
