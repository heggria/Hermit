window.BENCHMARK_DATA = {
  "lastUpdate": 1773752123193,
  "repoUrl": "https://github.com/heggria/Hermit",
  "entries": {
    "Benchmark": [
      {
        "commit": {
          "author": {
            "email": "bshengtao@gmail.com",
            "name": "Heggria",
            "username": "heggria"
          },
          "committer": {
            "email": "noreply@github.com",
            "name": "GitHub",
            "username": "web-flow"
          },
          "distinct": true,
          "id": "e4114eab1c6962683499f768c293f5a0c6cb3a2c",
          "message": "feat: 将所有硬编码中文字符串提取到 i18n 系统 (#17)\n\n* feat: 将所有硬编码中文字符串提取到 i18n 系统\n\n将 ~250 个硬编码中文字符串从 22 个 Python 源文件提取到 locale 文件中，\n通过 tr() / tr_list() / tr_list_all_locales() 加载。\n\n主要变更：\n- 新增 tr_list_all_locales() 辅助函数，合并所有 locale 的 NLP 模式\n- 内存分类从中文改为英文内部常量，添加 _LEGACY_CATEGORY_MAP 向后兼容\n- NLP 模式（问候语、续接标记、控制意图等）改为从 locale 加载\n- LLM prompt、用户界面字符串全部通过 tr() 国际化\n- SQLite schema v7→v8 迁移，更新 category 列\n- 更新所有测试文件中的分类字符串引用\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: 修复 slack/telegram adapter 和 template_learner 的 pyright 错误\n\n- slack adapter: 添加 app 公开属性，标记 handler 函数为已使用\n- telegram adapter: 添加 application 公开属性\n- template_learner: 为 dataclass 字段添加显式类型注解\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* Revert \"fix: 修复 slack/telegram adapter 和 template_learner 的 pyright 错误\"\n\nThis reverts commit 608e35c8a1b3eb634605004be370a6849f2eafc7.\n\n* feat: i18n 提取、文档站优化、新增 slack/telegram adapter 及 TUI 基础\n\n- 提取 CLI/kernel/executor/approval 中剩余硬编码中文到 i18n 系统\n- 优化 docs landing page、mkdocs 配置、新增 blog/tags 支持\n- 新增 slack/telegram adapter 和 template_learner\n- 新增 CLI TUI 模块基础结构\n- 补充 e2e 测试（schedule、signed_proofs、uncertain_outcome 等）\n- CI 新增 pyright 类型检查 job\n- 更新 pyproject.toml 依赖和 uv.lock\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: 修复 TUI 模块的 pyright 类型错误\n\n- App 添加泛型参数 App[None]\n- action_quit / _on_key 改为 async 以匹配基类签名\n- 补充 **kwargs / event 参数类型注解\n- parts 列表添加 list[str] 类型标注\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: 补充 telegram adapter/hooks 测试覆盖并排除 TUI 模块 coverage\n\n- 新增 telegram adapter 单元测试（init、session_id、dedup、stop 等）\n- 新增 telegram hooks 单元测试（dispatch_result 各场景、register）\n- 新增 preflight 测试覆盖 telegram/slack adapter 配置检查\n- TUI 模块加入 coverage omit（UI widget 难以单元测试）\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: 补充 slack adapter 测试并标记不可单元测试的 async 方法\n\n- 新增 slack adapter/hooks 单元测试\n- adapter async 方法（start/on_message/sweep）添加 pragma: no cover\n  这些方法依赖 Telegram/Slack SDK 实际网络连接，需集成测试覆盖\n- TUI 入口代码块添加 pragma: no cover\n- coverage 配置新增 exclude_also 规则\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: 为 benchmark CI step 添加 github-token 以支持 PR 评论\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix(i18n): 修复 i18n 标记匹配时的大小写不一致问题\n\n- continuation.py: normalize_text 添加 .lower() 使英文 corrective markers 能正确匹配\n- actions.py: _is_accessibility_error 对 i18n marker 做 lowercase 后再比较\n\n* fix(ci): 添加 CI gate job 以匹配 branch protection 要求的 status check\n\nbranch protection 要求名为 \"CI\" 的 check，但之前没有任何 job\n产生该名称的 check run，导致 PR 永远 pending。\n\n---------\n\nCo-authored-by: Claude Opus 4.6 <noreply@anthropic.com>",
          "timestamp": "2026-03-17T20:54:34+08:00",
          "tree_id": "e20175c99d3928c3343f85cac2a8555b75673f06",
          "url": "https://github.com/heggria/Hermit/commit/e4114eab1c6962683499f768c293f5a0c6cb3a2c"
        },
        "date": 1773752122330,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/benchmark/test_bench_io.py::TestCLIStartupBenchmarks::test_hermit_help_startup",
            "value": 2.0852710740854956,
            "unit": "iter/sec",
            "range": "stddev: 0.007941230908196633",
            "extra": "mean: 479.5539594000047 msec\nrounds: 5"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_init",
            "value": 177.05498372579507,
            "unit": "iter/sec",
            "range": "stddev: 0.00004106373998182569",
            "extra": "mean: 5.647963016667745 msec\nrounds: 180"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_append_event",
            "value": 2431.8330984112026,
            "unit": "iter/sec",
            "range": "stddev: 0.00014136376368401233",
            "extra": "mean: 411.21243092436447 usec\nrounds: 2975"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_list_tasks",
            "value": 3974.5885735738993,
            "unit": "iter/sec",
            "range": "stddev: 0.000011189462816076395",
            "extra": "mean: 251.59836835660522 usec\nrounds: 4083"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_write",
            "value": 5008.983399624251,
            "unit": "iter/sec",
            "range": "stddev: 0.00008260396102796155",
            "extra": "mean: 199.64130846890305 usec\nrounds: 6140"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_read",
            "value": 29398.47304350307,
            "unit": "iter/sec",
            "range": "stddev: 0.0000038798258813343644",
            "extra": "mean: 34.0153721086203 usec\nrounds: 30999"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_update",
            "value": 3227.4993438840993,
            "unit": "iter/sec",
            "range": "stddev: 0.00009136183981383507",
            "extra": "mean: 309.83739838551315 usec\nrounds: 3592"
          }
        ]
      }
    ]
  }
}