window.BENCHMARK_DATA = {
  "lastUpdate": 1773753157250,
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
      }
    ]
  }
}