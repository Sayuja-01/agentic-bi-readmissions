"""
Reasoning trace logger.

Every node in the pipeline calls log_step() at each meaningful action.
This is the "proof of work" artifact — at the end of a run, the full trace
gets saved as JSON so you (or an interviewer) can walk through exactly what
the agent planned, queried, got wrong, self-corrected, and concluded.
"""

import json
import os
from datetime import datetime, timezone


class ReasoningTrace:
    def __init__(self):
        self.steps = []
        self._counter = 0

    def log(self, node, input_data=None, prompt_sent=None, model_output=None,
            parsed_result=None, status="success", error=None, retry_count=0):
        self._counter += 1
        entry = {
            "step_id": self._counter,
            "node": node,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "input": input_data,
            "prompt_sent": prompt_sent,
            "model_output": model_output,
            "parsed_result": parsed_result,
            "status": status,
            "error": error,
            "retry_count": retry_count,
        }
        self.steps.append(entry)
        return entry

    def save(self, path=None):
        if path is None:
            os.makedirs("logs", exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
            path = os.path.join("logs", f"run_{timestamp}.json")
        with open(path, "w") as f:
            json.dump(self.steps, f, indent=2, default=str)
        return path
