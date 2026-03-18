window.BENCHMARK_DATA = {
  "lastUpdate": 1773823844033,
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
      },
      {
        "commit": {
          "author": {
            "email": "49699333+dependabot[bot]@users.noreply.github.com",
            "name": "dependabot[bot]",
            "username": "dependabot[bot]"
          },
          "committer": {
            "email": "noreply@github.com",
            "name": "GitHub",
            "username": "web-flow"
          },
          "distinct": true,
          "id": "4c221a9717d277815cc83edabb2089777140f733",
          "message": "chore: bump actions/download-artifact from 4.3.0 to 8.0.1 (#16)\n\nBumps [actions/download-artifact](https://github.com/actions/download-artifact) from 4.3.0 to 8.0.1.\n- [Release notes](https://github.com/actions/download-artifact/releases)\n- [Commits](https://github.com/actions/download-artifact/compare/d3f86a106a0bac45b974a628896c90dbdf5c8093...3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c)\n\n---\nupdated-dependencies:\n- dependency-name: actions/download-artifact\n  dependency-version: 8.0.1\n  dependency-type: direct:production\n  update-type: version-update:semver-major\n...\n\nSigned-off-by: dependabot[bot] <support@github.com>\nCo-authored-by: dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>",
          "timestamp": "2026-03-17T21:02:11+08:00",
          "tree_id": "57b027153a24b04c54d17ce57ea462d909a746ef",
          "url": "https://github.com/heggria/Hermit/commit/4c221a9717d277815cc83edabb2089777140f733"
        },
        "date": 1773752580720,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/benchmark/test_bench_io.py::TestCLIStartupBenchmarks::test_hermit_help_startup",
            "value": 2.1863105564620473,
            "unit": "iter/sec",
            "range": "stddev: 0.0039269455286199345",
            "extra": "mean: 457.39156179999867 msec\nrounds: 5"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_init",
            "value": 176.89157196715612,
            "unit": "iter/sec",
            "range": "stddev: 0.00006930186402096104",
            "extra": "mean: 5.653180583333119 msec\nrounds: 180"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_append_event",
            "value": 2605.8456826884526,
            "unit": "iter/sec",
            "range": "stddev: 0.00019312302404639193",
            "extra": "mean: 383.75257853653846 usec\nrounds: 3075"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_list_tasks",
            "value": 4004.6548388085757,
            "unit": "iter/sec",
            "range": "stddev: 0.000013520190624106734",
            "extra": "mean: 249.70941073600986 usec\nrounds: 4117"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_write",
            "value": 5670.4711180508375,
            "unit": "iter/sec",
            "range": "stddev: 0.00006179996483825615",
            "extra": "mean: 176.35218999999756 usec\nrounds: 6400"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_read",
            "value": 28846.179644956672,
            "unit": "iter/sec",
            "range": "stddev: 0.000003476240968767937",
            "extra": "mean: 34.666635662266465 usec\nrounds: 30329"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_update",
            "value": 2985.1468548148505,
            "unit": "iter/sec",
            "range": "stddev: 0.0008970293711402258",
            "extra": "mean: 334.9918944145291 usec\nrounds: 3921"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "email": "49699333+dependabot[bot]@users.noreply.github.com",
            "name": "dependabot[bot]",
            "username": "dependabot[bot]"
          },
          "committer": {
            "email": "noreply@github.com",
            "name": "GitHub",
            "username": "web-flow"
          },
          "distinct": true,
          "id": "e14e164d0ce64d2f0fcc293630962667f15899a3",
          "message": "chore: bump actions/attest-build-provenance from 2.4.0 to 4.1.0 (#15)\n\nBumps [actions/attest-build-provenance](https://github.com/actions/attest-build-provenance) from 2.4.0 to 4.1.0.\n- [Release notes](https://github.com/actions/attest-build-provenance/releases)\n- [Changelog](https://github.com/actions/attest-build-provenance/blob/main/RELEASE.md)\n- [Commits](https://github.com/actions/attest-build-provenance/compare/e8998f949152b193b063cb0ec769d69d929409be...a2bbfa25375fe432b6a289bc6b6cd05ecd0c4c32)\n\n---\nupdated-dependencies:\n- dependency-name: actions/attest-build-provenance\n  dependency-version: 4.1.0\n  dependency-type: direct:production\n  update-type: version-update:semver-major\n...\n\nSigned-off-by: dependabot[bot] <support@github.com>\nCo-authored-by: dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>",
          "timestamp": "2026-03-17T21:07:07+08:00",
          "tree_id": "225ea9badbe68c1830adc86a832337178f62a42a",
          "url": "https://github.com/heggria/Hermit/commit/e14e164d0ce64d2f0fcc293630962667f15899a3"
        },
        "date": 1773752872562,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/benchmark/test_bench_io.py::TestCLIStartupBenchmarks::test_hermit_help_startup",
            "value": 2.1226789729155677,
            "unit": "iter/sec",
            "range": "stddev: 0.010426578466603957",
            "extra": "mean: 471.10279639999817 msec\nrounds: 5"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_init",
            "value": 175.12143607317012,
            "unit": "iter/sec",
            "range": "stddev: 0.00009692022880016154",
            "extra": "mean: 5.710323204420131 msec\nrounds: 181"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_append_event",
            "value": 2498.255963071184,
            "unit": "iter/sec",
            "range": "stddev: 0.0001845249917376389",
            "extra": "mean: 400.2792407110554 usec\nrounds: 3095"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_list_tasks",
            "value": 3924.3169352036043,
            "unit": "iter/sec",
            "range": "stddev: 0.00003006619995422023",
            "extra": "mean: 254.821416443042 usec\nrounds: 4099"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_write",
            "value": 4941.620615910666,
            "unit": "iter/sec",
            "range": "stddev: 0.0001199398424914741",
            "extra": "mean: 202.362762689688 usec\nrounds: 6127"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_read",
            "value": 28920.08670458724,
            "unit": "iter/sec",
            "range": "stddev: 0.000004105747713558337",
            "extra": "mean: 34.57804294346677 usec\nrounds: 31134"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_update",
            "value": 3175.5427167855023,
            "unit": "iter/sec",
            "range": "stddev: 0.00021862885750037394",
            "extra": "mean: 314.9068015095911 usec\nrounds: 3577"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "email": "49699333+dependabot[bot]@users.noreply.github.com",
            "name": "dependabot[bot]",
            "username": "dependabot[bot]"
          },
          "committer": {
            "email": "noreply@github.com",
            "name": "GitHub",
            "username": "web-flow"
          },
          "distinct": true,
          "id": "bce5b42cc9a1310ab2f7b3f9d4f06d98ddfc5195",
          "message": "chore: bump actions/upload-artifact from 4.6.2 to 7.0.0 (#14)\n\nBumps [actions/upload-artifact](https://github.com/actions/upload-artifact) from 4.6.2 to 7.0.0.\n- [Release notes](https://github.com/actions/upload-artifact/releases)\n- [Commits](https://github.com/actions/upload-artifact/compare/ea165f8d65b6e75b540449e92b4886f43607fa02...bbbca2ddaa5d8feaa63e36b76fdaad77386f024f)\n\n---\nupdated-dependencies:\n- dependency-name: actions/upload-artifact\n  dependency-version: 7.0.0\n  dependency-type: direct:production\n  update-type: version-update:semver-major\n...\n\nSigned-off-by: dependabot[bot] <support@github.com>\nCo-authored-by: dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>\nCo-authored-by: Heggria <bshengtao@gmail.com>",
          "timestamp": "2026-03-17T21:11:49+08:00",
          "tree_id": "727eaebe17d979b2e2de9ef03fde058609f42d88",
          "url": "https://github.com/heggria/Hermit/commit/bce5b42cc9a1310ab2f7b3f9d4f06d98ddfc5195"
        },
        "date": 1773753156617,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/benchmark/test_bench_io.py::TestCLIStartupBenchmarks::test_hermit_help_startup",
            "value": 2.2983820999802886,
            "unit": "iter/sec",
            "range": "stddev: 0.0005324083914949726",
            "extra": "mean: 435.0886652000014 msec\nrounds: 5"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_init",
            "value": 178.54878497083521,
            "unit": "iter/sec",
            "range": "stddev: 0.000038731263305945175",
            "extra": "mean: 5.600710193370083 msec\nrounds: 181"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_append_event",
            "value": 2430.4133435209646,
            "unit": "iter/sec",
            "range": "stddev: 0.0005421094255308225",
            "extra": "mean: 411.4526455616351 usec\nrounds: 3143"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_list_tasks",
            "value": 4024.3273735106573,
            "unit": "iter/sec",
            "range": "stddev: 0.000011076691931726522",
            "extra": "mean: 248.48873046022626 usec\nrounds: 4107"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_write",
            "value": 5401.633090659101,
            "unit": "iter/sec",
            "range": "stddev: 0.00007748934038442985",
            "extra": "mean: 185.12919763640983 usec\nrounds: 6431"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_read",
            "value": 29305.992856756573,
            "unit": "iter/sec",
            "range": "stddev: 0.0000037636785510588627",
            "extra": "mean: 34.122713565373964 usec\nrounds: 30740"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_update",
            "value": 3288.1362797507204,
            "unit": "iter/sec",
            "range": "stddev: 0.0001664239483984334",
            "extra": "mean: 304.12364784217885 usec\nrounds: 3754"
          }
        ]
      },
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
          "id": "8c0eb96a304132cc1e6c01d3a8ec782813847f4a",
          "message": "iterate(demo-governed-hello): Add governed iteration marker file\" (#18)\n\n* feat: 将所有硬编码中文字符串提取到 i18n 系统\n\n将 ~250 个硬编码中文字符串从 22 个 Python 源文件提取到 locale 文件中，\n通过 tr() / tr_list() / tr_list_all_locales() 加载。\n\n主要变更：\n- 新增 tr_list_all_locales() 辅助函数，合并所有 locale 的 NLP 模式\n- 内存分类从中文改为英文内部常量，添加 _LEGACY_CATEGORY_MAP 向后兼容\n- NLP 模式（问候语、续接标记、控制意图等）改为从 locale 加载\n- LLM prompt、用户界面字符串全部通过 tr() 国际化\n- SQLite schema v7→v8 迁移，更新 category 列\n- 更新所有测试文件中的分类字符串引用\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: 修复 slack/telegram adapter 和 template_learner 的 pyright 错误\n\n- slack adapter: 添加 app 公开属性，标记 handler 函数为已使用\n- telegram adapter: 添加 application 公开属性\n- template_learner: 为 dataclass 字段添加显式类型注解\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* Revert \"fix: 修复 slack/telegram adapter 和 template_learner 的 pyright 错误\"\n\nThis reverts commit 608e35c8a1b3eb634605004be370a6849f2eafc7.\n\n* feat: i18n 提取、文档站优化、新增 slack/telegram adapter 及 TUI 基础\n\n- 提取 CLI/kernel/executor/approval 中剩余硬编码中文到 i18n 系统\n- 优化 docs landing page、mkdocs 配置、新增 blog/tags 支持\n- 新增 slack/telegram adapter 和 template_learner\n- 新增 CLI TUI 模块基础结构\n- 补充 e2e 测试（schedule、signed_proofs、uncertain_outcome 等）\n- CI 新增 pyright 类型检查 job\n- 更新 pyproject.toml 依赖和 uv.lock\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: 修复 TUI 模块的 pyright 类型错误\n\n- App 添加泛型参数 App[None]\n- action_quit / _on_key 改为 async 以匹配基类签名\n- 补充 **kwargs / event 参数类型注解\n- parts 列表添加 list[str] 类型标注\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: 补充 telegram adapter/hooks 测试覆盖并排除 TUI 模块 coverage\n\n- 新增 telegram adapter 单元测试（init、session_id、dedup、stop 等）\n- 新增 telegram hooks 单元测试（dispatch_result 各场景、register）\n- 新增 preflight 测试覆盖 telegram/slack adapter 配置检查\n- TUI 模块加入 coverage omit（UI widget 难以单元测试）\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: 补充 slack adapter 测试并标记不可单元测试的 async 方法\n\n- 新增 slack adapter/hooks 单元测试\n- adapter async 方法（start/on_message/sweep）添加 pragma: no cover\n  这些方法依赖 Telegram/Slack SDK 实际网络连接，需集成测试覆盖\n- TUI 入口代码块添加 pragma: no cover\n- coverage 配置新增 exclude_also 规则\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: 为 benchmark CI step 添加 github-token 以支持 PR 评论\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix(i18n): 修复 i18n 标记匹配时的大小写不一致问题\n\n- continuation.py: normalize_text 添加 .lower() 使英文 corrective markers 能正确匹配\n- actions.py: _is_accessibility_error 对 i18n marker 做 lowercase 后再比较\n\n* fix(ci): 添加 CI gate job 以匹配 branch protection 要求的 status check\n\nbranch protection 要求名为 \"CI\" 的 check，但之前没有任何 job\n产生该名称的 check run，导致 PR 永远 pending。\n\n* feat: governed self-evolution pipeline (Phase 1-3)\n\n- Add autonomous policy profile for non-interactive execution\n- Add --policy option to `hermit run` CLI command\n- Skip ingress routing for cli-oneshot sessions\n- Add iteration_summary readonly tool to core registry\n- Add hermit-iterate skill and script for spec-driven iteration\n- Add write_file tool usage guidance to iteration prompt\n- Verify rollback-capable receipts via write_file prestate capture\n- Add governed-self-evolution master spec and Phase 3/4 test specs\n\nGoverned execution chain verified end-to-end:\n  spec → parse → execute → receipt (rollback-capable) → proof-export → rollback\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* chore: add proof bundle from Phase 2 iteration\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* iterate(demo-governed-hello): Add governed iteration marker file\"\n\nTask: task_8c1a6eeffe95\nProof: .hermit-proof/demo-governed-hello.json\n\n* fix: resolve pyright type errors in runner policy_profile resolution\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* iterate(demo-governed-hello): Add governed iteration marker file\"\n\nTask: task_47196b115812\nProof: .hermit-proof/demo-governed-hello.json\n\n* feat: Phase 5 - human-readable proof formatter and governance logging\n\n- Add format_proof_summary() and format_receipt_table() to kernel verification\n- Add governed_tool_execution structured log for receipt-producing actions\n- Integrate formatter into hermit-iterate.sh for PR body and .md export\n- Add demo-add-proof-summary-util spec for showcase iteration\n- 4 new tests for proof formatter\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* iterate(demo-governed-hello): Add governed iteration marker file\"\n\nTask: task_f60e0232f028\nProof: .hermit-proof/demo-governed-hello.json\n\n* feat: Phase 5 showcase trace and governance logging\n\n- Add showcase-trace.md with curated execution trace for demo\n- Human-readable proof summary in .hermit-proof/*.md\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* docs: mark all governed-self-evolution phases as complete\n\nAll 5 phases and 7 acceptance criteria verified:\n- Phase 1: End-to-end hermit-iterate execution\n- Phase 2: Receipt and proof visibility\n- Phase 3: Rollback verification (file_restore)\n- Phase 4: PR close-loop with proof summary\n- Phase 5: Governance logging, human-readable formatter, showcase trace\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: update FakeRunner.handle() signature to accept run_opts kwarg\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* test: add coverage for autonomous policy profile and run_opts merging\n\nCover all branches of _evaluate_autonomous() in rules.py and the\nrun_opts merge path in runner.handle() to satisfy the 95% diff\ncoverage gate.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n---------\n\nCo-authored-by: Claude Opus 4.6 <noreply@anthropic.com>",
          "timestamp": "2026-03-17T22:00:17+08:00",
          "tree_id": "6d028731a35cb9697fd033fd0fa677f40f82528c",
          "url": "https://github.com/heggria/Hermit/commit/8c0eb96a304132cc1e6c01d3a8ec782813847f4a"
        },
        "date": 1773756086130,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/benchmark/test_bench_io.py::TestCLIStartupBenchmarks::test_hermit_help_startup",
            "value": 2.530349328672139,
            "unit": "iter/sec",
            "range": "stddev: 0.0021076513965115127",
            "extra": "mean: 395.2023495999953 msec\nrounds: 5"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_init",
            "value": 197.46157758482371,
            "unit": "iter/sec",
            "range": "stddev: 0.00006293093815996358",
            "extra": "mean: 5.064276363184779 msec\nrounds: 201"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_append_event",
            "value": 2285.5434643973404,
            "unit": "iter/sec",
            "range": "stddev: 0.00021804316758875766",
            "extra": "mean: 437.5326987114127 usec\nrounds: 3880"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_list_tasks",
            "value": 4199.174650010599,
            "unit": "iter/sec",
            "range": "stddev: 0.000005630214479645608",
            "extra": "mean: 238.14203583970388 usec\nrounds: 4269"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_write",
            "value": 6555.516223804672,
            "unit": "iter/sec",
            "range": "stddev: 0.00008125047291580818",
            "extra": "mean: 152.54328810426205 usec\nrounds: 11357"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_read",
            "value": 42961.79418619635,
            "unit": "iter/sec",
            "range": "stddev: 0.000005074895535732954",
            "extra": "mean: 23.276495289419284 usec\nrounds: 46173"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_update",
            "value": 5104.209874720299,
            "unit": "iter/sec",
            "range": "stddev: 0.0002250265830901162",
            "extra": "mean: 195.9167088627597 usec\nrounds: 6059"
          }
        ]
      },
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
          "id": "5aec4ab7ca4a9b4ca8773399a54fec569c577ccc",
          "message": "feat: kernel self-modification guard — governed self-surgery (#19)\n\n* test: cover _is_kernel_path OSError branch for diff coverage\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: detect kernel paths via segment fallback for subdirectory workspaces\n\nWhen Hermit is started from a subdirectory, workspace_root may not be\nthe repository root, causing kernel writes via relative paths like\n../src/hermit/kernel/... to bypass the self-modification guard.\n\nNow _is_kernel_path falls back to checking whether the resolved path\ncontains a src/hermit/kernel/ segment, so the guard fires regardless\nof the workspace root.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* test: cover workspace_root OSError fallback in _is_kernel_path\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* feat: add kernel self-modification guard to policy layer\n\nHermit now detects writes targeting src/hermit/kernel/ and escalates\nthem to approval_required with critical risk level. This guard ensures\nthat modifications to governed execution internals require explicit\napproval, receipt, preview, and evidence — even when the agent itself\nis doing the modifying.\n\nAdds _is_kernel_path() derivation helper and 11 tests covering\ndetection, guard behavior, reason codes, risk levels, and obligations.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* docs: add kernel-self-mod-guard spec\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n---------\n\nCo-authored-by: Claude Opus 4.6 <noreply@anthropic.com>",
          "timestamp": "2026-03-18T11:54:13+08:00",
          "tree_id": "f4d022044b951dac0bbb67e91668cd29265234c1",
          "url": "https://github.com/heggria/Hermit/commit/5aec4ab7ca4a9b4ca8773399a54fec569c577ccc"
        },
        "date": 1773806097634,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/benchmark/test_bench_io.py::TestCLIStartupBenchmarks::test_hermit_help_startup",
            "value": 2.2598253218663023,
            "unit": "iter/sec",
            "range": "stddev: 0.0012972276487612866",
            "extra": "mean: 442.5120783999972 msec\nrounds: 5"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_init",
            "value": 177.00728213827034,
            "unit": "iter/sec",
            "range": "stddev: 0.0000504532512064052",
            "extra": "mean: 5.649485082872714 msec\nrounds: 181"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_append_event",
            "value": 2530.6577610104387,
            "unit": "iter/sec",
            "range": "stddev: 0.0002145101314257898",
            "extra": "mean: 395.15418299814706 usec\nrounds: 3082"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_list_tasks",
            "value": 4014.636731377875,
            "unit": "iter/sec",
            "range": "stddev: 0.000011638987697083158",
            "extra": "mean: 249.0885394895461 usec\nrounds: 4115"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_write",
            "value": 5021.436209073848,
            "unit": "iter/sec",
            "range": "stddev: 0.00009149771664930325",
            "extra": "mean: 199.14621203252122 usec\nrounds: 6150"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_read",
            "value": 28896.467937036105,
            "unit": "iter/sec",
            "range": "stddev: 0.0000036502967903646223",
            "extra": "mean: 34.606305593436126 usec\nrounds: 30750"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_update",
            "value": 3274.6442102005476,
            "unit": "iter/sec",
            "range": "stddev: 0.00014866176524146228",
            "extra": "mean: 305.3766869954881 usec\nrounds: 3591"
          }
        ]
      },
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
          "id": "9929e8dec9f0fe8a92a33703ab06b2484cd2520d",
          "message": "iterate(adversarial-governance): Adversarial Governance Assurance Report (#20)\n\n* feat: 将所有硬编码中文字符串提取到 i18n 系统\n\n将 ~250 个硬编码中文字符串从 22 个 Python 源文件提取到 locale 文件中，\n通过 tr() / tr_list() / tr_list_all_locales() 加载。\n\n主要变更：\n- 新增 tr_list_all_locales() 辅助函数，合并所有 locale 的 NLP 模式\n- 内存分类从中文改为英文内部常量，添加 _LEGACY_CATEGORY_MAP 向后兼容\n- NLP 模式（问候语、续接标记、控制意图等）改为从 locale 加载\n- LLM prompt、用户界面字符串全部通过 tr() 国际化\n- SQLite schema v7→v8 迁移，更新 category 列\n- 更新所有测试文件中的分类字符串引用\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: 修复 slack/telegram adapter 和 template_learner 的 pyright 错误\n\n- slack adapter: 添加 app 公开属性，标记 handler 函数为已使用\n- telegram adapter: 添加 application 公开属性\n- template_learner: 为 dataclass 字段添加显式类型注解\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* Revert \"fix: 修复 slack/telegram adapter 和 template_learner 的 pyright 错误\"\n\nThis reverts commit 608e35c8a1b3eb634605004be370a6849f2eafc7.\n\n* feat: i18n 提取、文档站优化、新增 slack/telegram adapter 及 TUI 基础\n\n- 提取 CLI/kernel/executor/approval 中剩余硬编码中文到 i18n 系统\n- 优化 docs landing page、mkdocs 配置、新增 blog/tags 支持\n- 新增 slack/telegram adapter 和 template_learner\n- 新增 CLI TUI 模块基础结构\n- 补充 e2e 测试（schedule、signed_proofs、uncertain_outcome 等）\n- CI 新增 pyright 类型检查 job\n- 更新 pyproject.toml 依赖和 uv.lock\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: 修复 TUI 模块的 pyright 类型错误\n\n- App 添加泛型参数 App[None]\n- action_quit / _on_key 改为 async 以匹配基类签名\n- 补充 **kwargs / event 参数类型注解\n- parts 列表添加 list[str] 类型标注\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: 补充 telegram adapter/hooks 测试覆盖并排除 TUI 模块 coverage\n\n- 新增 telegram adapter 单元测试（init、session_id、dedup、stop 等）\n- 新增 telegram hooks 单元测试（dispatch_result 各场景、register）\n- 新增 preflight 测试覆盖 telegram/slack adapter 配置检查\n- TUI 模块加入 coverage omit（UI widget 难以单元测试）\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: 补充 slack adapter 测试并标记不可单元测试的 async 方法\n\n- 新增 slack adapter/hooks 单元测试\n- adapter async 方法（start/on_message/sweep）添加 pragma: no cover\n  这些方法依赖 Telegram/Slack SDK 实际网络连接，需集成测试覆盖\n- TUI 入口代码块添加 pragma: no cover\n- coverage 配置新增 exclude_also 规则\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: 为 benchmark CI step 添加 github-token 以支持 PR 评论\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix(i18n): 修复 i18n 标记匹配时的大小写不一致问题\n\n- continuation.py: normalize_text 添加 .lower() 使英文 corrective markers 能正确匹配\n- actions.py: _is_accessibility_error 对 i18n marker 做 lowercase 后再比较\n\n* fix(ci): 添加 CI gate job 以匹配 branch protection 要求的 status check\n\nbranch protection 要求名为 \"CI\" 的 check，但之前没有任何 job\n产生该名称的 check run，导致 PR 永远 pending。\n\n* feat: governed self-evolution pipeline (Phase 1-3)\n\n- Add autonomous policy profile for non-interactive execution\n- Add --policy option to `hermit run` CLI command\n- Skip ingress routing for cli-oneshot sessions\n- Add iteration_summary readonly tool to core registry\n- Add hermit-iterate skill and script for spec-driven iteration\n- Add write_file tool usage guidance to iteration prompt\n- Verify rollback-capable receipts via write_file prestate capture\n- Add governed-self-evolution master spec and Phase 3/4 test specs\n\nGoverned execution chain verified end-to-end:\n  spec → parse → execute → receipt (rollback-capable) → proof-export → rollback\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* chore: add proof bundle from Phase 2 iteration\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* iterate(demo-governed-hello): Add governed iteration marker file\"\n\nTask: task_8c1a6eeffe95\nProof: .hermit-proof/demo-governed-hello.json\n\n* fix: resolve pyright type errors in runner policy_profile resolution\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* iterate(demo-governed-hello): Add governed iteration marker file\"\n\nTask: task_47196b115812\nProof: .hermit-proof/demo-governed-hello.json\n\n* feat: Phase 5 - human-readable proof formatter and governance logging\n\n- Add format_proof_summary() and format_receipt_table() to kernel verification\n- Add governed_tool_execution structured log for receipt-producing actions\n- Integrate formatter into hermit-iterate.sh for PR body and .md export\n- Add demo-add-proof-summary-util spec for showcase iteration\n- 4 new tests for proof formatter\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* iterate(demo-governed-hello): Add governed iteration marker file\"\n\nTask: task_f60e0232f028\nProof: .hermit-proof/demo-governed-hello.json\n\n* feat: Phase 5 showcase trace and governance logging\n\n- Add showcase-trace.md with curated execution trace for demo\n- Human-readable proof summary in .hermit-proof/*.md\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* docs: mark all governed-self-evolution phases as complete\n\nAll 5 phases and 7 acceptance criteria verified:\n- Phase 1: End-to-end hermit-iterate execution\n- Phase 2: Receipt and proof visibility\n- Phase 3: Rollback verification (file_restore)\n- Phase 4: PR close-loop with proof summary\n- Phase 5: Governance logging, human-readable formatter, showcase trace\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: update FakeRunner.handle() signature to accept run_opts kwarg\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* test: add coverage for autonomous policy profile and run_opts merging\n\nCover all branches of _evaluate_autonomous() in rules.py and the\nrun_opts merge path in runner.handle() to satisfy the 95% diff\ncoverage gate.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* feat(governance): add adversarial governance assurance report formatter\n\nAdd governance_report.py with extract_governance_events() and\nformat_governance_assurance_report() — pure functions that analyze proof\nbundles and generate human-readable governance assurance reports.\n\nThe extractor classifies governance events from receipt bundles (denied /\nsucceeded), blocked authorization plans (policy_denied gaps), and\nabandoned execution contracts. The formatter renders an executive summary,\nboundary enforcement table, authorized executions, chain integrity, and\na verdict (GOVERNANCE ENFORCED / CLEAN EXECUTION / INTEGRITY COMPROMISED).\n\nIncludes spec file, 9 tests, and proof bundle from a governed iteration\nrun where the kernel successfully denied a sudo command execution.\n\nTask: task_3b6cd397e5e5\nProof: .hermit-proof/adversarial-governance.json\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n---------\n\nCo-authored-by: Claude Opus 4.6 <noreply@anthropic.com>",
          "timestamp": "2026-03-18T12:22:33+08:00",
          "tree_id": "55ad25e3feab474095421cd0180bb2cbae67e3e5",
          "url": "https://github.com/heggria/Hermit/commit/9929e8dec9f0fe8a92a33703ab06b2484cd2520d"
        },
        "date": 1773807797548,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/benchmark/test_bench_io.py::TestCLIStartupBenchmarks::test_hermit_help_startup",
            "value": 2.118476301529067,
            "unit": "iter/sec",
            "range": "stddev: 0.018571017959881777",
            "extra": "mean: 472.0373785999982 msec\nrounds: 5"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_init",
            "value": 177.68627719878137,
            "unit": "iter/sec",
            "range": "stddev: 0.00019271580908930352",
            "extra": "mean: 5.627896626374129 msec\nrounds: 182"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_append_event",
            "value": 2426.509219974521,
            "unit": "iter/sec",
            "range": "stddev: 0.00026034142433142685",
            "extra": "mean: 412.1146508606715 usec\nrounds: 3079"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_list_tasks",
            "value": 4027.8760441286895,
            "unit": "iter/sec",
            "range": "stddev: 0.000010020792451021831",
            "extra": "mean: 248.26980499006893 usec\nrounds: 4128"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_write",
            "value": 5356.512686568984,
            "unit": "iter/sec",
            "range": "stddev: 0.00007171186948361338",
            "extra": "mean: 186.68862719347572 usec\nrounds: 6097"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_read",
            "value": 28597.722624965045,
            "unit": "iter/sec",
            "range": "stddev: 0.000003470392719877473",
            "extra": "mean: 34.96781939996253 usec\nrounds: 30598"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_update",
            "value": 3389.599768042322,
            "unit": "iter/sec",
            "range": "stddev: 0.00012125163290520586",
            "extra": "mean: 295.0200815530367 usec\nrounds: 3838"
          }
        ]
      },
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
          "id": "3d8fbfc478cf6c8fa1a0517e366ce9e953da06ac",
          "message": "iterate(governed-subagent-identity): 给 subagent 安装 kernel 身份和治理基线 (#21)\n\n* feat: governed subagent identity — kernel 身份和治理基线\n\n给 subagent 安装 kernel 可见的一等身份：\n- SubagentSpec 新增 governed 和 policy_profile 字段\n- governed=True 的 delegation 工具使用 delegate_execution action_class\n- 新增 delegate_execution 的 policy rule（allow_with_receipt）和 action contract\n- governed subagent 注册 PrincipalRecord 并发射 ledger 生命周期事件\n- 现有 subagent 默认 governed=False，行为完全不变\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* feat(kernel): mid-execution steering spec and tests\n\nSteering directive lifecycle for in-flight task adjustments.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* feat(kernel): auto-park and task prioritizer\n\nAuto-park unfocused tasks and prioritize based on risk, age, and\nblocked status for never-idle execution.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* feat(plugins): overnight report, patrol engine, and trigger hooks\n\nBuiltin hook plugins for overnight reporting, patrol checks, and\nevent-driven trigger execution.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* feat: kernel self-mod guard, never-idle specs, and CI dep-freshness\n\nAdd kernel self-modification guard spec/tests, never-idle task specs,\nhelper scripts, and dependency freshness CI workflow.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: use public store.append_event instead of private _append_event_tx\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* test: extend subagent identity tests with governance coverage\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* feat(signals): add kernel signals module with evidence signals and steering directives\n\nImplements the full signals subsystem:\n- EvidenceSignal and SteeringDirective data models\n- SignalStoreMixin with evidence_signals table (integrated into KernelStore)\n- SignalProtocol for signal lifecycle (emit, consume, suppress)\n- SteeringProtocol for directive lifecycle (issue, acknowledge, apply, reject, supersede)\n- SignalConsumer for actionable signal → task creation\n- /steer prefix detection in TaskController.append_note\n- Auto-apply acknowledged steerings on TaskController.finalize_result\n- Active steerings rendering in ProviderInputCompiler context\n- CLI commands: hermit task steer, hermit task steerings\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: resolve import sorting lint failures in test files\n\nReorder imports to satisfy Ruff I001 (isort) rules in test_steering,\ntest_self_iteration_loop, and test_steering_e2e.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* feat: competition module, typecheck fixes, and test reliability improvements\n\n- Add competition execution module (models, evaluator, service, store mixin)\n  with CompetitionStoreMixin integrated into KernelStore for candidate-based\n  task execution\n- Fix Settings._env_file=None handling to properly skip env file reading\n  when explicitly set, preventing profile/config.toml header values from\n  overriding test kwargs\n- Fix pyright type error in logging setup.py with TextIO cast\n- Fix test_custom_headers_requires_colon_separator to use isolated tmp_path\n  base_dir, preventing ~/.hermit/config.toml from leaking into test\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* chore: exclude .claude/worktrees/ from ruff linting\n\nWorktree directories may contain in-progress code that should not\nbe linted alongside the main codebase.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: avoid eager stderr capture in structlog factory under pytest-xdist\n\nOnly set PrintLoggerFactory with an explicit file when a custom stream\nis provided. When using the default (sys.stderr), let structlog's\nbuilt-in factory resolve it lazily, preventing \"I/O operation on closed\nfile\" errors when xdist workers close stderr early.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* test: add coverage for overnight, patrol, trigger, and signal modules\n\nRegister _commands_overnight in CLI main.py and add comprehensive tests\nfor patrol tools/hooks/checks, trigger hooks/followup, overnight\ncommand/dashboard, and evidence signals to fix CI coverage gate (82% → 95%+).\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n---------\n\nCo-authored-by: Claude Opus 4.6 <noreply@anthropic.com>",
          "timestamp": "2026-03-18T14:07:49+08:00",
          "tree_id": "c8c40becd46c1ecb34d652ff78a64802d9f69940",
          "url": "https://github.com/heggria/Hermit/commit/3d8fbfc478cf6c8fa1a0517e366ce9e953da06ac"
        },
        "date": 1773814111849,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/benchmark/test_bench_io.py::TestCLIStartupBenchmarks::test_hermit_help_startup",
            "value": 2.150285843797533,
            "unit": "iter/sec",
            "range": "stddev: 0.01128250442869428",
            "extra": "mean: 465.0544498000045 msec\nrounds: 5"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_init",
            "value": 174.6339630203609,
            "unit": "iter/sec",
            "range": "stddev: 0.00005634615621737279",
            "extra": "mean: 5.726262994348975 msec\nrounds: 177"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_append_event",
            "value": 2390.633861305951,
            "unit": "iter/sec",
            "range": "stddev: 0.00018750039587026095",
            "extra": "mean: 418.2991030896391 usec\nrounds: 2978"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_list_tasks",
            "value": 3899.453937079285,
            "unit": "iter/sec",
            "range": "stddev: 0.000012399924831274132",
            "extra": "mean: 256.44616301045636 usec\nrounds: 4012"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_write",
            "value": 4897.552179419792,
            "unit": "iter/sec",
            "range": "stddev: 0.00008376303628909605",
            "extra": "mean: 204.18363365318322 usec\nrounds: 5836"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_read",
            "value": 28798.160729365274,
            "unit": "iter/sec",
            "range": "stddev: 0.000003902128108684698",
            "extra": "mean: 34.72443984869865 usec\nrounds: 30656"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_update",
            "value": 3271.282100019411,
            "unit": "iter/sec",
            "range": "stddev: 0.00011786021550391884",
            "extra": "mean: 305.6905425533513 usec\nrounds: 3666"
          }
        ]
      },
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
          "id": "e2fdfbc5223fd304d9b2cec6b3dbcad3a36c57b4",
          "message": "feat: parallel kernel innovation — 8 features, 108 tests, 6 dimensions (#23)\n\n* feat: governed subagent identity — kernel 身份和治理基线\n\n给 subagent 安装 kernel 可见的一等身份：\n- SubagentSpec 新增 governed 和 policy_profile 字段\n- governed=True 的 delegation 工具使用 delegate_execution action_class\n- 新增 delegate_execution 的 policy rule（allow_with_receipt）和 action contract\n- governed subagent 注册 PrincipalRecord 并发射 ledger 生命周期事件\n- 现有 subagent 默认 governed=False，行为完全不变\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* feat(kernel): mid-execution steering spec and tests\n\nSteering directive lifecycle for in-flight task adjustments.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* feat(kernel): auto-park and task prioritizer\n\nAuto-park unfocused tasks and prioritize based on risk, age, and\nblocked status for never-idle execution.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* feat(plugins): overnight report, patrol engine, and trigger hooks\n\nBuiltin hook plugins for overnight reporting, patrol checks, and\nevent-driven trigger execution.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* feat: kernel self-mod guard, never-idle specs, and CI dep-freshness\n\nAdd kernel self-modification guard spec/tests, never-idle task specs,\nhelper scripts, and dependency freshness CI workflow.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: use public store.append_event instead of private _append_event_tx\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* test: extend subagent identity tests with governance coverage\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* feat(signals): add kernel signals module with evidence signals and steering directives\n\nImplements the full signals subsystem:\n- EvidenceSignal and SteeringDirective data models\n- SignalStoreMixin with evidence_signals table (integrated into KernelStore)\n- SignalProtocol for signal lifecycle (emit, consume, suppress)\n- SteeringProtocol for directive lifecycle (issue, acknowledge, apply, reject, supersede)\n- SignalConsumer for actionable signal → task creation\n- /steer prefix detection in TaskController.append_note\n- Auto-apply acknowledged steerings on TaskController.finalize_result\n- Active steerings rendering in ProviderInputCompiler context\n- CLI commands: hermit task steer, hermit task steerings\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: resolve import sorting lint failures in test files\n\nReorder imports to satisfy Ruff I001 (isort) rules in test_steering,\ntest_self_iteration_loop, and test_steering_e2e.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* feat: competition module, typecheck fixes, and test reliability improvements\n\n- Add competition execution module (models, evaluator, service, store mixin)\n  with CompetitionStoreMixin integrated into KernelStore for candidate-based\n  task execution\n- Fix Settings._env_file=None handling to properly skip env file reading\n  when explicitly set, preventing profile/config.toml header values from\n  overriding test kwargs\n- Fix pyright type error in logging setup.py with TextIO cast\n- Fix test_custom_headers_requires_colon_separator to use isolated tmp_path\n  base_dir, preventing ~/.hermit/config.toml from leaking into test\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* chore: exclude .claude/worktrees/ from ruff linting\n\nWorktree directories may contain in-progress code that should not\nbe linted alongside the main codebase.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: avoid eager stderr capture in structlog factory under pytest-xdist\n\nOnly set PrintLoggerFactory with an explicit file when a custom stream\nis provided. When using the default (sys.stderr), let structlog's\nbuilt-in factory resolve it lazily, preventing \"I/O operation on closed\nfile\" errors when xdist workers close stderr early.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* feat: parallel kernel innovation — 8 specs, 108 tests, 6 dimensions\n\nImplemented 8 self-contained kernel features in parallel via isolated\nworktrees, covering ~50 person-days of innovation across 6 dimensions:\n\n1. Contract Template Learning — learn from successful reconciliations\n   to auto-suggest execution contracts (31 tests)\n2. Governed Task Delegation — parent/child task delegation with\n   receipted authority transfer (9 tests)\n3. A2A Governed Endpoint — Agent-to-Agent protocol with governed\n   task handling and capability cards (10 tests)\n4. Autonomous Patrol-to-Fix — patrol detects issues and creates\n   governed remediation tasks (18 tests)\n5. Dynamic Trust Scoring — trust scores from execution history\n   with advisory risk adjustments (10 tests)\n6. Proof Anchoring — external proof timestamping with chained\n   local logs and git notes (9 tests)\n7. Execution Analytics — governance metrics, trends, and risk\n   action analysis (9 tests)\n8. Recursive Rollback — transitive dependency tracking with\n   leaf-first rollback plans (12 tests)\n\nAlso includes zero-downtime hot reload for hermit serve:\n- hermit-watch.py sends SIGHUP instead of kill+respawn\n- WebhookServer.swap_runner() for thread-safe runner replacement\n- _serve_loop persists webhook HTTP server across reload cycles\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: resolve pyright typecheck errors across new kernel modules\n\nAdd typed default_factory generics (list[str], dict[str, Any], etc.) to\ndataclass fields and suppress protected-access warnings in template_learner.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: resolve remaining pyright errors in a2a module\n\nAdd typed default_factory generics and suppress protected-access\nwarnings for cross-module webhook server integration.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* test: add 118 coverage tests for new kernel and plugin modules\n\nCover missing lines in signals, steering, anchor methods, dependency\ntracker, trust scoring, delegation, patrol checks/hooks/tools,\ntrigger engine/hooks, webhook hooks, a2a hooks, overnight dashboard,\novernight commands, and serve loop.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* feat: add 20 frontier kernel innovation specs across 7 dimensions\n\nSpecs cover: MCP elicitation gateway, A2A task federation, agent card\npublisher, cross-step taint tracking, step-up authorization,\ncross-step policy patterns, Merkle proof trees, receipt replay\nverifier, episodic memory index, memory lineage graph, memory decay\ngovernance, governed browser sessions, dual-ledger replanning, budget\nenforcement policy, failure pattern recognition, tool sequence\noptimizer, governed prompt evolution, OTel trace export, live\nexecution streaming, and anomaly detection signals.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* ci: trigger CI re-run\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n---------\n\nCo-authored-by: Claude Opus 4.6 <noreply@anthropic.com>",
          "timestamp": "2026-03-18T14:31:35+08:00",
          "tree_id": "13251747c9c5964fb0074f98cb5e993f5ea3056b",
          "url": "https://github.com/heggria/Hermit/commit/e2fdfbc5223fd304d9b2cec6b3dbcad3a36c57b4"
        },
        "date": 1773815539031,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/benchmark/test_bench_io.py::TestCLIStartupBenchmarks::test_hermit_help_startup",
            "value": 2.127711573928137,
            "unit": "iter/sec",
            "range": "stddev: 0.0142964507725234",
            "extra": "mean: 469.9885135999992 msec\nrounds: 5"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_init",
            "value": 165.02365325521674,
            "unit": "iter/sec",
            "range": "stddev: 0.0003410972903406682",
            "extra": "mean: 6.0597373786983955 msec\nrounds: 169"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_append_event",
            "value": 3167.8511852336273,
            "unit": "iter/sec",
            "range": "stddev: 0.00009874210170171165",
            "extra": "mean: 315.67139411766607 usec\nrounds: 3740"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_list_tasks",
            "value": 3775.615459412302,
            "unit": "iter/sec",
            "range": "stddev: 0.000008422055922994425",
            "extra": "mean: 264.85748105175315 usec\nrounds: 3879"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_write",
            "value": 5572.829599514801,
            "unit": "iter/sec",
            "range": "stddev: 0.000020282023187136883",
            "extra": "mean: 179.44205580717292 usec\nrounds: 6845"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_read",
            "value": 30593.718839355173,
            "unit": "iter/sec",
            "range": "stddev: 0.000002360349907652544",
            "extra": "mean: 32.68644800100664 usec\nrounds: 32241"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_update",
            "value": 3652.309438874578,
            "unit": "iter/sec",
            "range": "stddev: 0.00005640365546189135",
            "extra": "mean: 273.79936359065454 usec\nrounds: 3977"
          }
        ]
      },
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
          "id": "77e01ca5eecfada0f9fed7076ed0d776e6cde793",
          "message": "feat(kernel): competition module, template learning, and adversarial governance (#22)\n\n* feat: 将所有硬编码中文字符串提取到 i18n 系统\n\n将 ~250 个硬编码中文字符串从 22 个 Python 源文件提取到 locale 文件中，\n通过 tr() / tr_list() / tr_list_all_locales() 加载。\n\n主要变更：\n- 新增 tr_list_all_locales() 辅助函数，合并所有 locale 的 NLP 模式\n- 内存分类从中文改为英文内部常量，添加 _LEGACY_CATEGORY_MAP 向后兼容\n- NLP 模式（问候语、续接标记、控制意图等）改为从 locale 加载\n- LLM prompt、用户界面字符串全部通过 tr() 国际化\n- SQLite schema v7→v8 迁移，更新 category 列\n- 更新所有测试文件中的分类字符串引用\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: 修复 slack/telegram adapter 和 template_learner 的 pyright 错误\n\n- slack adapter: 添加 app 公开属性，标记 handler 函数为已使用\n- telegram adapter: 添加 application 公开属性\n- template_learner: 为 dataclass 字段添加显式类型注解\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* Revert \"fix: 修复 slack/telegram adapter 和 template_learner 的 pyright 错误\"\n\nThis reverts commit 608e35c8a1b3eb634605004be370a6849f2eafc7.\n\n* feat: i18n 提取、文档站优化、新增 slack/telegram adapter 及 TUI 基础\n\n- 提取 CLI/kernel/executor/approval 中剩余硬编码中文到 i18n 系统\n- 优化 docs landing page、mkdocs 配置、新增 blog/tags 支持\n- 新增 slack/telegram adapter 和 template_learner\n- 新增 CLI TUI 模块基础结构\n- 补充 e2e 测试（schedule、signed_proofs、uncertain_outcome 等）\n- CI 新增 pyright 类型检查 job\n- 更新 pyproject.toml 依赖和 uv.lock\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: 修复 TUI 模块的 pyright 类型错误\n\n- App 添加泛型参数 App[None]\n- action_quit / _on_key 改为 async 以匹配基类签名\n- 补充 **kwargs / event 参数类型注解\n- parts 列表添加 list[str] 类型标注\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: 补充 telegram adapter/hooks 测试覆盖并排除 TUI 模块 coverage\n\n- 新增 telegram adapter 单元测试（init、session_id、dedup、stop 等）\n- 新增 telegram hooks 单元测试（dispatch_result 各场景、register）\n- 新增 preflight 测试覆盖 telegram/slack adapter 配置检查\n- TUI 模块加入 coverage omit（UI widget 难以单元测试）\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: 补充 slack adapter 测试并标记不可单元测试的 async 方法\n\n- 新增 slack adapter/hooks 单元测试\n- adapter async 方法（start/on_message/sweep）添加 pragma: no cover\n  这些方法依赖 Telegram/Slack SDK 实际网络连接，需集成测试覆盖\n- TUI 入口代码块添加 pragma: no cover\n- coverage 配置新增 exclude_also 规则\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: 为 benchmark CI step 添加 github-token 以支持 PR 评论\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix(i18n): 修复 i18n 标记匹配时的大小写不一致问题\n\n- continuation.py: normalize_text 添加 .lower() 使英文 corrective markers 能正确匹配\n- actions.py: _is_accessibility_error 对 i18n marker 做 lowercase 后再比较\n\n* fix(ci): 添加 CI gate job 以匹配 branch protection 要求的 status check\n\nbranch protection 要求名为 \"CI\" 的 check，但之前没有任何 job\n产生该名称的 check run，导致 PR 永远 pending。\n\n* feat: governed self-evolution pipeline (Phase 1-3)\n\n- Add autonomous policy profile for non-interactive execution\n- Add --policy option to `hermit run` CLI command\n- Skip ingress routing for cli-oneshot sessions\n- Add iteration_summary readonly tool to core registry\n- Add hermit-iterate skill and script for spec-driven iteration\n- Add write_file tool usage guidance to iteration prompt\n- Verify rollback-capable receipts via write_file prestate capture\n- Add governed-self-evolution master spec and Phase 3/4 test specs\n\nGoverned execution chain verified end-to-end:\n  spec → parse → execute → receipt (rollback-capable) → proof-export → rollback\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* chore: add proof bundle from Phase 2 iteration\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* iterate(demo-governed-hello): Add governed iteration marker file\"\n\nTask: task_8c1a6eeffe95\nProof: .hermit-proof/demo-governed-hello.json\n\n* fix: resolve pyright type errors in runner policy_profile resolution\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* iterate(demo-governed-hello): Add governed iteration marker file\"\n\nTask: task_47196b115812\nProof: .hermit-proof/demo-governed-hello.json\n\n* feat: Phase 5 - human-readable proof formatter and governance logging\n\n- Add format_proof_summary() and format_receipt_table() to kernel verification\n- Add governed_tool_execution structured log for receipt-producing actions\n- Integrate formatter into hermit-iterate.sh for PR body and .md export\n- Add demo-add-proof-summary-util spec for showcase iteration\n- 4 new tests for proof formatter\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* iterate(demo-governed-hello): Add governed iteration marker file\"\n\nTask: task_f60e0232f028\nProof: .hermit-proof/demo-governed-hello.json\n\n* feat: Phase 5 showcase trace and governance logging\n\n- Add showcase-trace.md with curated execution trace for demo\n- Human-readable proof summary in .hermit-proof/*.md\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* docs: mark all governed-self-evolution phases as complete\n\nAll 5 phases and 7 acceptance criteria verified:\n- Phase 1: End-to-end hermit-iterate execution\n- Phase 2: Receipt and proof visibility\n- Phase 3: Rollback verification (file_restore)\n- Phase 4: PR close-loop with proof summary\n- Phase 5: Governance logging, human-readable formatter, showcase trace\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: update FakeRunner.handle() signature to accept run_opts kwarg\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* test: add coverage for autonomous policy profile and run_opts merging\n\nCover all branches of _evaluate_autonomous() in rules.py and the\nrun_opts merge path in runner.handle() to satisfy the 95% diff\ncoverage gate.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* feat(governance): add adversarial governance assurance report formatter\n\nAdd governance_report.py with extract_governance_events() and\nformat_governance_assurance_report() — pure functions that analyze proof\nbundles and generate human-readable governance assurance reports.\n\nThe extractor classifies governance events from receipt bundles (denied /\nsucceeded), blocked authorization plans (policy_denied gaps), and\nabandoned execution contracts. The formatter renders an executive summary,\nboundary enforcement table, authorized executions, chain integrity, and\na verdict (GOVERNANCE ENFORCED / CLEAN EXECUTION / INTEGRITY COMPROMISED).\n\nIncludes spec file, 9 tests, and proof bundle from a governed iteration\nrun where the kernel successfully denied a sudo command execution.\n\nTask: task_3b6cd397e5e5\nProof: .hermit-proof/adversarial-governance.json\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* feat(policy): fix template suggestion timing with PolicyEvidenceEnricher\n\nPolicySuggestion was computed in synthesize_default() (after policy\nevaluation) but consumed by _apply_policy_suggestion() in evaluate_rules()\n(during policy evaluation), making template-based approval relaxation\ndead code.\n\nFix: introduce PolicyEvidenceEnricher that injects template and pattern\nevidence into action_request.context before policy evaluation. Also wire\nTaskPatternLearner.learn_from_completed_task() into the reconciliation\ncompletion path and add _apply_task_pattern() consumer in rules.py to\nclose the task-pattern loop end-to-end.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* feat(kernel): contract template learner and task pattern learner\n\nAdd template outcome tracking (invocation/success/failure counts,\nauto-invalidation), policy suggestion computation from template\nconfidence, and task-level pattern learning from completed multi-step\ntasks.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* feat(kernel): add execution competition module\n\nCriteria evaluation, competition service, and store for comparing\ncompeting execution approaches.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* merge: resolve conflicts with main and preserve PR changes\n\nMerge main into iterate/adversarial-governance, resolving conflicts\nin competition module, rules.py, and template_learner.py. Preserves\nPolicyEvidenceEnricher, template outcome tracking, task pattern\nlearning, and policy suggestion functions. Adds delegate_execution\nrule from main and structured_assertion support in update_memory_record.\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: align template learner merge with tests — tracking fields, gradual degradation, policy suggestions\n\nCompletes the merge resolution from main into iterate/adversarial-governance:\n- Add PolicySuggestion import and compute_policy_suggestion method\n- Add record_template_outcome for tracking template use success/failure\n- Implement gradual degradation (invocation_count >= 5 + success_rate < 0.3)\n- Set initial invocation_count=1, success_count=1 on template creation\n- Add tracking fields (invocation_count, failure_count, success_rate, last_failure_at) to structured assertion\n- Update reinforcement path to increment tracking counters\n- Update tests to match gradual degradation semantics\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* fix: restore competition module and fix CI failures from merge\n\n- Restore competition module files (models, evaluator, service, store,\n  criteria, workspace) from original commit 4752fbf — the merge kept\n  truncated scaffolding from main instead of the full implementations\n- Add create_worktree/remove_worktree methods to GitWorktreeInspector\n  (needed by CompetitionWorkspaceManager)\n- Bump schema version 8 → 10 and add competitions/competition_candidates\n  to _KNOWN_KERNEL_TABLES\n- Forward webhook payload.policy_profile into ingress_metadata\n- Update schema version assertions in tests (8 → 10)\n- Add workspace_ref and scope_kind fields to ContractTemplate\n- Add workspace-scoped template learning and cross-workspace promotion\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n* test: add coverage for competition module, workspace, criteria, and template learner\n\n68 new tests bringing diff coverage from 82% toward 95%:\n- Competition workspace: create, merge, cleanup, list_orphans (14 tests)\n- Competition criteria: TestPass, LintClean, TypeCheck edge cases (9 tests)\n- Template learner: workspace-scoped learning, cross-workspace promotion,\n  record_template_outcome, degradation edge cases (18 tests)\n- Competition service: spawn errors, timeout policies, promote/cancel\n  edge cases, orphan cleanup (20 tests)\n- Competition store: status update edge cases, score update (7 tests)\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>\n\n---------\n\nCo-authored-by: Claude Opus 4.6 <noreply@anthropic.com>",
          "timestamp": "2026-03-18T15:25:45+08:00",
          "tree_id": "86d1d74d750d0996ed9e7d9ef6f633bd3f12e06c",
          "url": "https://github.com/heggria/Hermit/commit/77e01ca5eecfada0f9fed7076ed0d776e6cde793"
        },
        "date": 1773818789171,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/benchmark/test_bench_io.py::TestCLIStartupBenchmarks::test_hermit_help_startup",
            "value": 2.113810861653501,
            "unit": "iter/sec",
            "range": "stddev: 0.007639105719499514",
            "extra": "mean: 473.0792230000006 msec\nrounds: 5"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_init",
            "value": 172.5460570768033,
            "unit": "iter/sec",
            "range": "stddev: 0.00042002889714971636",
            "extra": "mean: 5.7955540505621785 msec\nrounds: 178"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_append_event",
            "value": 2454.8772728390704,
            "unit": "iter/sec",
            "range": "stddev: 0.00019830313185783283",
            "extra": "mean: 407.35233938741794 usec\nrounds: 2808"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_list_tasks",
            "value": 3983.291664179416,
            "unit": "iter/sec",
            "range": "stddev: 0.000009146632292647765",
            "extra": "mean: 251.04865129327817 usec\nrounds: 4098"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_write",
            "value": 5034.827663182376,
            "unit": "iter/sec",
            "range": "stddev: 0.00009822129371216031",
            "extra": "mean: 198.616530077601 usec\nrounds: 6184"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_read",
            "value": 29169.9384199449,
            "unit": "iter/sec",
            "range": "stddev: 0.00000392712085226778",
            "extra": "mean: 34.28186873772252 usec\nrounds: 30999"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_update",
            "value": 3225.382763313235,
            "unit": "iter/sec",
            "range": "stddev: 0.00014258583277477829",
            "extra": "mean: 310.04072179413595 usec\nrounds: 3634"
          }
        ]
      },
      {
        "commit": {
          "author": {
            "email": "49699333+dependabot[bot]@users.noreply.github.com",
            "name": "dependabot[bot]",
            "username": "dependabot[bot]"
          },
          "committer": {
            "email": "noreply@github.com",
            "name": "GitHub",
            "username": "web-flow"
          },
          "distinct": true,
          "id": "61471de1f23e951982dba646bab680e0aba2637e",
          "message": "chore: bump python from 3.13-slim-bookworm to 3.14-slim-bookworm (#13)\n\nBumps python from 3.13-slim-bookworm to 3.14-slim-bookworm.\n\n---\nupdated-dependencies:\n- dependency-name: python\n  dependency-version: 3.14-slim-bookworm\n  dependency-type: direct:production\n...\n\nSigned-off-by: dependabot[bot] <support@github.com>\nCo-authored-by: dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>\nCo-authored-by: Heggria <bshengtao@gmail.com>",
          "timestamp": "2026-03-18T16:49:56+08:00",
          "tree_id": "6ede76879588304bfcdf6654d160bc8a22c7214f",
          "url": "https://github.com/heggria/Hermit/commit/61471de1f23e951982dba646bab680e0aba2637e"
        },
        "date": 1773823843389,
        "tool": "pytest",
        "benches": [
          {
            "name": "tests/benchmark/test_bench_io.py::TestCLIStartupBenchmarks::test_hermit_help_startup",
            "value": 2.118797021598884,
            "unit": "iter/sec",
            "range": "stddev: 0.006222161073967623",
            "extra": "mean: 471.9659268000015 msec\nrounds: 5"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_init",
            "value": 171.78350359909587,
            "unit": "iter/sec",
            "range": "stddev: 0.00008752678101762766",
            "extra": "mean: 5.821280734463162 msec\nrounds: 177"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_append_event",
            "value": 2296.693423833382,
            "unit": "iter/sec",
            "range": "stddev: 0.00033701153508249363",
            "extra": "mean: 435.40857026137724 usec\nrounds: 3060"
          },
          {
            "name": "tests/benchmark/test_bench_kernel.py::TestKernelStoreBenchmarks::test_store_list_tasks",
            "value": 3976.688960901318,
            "unit": "iter/sec",
            "range": "stddev: 0.000008953407166012917",
            "extra": "mean: 251.4654804114601 usec\nrounds: 4084"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_write",
            "value": 4940.358904134524,
            "unit": "iter/sec",
            "range": "stddev: 0.0002698128662891977",
            "extra": "mean: 202.41444385004348 usec\nrounds: 6358"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_read",
            "value": 29368.412828871926,
            "unit": "iter/sec",
            "range": "stddev: 0.0000035906390365291454",
            "extra": "mean: 34.05018874622007 usec\nrounds: 30941"
          },
          {
            "name": "tests/benchmark/test_bench_runtime.py::TestJsonStoreBenchmarks::test_json_store_update",
            "value": 3241.7187142331913,
            "unit": "iter/sec",
            "range": "stddev: 0.0002963834852160672",
            "extra": "mean: 308.47833762052477 usec\nrounds: 3732"
          }
        ]
      }
    ]
  }
}