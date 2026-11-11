# 第十九轮：固定私有模型与租用前验证

2026-11-11，收尾第8/12轮的准备交付。当前仍为7/12轮已交付；GPU部署与真实Agent验收未完成。Vast.ai与500元总预算已确定，Safari登录已确认，尚未创建实例、注册本轮公钥、下载镜像层/权重或调用真实模型。

## 本轮完成了什么

- 固定Qwen2.5-7B-Instruct权重/tokenizer revision、vLLM 0.32.0的linux/amd64镜像digest、BF16与32768上下文；依据是官方小型元数据，不是已下载模型。
- 部署launcher默认只打印计划；执行前检查单GPU、compute capability、驱动、vLLM版本、cgroup内存与可用磁盘。模型服务绑定loopback，使用独立key与SSH tunnel。
- 保留原始部署profile，隔离Hub缓存/隐式凭据；超时、SIGINT、SIGTERM处理所属进程组，检查忽略TERM的后代，清理失败也保存明确退出证据。
- 三次有界兼容性探针：中文文本、结构化选工具、生产scoped-review契约。先冻结真实payload/source/profile，最多3次、每次40秒、总计120秒，不自动重试；任何失败阻断后续。
- 计划只读一次，所有执行凭据绑定同一份bytes/hash。即使运行中原文件被替换或删除，仍能封存真正执行的输入；远端实际权重身份保持未验证。

启动与隔离配置见[部署手册](private-model-deployment.md)，费用与候选见[GPU报价快照](evaluations/conversation-round19/gpu-options.md)，模型依据见[选型记录](evaluations/conversation-round19/model-selection.md)，请求边界见[协议](evaluations/conversation-round19/protocol.md)。

## 实际验证与困难

本地Python全量 **2240 passed**，包含新增launcher **32项**与probe **62项**。新增测试使用真实CPU子进程和本机HTTP server；不下载或加载GPU模型。存在一项FastAPI/httpx上游弃用警告；[完整输出](evaluations/conversation-round19/python-tests.txt)保留该警告。Java、前端和主服务本轮未改，未把上轮测试数充作本轮重新验证。

| 遇到的问题 | 修复与证据 | 仍然未知 |
| --- | --- | --- |
| 只验证显存/驱动会放行不支持候选BF16路径的旧架构；损坏GPU行被忽略 | 检查compute capability且严格解析全部行；旧能力/畸形行均拒绝 | 实际Vast GPU启动结果 |
| 启动器SIGTERM或leader退出会遗留模型子进程 | 标记信号并安全轮询；按所属PGID终止、升级KILL、确认无运行后代；真实CPU回归 | SIGKILL、主机失联后的云端释放需平台核对 |
| macOS退出竞态中killpg返回EPERM，清理异常又吞掉exit证据 | 检查精确PGID活动状态；僵尸不当作运行进程；异常记录为未验证且返回失败 | 本机通过不是Linux GPU运行通过 |
| Hub环境可将下载写入未检查文件系统或读取旧token | 固定HOME/HUB/XET/ASSETS缓存，去旧别名与token path，禁隐式token；CPU环境隔离测试 | 容器解压后的真实空闲空间 |
| 收尾重读plan/profile会把旧请求绑定新文件 | probe固定原bytes；launcher校验预检期间漂移并保存原profile；真实HTTP中替换/删除计划回归 | 哈希清单不是签名审计，也不认证服务权重 |

开发期间的首次4项失败、后续缓存隔离缺口、EPERM和输入冻结问题没有被描述为首次通过；[开发记录](evaluations/conversation-round19/development-notes.md)保留处理过程。正式本地验收在文件停止修改后运行；原历史模型失败、Round13漏发和独立语义评测缺口均保留。

## 可展示与不可写入的简历内容

可以展示：固定模型/镜像候选、部署前资源校验、SSH隔离接入设计、可中断进程组清理、先落盘再发送的有界协议及输入证据绑定。

目前不能写“已完成GPU私有化部署”“模型吞吐提升”“抗住真实业务流量”或“新题准确率达标”。三次探针没有执行检索工具、没有落MySQL历史；完整Agent需要在后续隔离环境中完成提问、工具轨迹、检索/核验、报告与刷新回看。

## 下一步与预算

首选快照中的A6000 48GB级单卡、100GB盘；按2小时、50GB入站和2GB出站估算约1.3859美元，报价会变且未含税费等。建议首个租期以2小时、10美元为操作上限，位于用户500元总预算内，创建前再核价。该上限必须靠平台观察与及时释放管理，launcher的7200秒只停止进程，不停止GPU/磁盘收费。

具体租用和本轮临时公钥注册最后确认后，先验证镜像/权重身份和3次兼容请求；任何失败保留原件，不自动重跑。通过后另行冻结完整Agent验收并在剩余租期内执行。导出并校验证据后停止/销毁本次实例，回读平台实例、存储和账单状态。草稿候选不合并，8081/8765主服务保持原版本。
