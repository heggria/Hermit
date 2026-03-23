path = "src/hermit/kernel/execution/coordination/observation.py"
with open(path) as f:
    content = f.read()

# 1. Add structlog import and _log after the existing imports block
content = content.replace(
    "from hermit.runtime.control.lifecycle.budgets import ExecutionBudget, get_runtime_budget\n",
    "import structlog\n\nfrom hermit.runtime.control.lifecycle.budgets import ExecutionBudget, get_runtime_budget\n\n_log = structlog.get_logger()\n",
)

# 2. Add logging in _loop's except block
content = content.replace(
    "            except Exception:\n                continue\n",
    "            except Exception:\n                _log.warning('observation_service.tick_error', exc_info=True)\n                continue\n",
    1,  # only first occurrence
)

with open(path, "w") as f:
    f.write(content)
print("Done")
