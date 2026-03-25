with open("src/hermit/kernel/policy/guards/rules.py") as f:
    content = f.read()

old_block = """    for evaluator in evaluators:
        result = evaluator(request)
        if result is not None:
            result = _apply_policy_suggestion(request, result)
            result = _apply_task_pattern(request, result)
            return result

    # Unclassified mutable action: default to approval"""

new_block = """    # Collect results from ALL evaluators, then merge by strictest verdict.
    # This prevents an earlier APPROVAL_REQUIRED from shadowing a later DENY.
    _VERDICT_PRIORITY = {"deny": 4, "approval_required": 3, "preview_required": 2, "allow_with_receipt": 1, "allow": 0}
    collected: list[list[RuleOutcome]] = []
    for evaluator in evaluators:
        result = evaluator(request)
        if result is not None:
            result = _apply_policy_suggestion(request, result)
            result = _apply_task_pattern(request, result)
            collected.append(result)

    if collected:
        # Pick the result set whose most-restrictive outcome has the highest priority
        def _max_priority(outcomes: list[RuleOutcome]) -> int:
            return max(_VERDICT_PRIORITY.get(o.verdict, 0) for o in outcomes)

        collected.sort(key=_max_priority, reverse=True)
        return collected[0]

    # Unclassified mutable action: default to approval"""

assert old_block in content, "Old block not found!"
content = content.replace(old_block, new_block)

with open("src/hermit/kernel/policy/guards/rules.py", "w") as f:
    f.write(content)

print("Patch applied successfully.")
