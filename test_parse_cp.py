import json
import os

import pytest

from make_note import _build_client, wait_for_completion


pytestmark = pytest.mark.skipif(
    not os.getenv("TINGWU_TEST_TASK_ID"),
    reason="Set TINGWU_TEST_TASK_ID to run the live Tingwu integration test.",
)


def test_wait_for_completion_live_task(tmp_path):
    client = _build_client()
    task_id = os.environ["TINGWU_TEST_TASK_ID"]
    result = wait_for_completion(client, task_id)

    output_path = tmp_path / "raw_res_cp.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    assert result["Data"]["TaskId"] == task_id
