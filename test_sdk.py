from alibabacloud_tingwu20230930 import models as tingwu_models
cp = tingwu_models.CreateTaskRequestParametersCustomPromptContents(
    name="test",
    prompt="test",
    trans_type="transcription"
)
print(cp.to_map())
