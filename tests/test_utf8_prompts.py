import pytest
import json

def test_utf8_decode():
    # Load all prompts that might have utf-8 issues
    from src.qa.semantic_critic import _CRITIC_SYSTEM_PROMPT
    prompts = [_CRITIC_SYSTEM_PROMPT]

    try:
        from src.qa.claim_generator import _CLAIM_SYSTEM_PROMPT
        prompts.append(_CLAIM_SYSTEM_PROMPT)
    except ImportError:
        pass

    try:
        from src.qa.script_assembler import _ASSEMBLER_SYSTEM_PROMPT
        prompts.append(_ASSEMBLER_SYSTEM_PROMPT)
    except ImportError:
        pass

    try:
        from src.agents.content_creator import _CREATOR_SYSTEM_PROMPT
        prompts.append(_CREATOR_SYSTEM_PROMPT)
    except ImportError:
        pass

    for prompt in prompts:
        assert prompt is not None
        assert "\ufffd" not in prompt # U+FFFD (Replacement character)

        # Check mojibake patterns
        assert "ë" not in prompt
        assert "í" not in prompt

        # JSON serialization round-trip
        data = {"p": prompt}
        dumped = json.dumps(data, ensure_ascii=False)
        loaded = json.loads(dumped)
        assert loaded["p"] == prompt
