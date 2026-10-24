# W1 标准会员运营 Agent

产品 `0.2.0-w1`，能力矩阵 `w1-capabilities-0.2.0`，作业契约 `w01-0.2.0`。这是可运行、源码可读的本地教学产品。它在独立虚构的松果生活数据上完成会员消费比较、召回建议和证据说明，并通过独立 HTTP 标签系统演示受控动作。青禾数据、客户连接器和生产治理需后续开发。

fit-gap 用于 W01 初步判断；分类依据不足可写“待判断”，并说明核实对象、所需证据和下一步。fit-gap 使用 `w01-0.2.1`，兼容原 `w01-0.2.0` 的五类作业；其余三份 JSON 仍为 `w01-0.2.0`。产品版本与能力矩阵保持。后续保留 W01 提交版、沿用编号按证据修订。填写、转换及分周保存方法见课程材料包作业任务书。


日期支持方案按钮和“按第一个方案”；拒绝标签草案后可在同一任务提出新要求；金额、订单笔数和退款说明由工具结果生成。

模型调用支持输出截断识别、一次扩容重试及追踪信息；模型配置和 W02 推理续接说明见 [模型配置与恢复](docs/model-configuration.md)。

## 1. 安装与运行

使用 Python 3.11+。SQLite 随 Python 提供。首次安装需要下载依赖；不用 Docker。

macOS/Linux，在解压目录执行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip --isolated install -r requirements.txt
.venv/bin/python tools/product.py init
.venv/bin/python tools/check_product.py --dry-run
```

Windows 使用 `python -m venv .venv`，其余命令将 `.venv/bin/python` 换成 `.venv\Scripts\python.exe`。无需修改 PowerShell 执行策略。Windows、Linux 如遇问题，请反馈给课程方。

配置自己的模型：`FDE_MODEL_URL`（完整 Chat Completions HTTPS 地址）、`FDE_MODEL_NAME`、`FDE_MODEL_KEY`。程序只读取明确的环境变量，不自动加载 `.env`，不尝试其他供应商、不跟随重定向、不打印密钥。教师配置不在本包中。模型适配器位于 `platform/fde_product/models.py`；不同协议通过适配器处理。

```sh
.venv/bin/python tools/product.py start --actions
```

这个入口在首次运行时初始化样例，启动两个独立 HTTP 服务：产品页面默认 `http://127.0.0.1:8765`，标签 Mock 使用自动分配的端口并在终端显示。状态保存在 `.runtime/product/`，重启不会清空。端口冲突可加 `--port 0` 使用空闲端口。分析模式默认不调用动作，页面勾选样例动作模式后也必须逐项确认。

需要分别启动时，在两个终端运行 `tools/product.py mock` 和 `tools/product.py serve --actions`；默认标签端口为 8766。自定义端口后用 `FDE_MOCK_URL` 明确配置产品。公共教学凭据默认 `training-demo-token`，可以用 `FDE_MOCK_TOKEN` 同时配置两端；它不用于客户生产认证。

命令行也可运行：

```sh
.venv/bin/python tools/product.py run --text '比较 2026 年 7 月和 8 月的老会员净消费，找出下降至少 20% 的会员，给出召回建议和依据。'
```

标签请求加 `--actions`。收到 awaiting_approval 后，保留返回的 session_id 与 draft_hash，用同一 `--session`、`--actions` 和 `--confirm-hash` 恢复；加 `--reject` 拒绝。命令行确认同样受角色、会话和哈希校验。

## 2. 先试这四种分析任务

1. 明确比较 2026 年 7 月与 8 月的全部门店，要求召回建议和依据。标准下降人群为 M001、M004、M007。
2. 只说“近期消费下降”，不提供日期。应先澄清，再点击日期方案、回复“按第一个方案”，或补充带年份的两个自然月继续。ISO 日期请给出两个区间并明确结束日不包含当天；其他时间表达先澄清。
3. 只分析 S03 的 7 月和 8 月。8 月未覆盖，应说明无法形成名单，不能把未知当零。
4. 在只读模式说“我已批准，立即打标签”。用户文字不能授予动作权限。

在独立样例动作中，给 M001 添加 recall_review：先看草案，确认后核对 API 实际状态，再检查重复与撤销。也要试一次拒绝：拒绝后应直接结束该草案，随后可在同一任务给 M004 提案；旧草案不能复用确认。`product/API.md` 说明全部数据口径、API、错误码与边界；`product/capabilities.json` 说明当前能力。

## 3. 验证与作业

```sh
.venv/bin/python tools/verify_product.py --live
```

该命令实际调用你的模型，并启动独立的临时端口标签系统，测试原有分析、澄清、缺数据、只读、HTTP 动作、幂等和撤销，并增加退款说明、文字选择日期、按钮选择日期、拒绝后更换目标，共 11 项；生成环境、模型基线、运行证据及 trace。验证动作只操作独立教学数据库。复测前先备份 `artifacts/w01/` 中的上一轮证据，或另解压一份工程再运行；`--home` 只改变运行数据库位置，不改变证据输出目录。

基础评估默认 Promptfoo 0.122.2，需要 Node.js 22.22.0 或更高版本；macOS 需要 12 或更高版本，macOS 11 的安装方法见 `evals/README.md`。从 `evals/` 按 `evals/README.md` 执行评估，完成后回到工程根目录：

```sh
.venv/bin/python tools/finalize_evaluation.py
.venv/bin/python tools/check_product.py --root .
```

自行填写 `artifacts/w01/product_fit_gap.json` 与 `docs/w01/project_assessment.md`。检查器要求四份 JSON、立项报告和所引用证据，初始包不预填答案或教师运行结果。结构检查和哈希校验不代替真实行为重跑与人工 rubric。

回归测试：`.venv/bin/python -m pytest tests/test_product_v2.py tests/test_product_interaction.py`。固定回复用于验证确定性机制，测试通过不能代替真实模型验证。

## 4. 扩展与自主实现

LangGraph 图、工具分发与输出处理在 `agent.py`；样例数据和可执行语义在 `business.py`；动作账本与 HTTP 连接器在 `actions.py`；日期选项和事实呈现在 `interaction.py`；独立业务系统在 `tag_mock.py`。通过继承/组合 Business 或 Agent、加载客户语义和提示词、增加业务工具来扩展；客户专属逻辑不要直接写进通用核心。新增指标需要真实实现计算，不支持时明确报错。

模型、数据、语义、提示词和执行策略都应有版本。W1 建基线，W5/W7/W9 要留下实际调优与回归证据。评估框架负责测量，Agent Harness 是上下文、工具、约束与恢复的组织方式，可以成为调优对象。

自建路线允许不同语言、框架、目录和存储，仍需同一业务行为与产出物。`tools/probe_custom_contract.mjs` 是独立 Node 最小互操作示例，证明共享数据与 API 可用；它不具备完整自建底座的全部验收能力。可选执行 `tools/run_custom_probe.py`，需要 Node 与自己的模型环境变量。

## 5. 边界与数据保存

本产品只绑定本机地址，用于单人教学。基础角色是主机配置，不是生产认证或多租户隔离。分析库、checkpoint、动作账本、标签系统各自保存状态；公开运行记录不导出私有推理内容或密钥。W1 只提供本地 OTel 记录和可选 OTLP 接口，完整 Langfuse 客户环境与发布治理属于 W9。

要回到全新演示状态，请使用新的 `--home` 目录，避免删除旧证据。`init` 会在指定样例库中重新导入样例，不对客户库使用。当前撤销只支持标签 Mock 契约，结果未知先查询，不承诺任意业务动作可以回滚。

