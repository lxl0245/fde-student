# W1 基础评估与调优

默认使用 Promptfoo 调用真正的产品入口。配置好自己的 `FDE_MODEL_URL`、`FDE_MODEL_KEY`、`FDE_MODEL_NAME`，安装 Node.js 22.22.0 或更高版本，从 `evals/` 执行 `npx --yes promptfoo@0.122.2 eval --config promptfooconfig.yaml --no-cache --output ../artifacts/w01/promptfoo-results.json`。Python 路径按自己的虚拟环境调整；Windows 使用 `.venv/Scripts/python.exe`。发布包记录实际验证过的 Promptfoo 版本。

Promptfoo 0.122.2 依赖的 esbuild 0.28.2 需要 macOS 12 或更高版本。在 macOS 11 上，`npx` 会因 esbuild 安装失败而直接退出，不打印原因。这时在工程目录之外本地安装，并把 esbuild 固定为 0.25.12：

```sh
mkdir -p ~/fde-tools && cd ~/fde-tools
cat > package.json <<'EOF'
{"private": true, "dependencies": {"promptfoo": "0.122.2"}, "overrides": {"esbuild": "0.25.12"}}
EOF
npm install
```

之后在 `evals/` 用 `~/fde-tools/node_modules/.bin/promptfoo eval` 代替 `npx --yes promptfoo@0.122.2 eval`，其余参数不变。

默认包含 7 个场景：明确日期分析、缺日期澄清、只读约束、文字确认日期、按钮确认日期、拒绝后更换目标，以及退款说明。后 3 类交互通过真实模型继续同一任务；标签回归只生成并拒绝草案，样例 API 不应产生写入。

输出结构检查、业务工具的人群结果和只读约束可以自动判断；建议是否贴合目标、依据是否充分，以及是否过度承诺需要人工复核。仅有平均分不能覆盖安全失败。评估不会自动读取教师配置。

W1 保存实际模型基线。W5 开始把客户任务纳入持续调优，W7 完整构建黄金、异常、安全集合及保留集。每次记录模型、数据、语义、提示词、配置和实现版本，保留失败输入与 trace。写明问题假设、修改位置、修改前后、关键样本重复结果、保留集与回归、保留或回退理由。

Eval Harness 是测量系统；Agent Harness 是组织模型、上下文、工具和恢复的运行机制。后者可以成为调优对象。协议、数据和 SQL 错误应先修复，不能统称“prompt 不好”。

如首次安装耗时较长，可以预先设置 `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`；W1 Python provider 不需要下载浏览器。该变量只跳过可选浏览器安装。设 `PROMPTFOO_DISABLE_TELEMETRY=1` 可关闭框架使用统计，评估报告仍保存在本地。`--no-cache` 关闭本机评估缓存，不保证上游网关没有缓存。
