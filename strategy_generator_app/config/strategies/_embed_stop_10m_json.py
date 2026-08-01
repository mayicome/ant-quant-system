# 一次性脚本：把 position_sell_stop_10m_after_open.py 写入 strategy_f12e82e8.json 的 strategy_code
import json
from pathlib import Path

here = Path(__file__).resolve().parent
py_path = here / "position_sell_stop_10m_after_open.py"
json_path = here / "strategy_f12e82e8.json"
code = py_path.read_text(encoding="utf-8")
data = json.loads(json_path.read_text(encoding="utf-8"))
data["strategy_code"] = code
json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print("OK:", json_path)
