# 参与开发

先查看 [Issues](https://github.com/N7766/lablens-agent/issues) 和 [GitHub 维护约定](docs/github-maintenance.md)，避免重复开单。改动应对应真实问题、可复现验证或有用的文档，不创建空提交或虚构使用反馈。

## 报告问题

- Bug：说明代码版本、运行入口、脱敏复现步骤、预期与实际行为。
- 评测提议：说明问题集来源、开发/测试分离、对照、评分与预算。
- 区分确定性规则错误、服务故障、检索问题和模型回答错误；引用存在不等于结论得到支持。

Issue 和 PR 均为公开内容。不要粘贴密钥、授权头、`.env`、私有文档、完整数据库或含个人信息的日志。用最小合成样本复现；不要为排查直接导出整个工作区。

## 本地验证

Python 3.11+，在仓库根目录运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
lablens demo --output reports/offline-demo.json
```

这两项验证不调用外部模型。工作区 UI 状态测试使用 Node.js（CI 使用 22）：

```bash
node --test services/workspace/tests/workspace-ui.test.cjs
```

纯 JVM 单元测试需要 Java 21 和 Maven：

```bash
mvn -f services/workspace/pom.xml test
```

纯 JVM 测试会跳过真实服务集成项。MySQL / Redis 集成测试按 [服务说明](services/workspace/README.md#tests)单独配置；当前 [CI](.github/workflows/ci.yml)还运行跨服务离线检查。UI 状态测试不等于真实浏览器验收。

## 提交改动

1. 使用独立分支，一个 PR 处理一项清楚的改动，关联已有 Issue（如适用）。
2. 记录实际执行的命令和结果，明确未运行项；文档改动检查链接及命令入口。
3. 检查 `git diff --check`、`git diff --cached` 和 `git status --short`。只暂存相关文件，别把本地实验材料一起提交。
4. 填写 PR 的行为变化、验证和风险。改变运行逻辑时补充针对性回归；不要因文档提交顺手改写历史成绩。

需要外部模型、嵌入接口或 GPU 的实验，先确定数据发送范围和调用/费用上限。保留首次失败，标明重复运行；未知用量不能补零，内部或 AI 评分不能称为独立人工评测。该流程不授权自动租赁 GPU 或批量调用 API。

## 合并与发布

通过检查后再合并，不为制造活跃度重复提交。实现完成、回归通过、回答质量达标和正式 release 是不同状态，应分别记录。未经授权的数据、权重与原始运行输出不随代码上传。
