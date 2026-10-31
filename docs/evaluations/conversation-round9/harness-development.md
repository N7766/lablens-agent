# Round 9 评测准备与难题

## 保持不变的比较条件

沿用 Round 5 的 10 轮、16 份合成资料与原两套 gold，均已暴露。DeepInfra 模型、75 秒服务边界、阶段时间分配、6+1 次调用、1800/2600 输出及 70 次全轮尝试上限不变。无重试、补采或 embedding。候选稀疏复核改变生成协议，因此任何效果仅是本候选在已暴露小样本的观察。

## 实现选择

- 新 runner/scorer 独立版本化，复用原冻结 HTTP helper；不修改历史 runner、原始输入、raw 或评分。生产协议的修改不混入历史证据。
- 测试通过独立模块实例加载 Round 8 的 83 项控制，并给该实例替换 Round 9 runner/scorer；不复制一整份控制代码，也不改变历史测试模块实例。当前输入历史绑定包含原测试源文件。
- Round 8 的全部原始文件、raw seal、全部评分文件、grading manifest、input freeze 及该 input 的完整历史进入当前输入清单。prepare 同时检查 grading/candidate、grading/raw 和 run/input 三项身份。
- 每次新任务提交前与运行结束继续验证完整输入清单，能发现 freeze 后对第八轮原始字节的改动；旧失败、评分分歧和超时诊断不能悄悄被覆盖。
- 评分仍要求 run-summary 已封存、源码/输入检查有效、所有计划轮保留、交付类型明确、所有证据位置可解析、原 gold 与诊断自洽。它不能代替语义阅读或证明答案正确。

## 离线结果

`tests/test_round9_evaluation_harness.py`：**94 passed**，83 项继承控制 + 11 项新历史链控制。新增覆盖 Round 8 全量绑定、输入/raw/评分正文变动、candidate/raw/input 身份冲突，以及当前冻结后历史字节变动。测试使用临时目录、伪造任务和离线 API，不调用真实模型。

当前未执行 prepare，未生成 input-freeze，未启动采集。协议、预算和候选须完成审查后由 root 决定冻结。
