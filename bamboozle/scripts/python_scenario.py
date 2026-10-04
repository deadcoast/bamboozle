"""Complete dependency-planning application and agent-authored scheduling enhancement."""

from pathlib import Path
from textwrap import dedent

MODEL = """
from dataclasses import dataclass

@dataclass(frozen=True)
class Task:
    name: str
    weight: int
    dependencies: tuple[str, ...]

def load(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError("nonempty task array required")
    tasks = []
    names = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"name", "weight", "dependencies"}:
            raise ValueError("invalid task fields")
        name, weight, dependencies = row["name"], row["weight"], row["dependencies"]
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("duplicate or invalid task name")
        if type(weight) is not int or not 1 <= weight <= 1000000:
            raise ValueError("positive bounded integer weight required")
        if not isinstance(dependencies, list) or any(not isinstance(value, str) for value in dependencies) or len(set(dependencies)) != len(dependencies):
            raise ValueError("distinct dependency names required")
        names.add(name)
        tasks.append(Task(name, weight, tuple(dependencies)))
    if any(value not in names for task in tasks for value in task.dependencies):
        raise ValueError("unknown dependency")
    return {task.name: task for task in tasks}
"""

GRAPH = """
from heapq import heappop, heappush
from .model import Task, load

# Task, build, weight and total in this comment remain literal.
PROTOCOL_EXAMPLE = "__SCM_LIT_bad__ __SCM_CASE_"
RESOURCE = r"C:\\models\\Task\\weight.json"
__all__ = ["build"]

def build(rows):
    tasks: dict[str, Task] = load(rows)
    incoming = {name: len(task.dependencies) for name, task in tasks.items()}
    following = {name: [] for name in tasks}
    for name, task in tasks.items():
        for parent in task.dependencies:
            following[parent].append(name)
    ready = []
    for name, count in incoming.items():
        if count == 0:
            heappush(ready, name)
    order, finish = [], {}
    while ready:
        name = heappop(ready)
        task = tasks[name]
        finish[name] = max((finish[parent] for parent in task.dependencies), default=0) + task.weight
        order.append(name)
        for child in sorted(following[name]):
            incoming[child] -= 1
            if incoming[child] == 0:
                heappush(ready, child)
    if len(order) != len(tasks):
        raise ValueError("dependency cycle")
    total = sum(task.weight for task in tasks.values())
    return {"order": order, "finish": finish, "total": total, "critical": max(finish.values())}
"""

MAIN = """
import argparse
import json
from pathlib import Path
from .graph import build

def run(rows):
    return build(rows)

def main():
    parser = argparse.ArgumentParser(description="Validate and plan dependency workloads")
    parser.add_argument("input")
    args = parser.parse_args()
    try:
        result = run(json.loads(Path(args.input).read_text(encoding="utf-8")))
    except (ValueError, OSError) as error:
        print(json.dumps({"status": "invalid", "error": str(error)}))
        return 2
    print(json.dumps({"status": "planned", **result}, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
"""

# Agent-created modules use only the public neutral symbol contract. They are
# deliberately authored independently of the original-domain dictionary.
TIMELINE = """
from heapq import heappop, heappush
from .model import load
from .graph import assemble

def simulate(rows, capacity):
    if type(capacity) is not int or not 1 <= capacity <= 64:
        raise ValueError("capacity must be an integer from 1 to 64")
    plan = assemble(rows)
    tasks = load(rows)
    completed, launched = set(), set()
    running, intervals = [], {}
    free = set(range(capacity))
    now = 0
    while len(completed) != len(tasks):
        ready = [name for name in plan["order"] if name not in launched and set(tasks[name].dependencies) <= completed]
        for name in ready[:len(free)]:
            worker = min(free)
            free.remove(worker)
            launched.add(name)
            finish = now + tasks[name].effort
            intervals[name] = {"start": now, "finish": finish, "worker": worker}
            heappush(running, (finish, name, worker))
        if not running:
            raise ValueError("unschedulable workload")
        now = running[0][0]
        while running and running[0][0] == now:
            _, name, worker = heappop(running)
            completed.add(name)
            free.add(worker)
    return {**plan, "makespan": now, "intervals": intervals, "capacity": capacity}
"""

BENCH = """
import copy
import json
import random
from pipeline.main import execute
from pipeline.graph import assemble, PROTOCOL_EXAMPLE, RESOURCE

rng = random.Random(72831)
executions = 0
for size in range(1, 26):
    for repetition in range(4):
        rows = [{"name": str(index), "weight": rng.randrange(1, 13), "dependencies": [str(parent) for parent in range(index) if rng.random() < .18]} for index in range(size)]
        # Independent recurrence in generation order, separate from the returned
        # planner's heap traversal and event-driven capacity scheduler.
        ends = {}
        for row in rows:
            ends[row["name"]] = max((ends[parent] for parent in row["dependencies"]), default=0) + row["weight"]
        for capacity in (1, 2, 32):
            result = execute(rows, capacity)
            intervals = result["intervals"]
            assert set(intervals) == set(ends)
            assert result["critical"] == max(ends.values())
            assert result["total"] == sum(row["weight"] for row in rows)
            for row in rows:
                span = intervals[row["name"]]
                assert span["finish"] - span["start"] == row["weight"]
                assert all(intervals[parent]["finish"] <= span["start"] for parent in row["dependencies"])
            times = {item["start"] for item in intervals.values()} | {item["finish"] for item in intervals.values()}
            for moment in times:
                active = [item for item in intervals.values() if item["start"] <= moment < item["finish"]]
                assert len(active) <= capacity
                assert len({item["worker"] for item in active}) == len(active)
            assert result["makespan"] == max(item["finish"] for item in intervals.values())
            if capacity == 1:
                assert result["makespan"] == result["total"]
            if capacity == 32:
                assert result["makespan"] == result["critical"]
            executions += 1
valid = [{"name": "a", "weight": 2, "dependencies": []}]
bad = [[], {}, valid + valid, [{"name":"a", "weight":2,"dependencies":["missing"]}], [{"name":"a","weight":2,"dependencies":["a"]}]]
for value in (True, 0, -1, 1000001, "2", None):
    rows = copy.deepcopy(valid)
    rows[0]["weight"] = value
    bad.append(rows)
rows = copy.deepcopy(valid)
rows[0]["dependencies"] = ["a", "a"]
bad.append(rows)
for rows in bad:
    try:
        execute(rows, 2)
    except ValueError:
        pass
    else:
        raise AssertionError(rows)
for capacity in (0, -1, True, 65, "2"):
    try:
        execute(valid, capacity)
    except ValueError:
        pass
    else:
        raise AssertionError(capacity)
assert PROTOCOL_EXAMPLE == "__SCM_LIT_bad__ __SCM_CASE_"
assert RESOURCE == r"C:\\models\\Task\\weight.json"
print(json.dumps({"workloads":100,"scheduler_executions":executions,"invalid_workloads":len(bad),"invalid_capacities":5,"protected_literals_preserved":True}, sort_keys=True))
"""


def originals(root: Path) -> dict[str, bytes]:
    result = {
        "pipeline/__init__.py": b"",
        "pipeline/model.py": dedent(MODEL).lstrip().encode(),
        "pipeline/graph.py": b"\xef\xbb\xbf"
        + dedent(GRAPH).lstrip().replace("\n", "\r\n").encode(),
        "pipeline/main.py": dedent(MAIN).lstrip().encode(),
        "workload.json": b'[{"name":"a","weight":3,"dependencies":[]},{"name":"b","weight":2,"dependencies":[]},{"name":"c","weight":5,"dependencies":["a","b"]}]',
    }
    for name, raw in result.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return result


def agent_edit(view: Path) -> None:
    main = view / "pipeline/main.py"
    text = main.read_text(encoding="utf-8")
    text = text.replace("from .graph import assemble", "from .timeline import simulate")
    text = text.replace(
        "def execute(rows):\n    return assemble(rows)",
        "def execute(rows, capacity=2):\n    return simulate(rows, capacity)",
    )
    text = text.replace(
        'parser.add_argument("input")',
        'parser.add_argument("input")\n    parser.add_argument("--capacity", type=int, default=2)',
    )
    text = text.replace('encoding="utf-8")))', 'encoding="utf-8")), args.capacity)')
    main.write_text(text, encoding="utf-8", newline="")
    (view / "pipeline/timeline.py").write_text(
        dedent(TIMELINE).lstrip(), encoding="utf-8", newline=""
    )
    (view / "bench.py").write_text(dedent(BENCH).lstrip(), encoding="utf-8", newline="")


def rules() -> dict:
    return {
        "format": "python",
        "identifiers": ["Task", "build", "run", "weight", "total"],
        "modules": [
            ".model",
            ".graph",
            ".timeline",
            "pipeline.model",
            "pipeline.graph",
            "pipeline.main",
            "pipeline.timeline",
        ],
        "strings": ["build"],
    }
