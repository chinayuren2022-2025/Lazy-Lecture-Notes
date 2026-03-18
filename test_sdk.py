from alibabacloud_tingwu20230930 import models as tingwu_models


def test_custom_prompt_content_to_map():
    content = tingwu_models.CreateTaskRequestParametersCustomPromptContents(
        name="test",
        prompt="test",
        trans_type="transcription",
    )

    assert content.to_map() == {
        "Name": "test",
        "Prompt": "test",
        "TransType": "transcription",
    }
