import json
from make_note import _build_client, wait_for_completion

try:
    client = _build_client()
    task_id = "a4119e2f85df4a98a06f25046e7014ab"
    res = wait_for_completion(client, task_id)
    with open("raw_res_cp.json", "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("Saved to raw_res_cp.json")
except Exception as e:
    print("Error:", e)
