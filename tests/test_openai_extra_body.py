from qitos.models.openai import _relocate_chat_template_kwargs


def test_provider_only_generation_kwargs_use_openai_sdk_extra_body():
    result = _relocate_chat_template_kwargs(
        {"do_sample": True, "chat_template_kwargs": {"enable_thinking": False}, "tools": ["tool"]}
    )
    assert result["tools"] == ["tool"]
    assert "do_sample" not in result
    assert "chat_template_kwargs" not in result
    assert result["extra_body"] == {"do_sample": True, "chat_template_kwargs": {"enable_thinking": False}}
