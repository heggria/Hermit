# Changelog

All notable changes to this project will be documented in this file.

## [0.2.5] - 2026-03-17

### Bug Fixes

- 同步 __version__ 为 0.2.5 ([eaa53f8](https://github.com/heggria/Hermit/commit/eaa53f8d83cfe426dd6ac5440a4ec6b774f2c78b))
- 修正 Homebrew tap 仓库名为 heggria/homebrew-tap ([596e0d5](https://github.com/heggria/Hermit/commit/596e0d50bc95fa199baccb352865e594cedc2d81))
- 更新文档对齐测试以匹配新的首页模板结构 ([487cdf1](https://github.com/heggria/Hermit/commit/487cdf1ab17536e9478cd8884f11cd0c5a9dc188))
- Keep README.md in Docker build context ([128b874](https://github.com/heggria/Hermit/commit/128b8745b5cc096166c56e52aad8300d7e5b5a81))
- Add return type annotation to iter_events for pyright ([27a4102](https://github.com/heggria/Hermit/commit/27a410236ba82938d5c4e4e35f30ca92f98fc12f))
- Remove duplicate ...HEAD in diff-cover compare-branch argument ([32b7ac6](https://github.com/heggria/Hermit/commit/32b7ac63e328f83ebf5a3cbb7ed8c9ffae0e3738))
- Stabilize flaky observation test and fix coverage diff-cover fetch ([05f1151](https://github.com/heggria/Hermit/commit/05f1151efa20e62f930fcde5ed74beccc503373e))
- Resolve all CI failures after directory restructure ([f749435](https://github.com/heggria/Hermit/commit/f749435787ea72f7615f839609736ad20a6fd719))
- Correct builtin plugin directory resolution after src/ layout migration ([0aef792](https://github.com/heggria/Hermit/commit/0aef79264097fec53d75359f1aa39ed0e33577ea))
- Adjust timeout for formatter execution and update focus reason in tests ([1ee0d16](https://github.com/heggria/Hermit/commit/1ee0d16fc59050f8ef1770e7a2855a2754125392))
- Use connect-only timeout for Anthropic client to prevent spurious API timeouts ([06ff0e6](https://github.com/heggria/Hermit/commit/06ff0e687cb8bbcd315a98839624e6f33a617d5b))

### CI/CD

- 添加 Homebrew tap 自动更新到 release 流水线 ([ad9dbe4](https://github.com/heggria/Hermit/commit/ad9dbe4e43f38cddb1a8209f4ac8fc1b87ac3f9f))
- Remove test sharding, run full suite per platform/version ([866a220](https://github.com/heggria/Hermit/commit/866a2203ac6ce542bc8df8c43bf3541f58a5b32a))

### Documentation

- Translate all docs and builtin skill files to English ([11d69f7](https://github.com/heggria/Hermit/commit/11d69f737ffa619f85635d0ec5ca57093215d727))

### Features

- 添加产品风格深色主题 GitHub Pages 首页 ([42dd47f](https://github.com/heggria/Hermit/commit/42dd47f36c7fd2b2b44471d6f61285ddb9e8067e))
- Migrate to src/ layout and implement v0.2 kernel infrastructure ([634a813](https://github.com/heggria/Hermit/commit/634a813e4049f0b38da4ab14471dd50ea217703d))
- Enhance conversation projection and ingress observability ([56196b1](https://github.com/heggria/Hermit/commit/56196b1a8e9a5a9744b7b58a9076cc8d7e58a4eb))
- Enhance artifact and receipt reference handling in ingress routing ([43d7c3d](https://github.com/heggria/Hermit/commit/43d7c3ddc27e2c9deaf13e2516bdcb53d4e7de27))
- Enhance Feishu adapter with message acknowledgment and reference handling ([618f6a7](https://github.com/heggria/Hermit/commit/618f6a74c54ce9e787eebd9df8cce79c775f0714))
- Enhance Feishu message handling and task management ([945323f](https://github.com/heggria/Hermit/commit/945323f1eadb12a794ddbc594ef0f643360761bb))
- 重构飞书表情回复链路 ([3f0d379](https://github.com/heggria/Hermit/commit/3f0d379e69df56a2d8c808a3b09b6086ef7cc70d))
- Enhance internationalization and desktop automation error handling ([04a165d](https://github.com/heggria/Hermit/commit/04a165dd561989b90911a5fdb555b65afc30e0d5))
- Add memory governance features and enhance memory management ([2d5570d](https://github.com/heggria/Hermit/commit/2d5570ddd8be3803b5d7a414c9048d4e89f3ee36))
- Enhance desktop automation error handling and improve tool descriptions ([d993ae3](https://github.com/heggria/Hermit/commit/d993ae31e7e3b0644015d7024f0b7995824bb104))
- Enhance internationalization support and add i18n roadmap documentation ([5c03679](https://github.com/heggria/Hermit/commit/5c036796496e33872771c44b9e15ee27bdc090d6))

### Miscellaneous

- Bump version to 0.2.5 ([2ca9ed4](https://github.com/heggria/Hermit/commit/2ca9ed47b6470dce830202c56f58b07d165eb57b))
- 修复重构后残留的旧路径引用和配置问题 ([b913c73](https://github.com/heggria/Hermit/commit/b913c7363a453f1a40d16cb3f6924a7608dca4ad))
- Upgrade infra to 2026.3 best practices ([84ee33f](https://github.com/heggria/Hermit/commit/84ee33f403309cde150d92f414bdcf8153827578))
- Remove tracked reports/ directory (generated artifact) ([f98c12a](https://github.com/heggria/Hermit/commit/f98c12af2db08032b2ff4b3a0aaece5d6113e8e5))
- Clean up post-restructure redundancy and sync AGENTS.md ([eb3c413](https://github.com/heggria/Hermit/commit/eb3c413966e271a695537d4d13a64afaf3a9c696))
- Suppress pyright unused class warning for _StreamPrinter ([83df56c](https://github.com/heggria/Hermit/commit/83df56c0e00e822893ad238d01d390711f044654))
- Enable pyright strict mode for full src/hermit coverage ([a4d2720](https://github.com/heggria/Hermit/commit/a4d2720fe1aba21be08273a851148f83ef01d646))

### Performance

- Optimize test suite and fix 42 broken tests after v0.2 restructure ([62a0b28](https://github.com/heggria/Hermit/commit/62a0b285642c5e9e80f32a7d5f6bf626dc1f56f9))

### Refactoring

- Restructure directory layout for v0.2 kernel architecture ([d9ddd3a](https://github.com/heggria/Hermit/commit/d9ddd3a6c609cdb868e9b795686a3421621e96b5))

## [0.1.0] - 2026-03-11

### Bug Fixes

- MCP 启动容错 + 文档相对路径修复 + GitHub 内置插件 ([8e418da](https://github.com/heggria/Hermit/commit/8e418daa3174a66df822db4b901876b530b12712))

### Documentation

- Refresh README and architecture docs ([f452a8b](https://github.com/heggria/Hermit/commit/f452a8b238b981e4d38aff111808204b2cc85a1b))

### Features

- Add _sanitize_messages to fix orphaned tool_use blocks before compact ([4229051](https://github.com/heggria/Hermit/commit/42290514110bc6f5878873fa54c06fc5eb42e809))
- Initial Hermit import ([375c969](https://github.com/heggria/Hermit/commit/375c969ba6f3a8c128e02164819992f6c8488bbc))
