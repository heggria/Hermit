# Desktop Companion

Hermit 菜单栏 companion 是一个独立于 runtime 的 macOS 控制进程，不属于插件体系。

对应入口：

- `hermit-menubar`
- `hermit-menubar-install-app`

源码位置：

- [`hermit/companion/menubar.py`](../hermit/companion/menubar.py)
- [`hermit/companion/control.py`](../hermit/companion/control.py)
- [`hermit/companion/appbundle.py`](../hermit/companion/appbundle.py)

## 设计边界

menu bar companion 的职责是控制，不是承载 agent runtime。

它负责：

- 查看服务状态
- 启动 / 停止 / reload `hermit serve`
- 管理 `launchd` 自启
- 管理菜单栏 app 自身的 Login Item
- 打开设置、README、Wiki、日志目录、Hermit home
- 展示 About 面板，方便确认当前版本与运行上下文

它不负责：

- 直接执行插件逻辑
- 直接替代 `serve`
- 接管 session / memory / scheduler 主逻辑

## 安装

需要 macOS，并安装菜单栏依赖：

```bash
pip install -e ".[dev,macos]"
```

如果走仓库的一键安装：

```bash
bash install.sh
```

安装脚本会顺带安装 `hermit-menubar` 并尝试安装本地 app bundle。

## 启动方式

默认管理 `feishu` adapter：

```bash
hermit-menubar
```

显式指定 adapter：

```bash
hermit-menubar --adapter feishu
```

指定 profile：

```bash
hermit-menubar --adapter feishu --profile codex-local
```

指定 base dir：

```bash
hermit-menubar --base-dir ~/.hermit --adapter feishu
```

## 当前菜单项

菜单栏每 5 秒刷新一次状态，展示：

- 运行状态与 PID
- 当前 profile
- 当前 provider
- 当前 model

可执行动作：

- Start Service
- Stop Service
- Reload Service
- Enable Auto-start
- Disable Auto-start
- Enable / Disable Menu Login Item
- Install / Open Menu App
- Open Settings
- Open README
- Open Wiki
- Open Logs
- Open Hermit Home
- About Hermit

## 服务控制实现

menu bar 并不直接嵌入 runtime，而是通过命令行控制：

- `hermit serve --adapter <adapter>`
- `hermit reload --adapter <adapter>`
- `hermit autostart enable --adapter <adapter>`
- `hermit autostart disable --adapter <adapter>`

日志输出默认写入：

```text
~/.hermit/logs/
```

例如：

- `feishu-menubar-stdout.log`
- `feishu-menubar-stderr.log`

## app bundle 与 Login Item

`hermit-menubar-install-app` 会在本地生成一个可双击打开的 app bundle。

当前设计：

- prod app 默认位于 `~/Applications/Hermit.app`
- dev/test 会自动带环境后缀，例如 `~/Applications/Hermit Dev.app`
- launcher 会把 adapter / profile / base-dir 以环境变量和命令参数方式传入
- Login Item 是菜单栏 app 自身，不是 `hermit serve`

这意味着：

- 菜单栏 app 可以在登录时启动
- app 再去控制后台 `serve`
- GUI 层与 runtime 生命周期仍然解耦

## 多环境建议

如果同机维护 prod / dev / test，建议不要直接手敲 `HERMIT_BASE_DIR`，而是统一用包装脚本：

```bash
scripts/hermit-menubar-env.sh prod --adapter feishu
scripts/hermit-menubar-env.sh dev --adapter feishu
scripts/hermit-menubar-install-env.sh dev --open
scripts/hermit-autostart-env.sh test enable --adapter feishu
```

区分规则：

- prod app: `Hermit.app`
- dev app: `Hermit Dev.app`
- test app: `Hermit Test.app`

对应 login item 名称也跟随 app 名，不会互相覆盖。

## 配置文件行为

当你从菜单栏点击 `Open Settings` 时：

- 如果 `~/.hermit/config.toml` 不存在
- companion 会先生成一个默认模板

默认模板会包含：

```toml
default_profile = "default"

[profiles.default]
provider = "claude"
model = "claude-3-7-sonnet-latest"
```

## 限制与注意事项

- 仅支持 macOS
- 缺少 `rumps` 时不会启动
- 不是进程管理器替代品；长期托管仍建议交给 `launchd`
- 只是控制层，不应把业务逻辑继续堆进 `hermit/companion/`

## 这轮文档更新修正的点

- companion 已经是独立模块，不应继续写成“附属脚本”
- 配置、日志、Login Item 的行为此前文档不完整
- 现在所有服务控制示例都统一使用 `--adapter`
