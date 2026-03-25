#!/usr/bin/env python3
"""Apply targeted exception handling fixes."""

import sys
from pathlib import Path

ROOT = Path("/Users/beta/work/Hermit")


def patch(filepath: str, old: str, new: str, label: str) -> None:
    p = ROOT / filepath
    content = p.read_text()
    if old not in content:
        print(f"FAIL [{label}]: pattern not found in {filepath}", file=sys.stderr)
        sys.exit(1)
    if content.count(old) > 1:
        print(
            f"FAIL [{label}]: pattern appears {content.count(old)} times (expected 1) in {filepath}",
            file=sys.stderr,
        )
        sys.exit(1)
    p.write_text(content.replace(old, new))
    verify = p.read_text()
    if new not in verify:
        print(f"FAIL [{label}]: replacement text not found after write", file=sys.stderr)
        sys.exit(1)
    if old in verify:
        print(f"FAIL [{label}]: old text still present after write", file=sys.stderr)
        sys.exit(1)
    print(f"  OK [{label}]: {filepath}")


# 1. hooks_promotion.py — embedding index except block
PROMO = "src/hermit/plugins/builtin/hooks/memory/hooks_promotion.py"

patch(
    PROMO,
    "            except Exception:\n"
    "                import structlog as _log\n"
    "\n"
    "                _log.get_logger().warning(\n"
    '                    "embedding_index_failed_non_critical", memory_ids=promoted_memories\n'
    "                )",
    "            except (ImportError, OSError, RuntimeError) as exc:\n"
    "                import structlog as _log\n"
    "\n"
    "                _log.get_logger().warning(\n"
    '                    "embedding_index_failed_non_critical",\n'
    "                    memory_ids=promoted_memories,\n"
    "                    error=str(exc),\n"
    "                )",
    "promo-embedding",
)

# 2. hooks_promotion.py — enrichment records except block
patch(
    PROMO,
    "            except Exception:\n"
    "                log.warning(\n"
    '                    "enrichment_records_failed_non_critical",\n'
    "                    memory_ids=promoted_memories,",
    "            except (OSError, RuntimeError) as exc:\n"
    "                log.warning(\n"
    '                    "enrichment_records_failed_non_critical",\n'
    "                    memory_ids=promoted_memories,\n"
    "                    error=str(exc),",
    "promo-enrichment",
)

# 3a. retrieval.py — _semantic_rank
RET = "src/hermit/kernel/context/memory/retrieval.py"

patch(
    RET,
    "        except Exception:\n"
    '            log.debug("semantic_rank_fallback")\n'
    "            return []",
    "        except (ImportError, OSError, RuntimeError) as exc:\n"
    '            log.warning("semantic_rank_fallback", error=str(exc))\n'
    "            return []",
    "semantic-rank",
)

# 3b. retrieval.py — _procedural_rank
patch(
    RET,
    "            return ranked\n"
    "        except Exception:\n"
    "            return []\n"
    "\n"
    "    @staticmethod\n"
    "    def _entity_rank",
    "            return ranked\n"
    "        except (ImportError, OSError, RuntimeError) as exc:\n"
    '            log.warning("procedural_rank_failed", error=str(exc))\n'
    "            return []\n"
    "\n"
    "    @staticmethod\n"
    "    def _entity_rank",
    "procedural-rank",
)

# 3c. retrieval.py — _entity_rank
patch(
    RET,
    "            return ranked\n"
    "        except Exception:\n"
    "            return []\n"
    "\n"
    "    @staticmethod\n"
    "    def _importance_rank",
    "            return ranked\n"
    "        except (OSError, RuntimeError) as exc:\n"
    '            log.warning("entity_rank_failed", error=str(exc))\n'
    "            return []\n"
    "\n"
    "    @staticmethod\n"
    "    def _importance_rank",
    "entity-rank",
)

# 4. benchmark/hooks.py — task callback
BENCH = "src/hermit/plugins/builtin/hooks/benchmark/hooks.py"

patch(
    BENCH,
    "        _background_tasks.add(task)\n"
    "        task.add_done_callback(_background_tasks.discard)",
    "        _background_tasks.add(task)\n"
    "\n"
    "        def _on_task_done(t: asyncio.Task) -> None:\n"
    "            _background_tasks.discard(t)\n"
    "            if not t.cancelled() and t.exception():\n"
    '                log.error("benchmark_task_failed", error=str(t.exception()))\n'
    "\n"
    "        task.add_done_callback(_on_task_done)",
    "benchmark-callback",
)

# 5. telegram/hooks.py — send task callback
TELE = "src/hermit/plugins/builtin/adapters/telegram/hooks.py"

patch(
    TELE,
    "            _task = loop.create_task(send_message(bot, chat_id, text))  # noqa: RUF006",
    "            _task = loop.create_task(send_message(bot, chat_id, text))  # noqa: RUF006\n"
    "\n"
    "            def _on_send_done(t: asyncio.Task) -> None:\n"
    "                if not t.cancelled() and t.exception():\n"
    '                    _log.error("telegram_send_task_failed: %s", t.exception())\n'
    "\n"
    "            _task.add_done_callback(_on_send_done)",
    "telegram-callback",
)

# 6. slack/hooks.py — send task callback
SLACK = "src/hermit/plugins/builtin/adapters/slack/hooks.py"

patch(
    SLACK,
    "            _task = loop.create_task(send_message(client, channel_id, text))  # noqa: RUF006",
    "            _task = loop.create_task(send_message(client, channel_id, text))  # noqa: RUF006\n"
    "\n"
    "            def _on_send_done(t: asyncio.Task) -> None:\n"
    "                if not t.cancelled() and t.exception():\n"
    '                    _log.error("slack_send_task_failed: %s", t.exception())\n'
    "\n"
    "            _task.add_done_callback(_on_send_done)",
    "slack-callback",
)

print("\nAll patches applied and verified successfully.")
