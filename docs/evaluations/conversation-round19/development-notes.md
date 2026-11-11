# Round19 开发中的失败与复查

2026-11-11。以下为持续修改期间的开发观察，不是冻结后的模型/性能测量；没有GPU请求或模型下载。

1. 独立编写的launcher首轮CPU测试18通过/4失败：低compute capability被放行、损坏的第二GPU行被忽略、leader退出后忽略TERM的后代存活、launcher SIGTERM留下新session子进程。修复后用真实CPU进程重新检查，测试finally会清理自己创建的子进程。
2. 资源检查原来只看output文件系统，但继承的HF_HOME/HF_HUB_CACHE/XET可能落到别的盘。补固定缓存目录、download-dir、旧别名移除与隐式token隔离，加入环境回读测试。没有读取或输出用户token。
3. macOS短命进程组退出期间，killpg检查出现EPERM。不能直接将EPERM等同不存在；改按ps读取精确PGID与运行状态。cleanup失败必须保留exit.json和未验证标志，不能吞掉终态或宣称停止计费。
4. 交叉审查发现probe收尾重新计算磁盘plan hash，会在运行中编辑/删除计划时绑定错证据或封存失败。改为开始一次读取和校验，保存实际执行bytes，intent/summary/manifest全程绑定同hash，末尾只记录磁盘漂移。真实loopback HTTP处理中替换/删除计划，旧执行凭据仍正确。
5. 同类问题也检查launcher：host预检可能耗时，profile在期间变动必须在Popen前拒绝。保存原profile与原hash，避免旧命令绑定新配置。
6. 一个开发中的全量运行与测试文件修改重叠，得到2229通过/2失败（新增严格profile比对后，旧测试只改内存而未写回profile）。该运行不作为冻结验收；补齐fixture写回并等文件停止修改后执行最终全量。随后2235通过的结果保留在python-tests-before-config-guard.txt。最后交叉审查又补5项固定配置一致性检查，最终python-tests.txt为2240通过，保留上游弃用警告。
7. Safari实际模板预填完整image@sha256后，CLI预览保留同一摘要；已Cancel，未创建或保存模板。该观察只覆盖表单编码，不证明后端拉取或模型启动。平台实例/存储均显示0，SSH列表空。

未租用、未下载权重/镜像层、真实模型调用0。实际GPU参数、启动时长、吞吐、峰值显存、语义质量及最终费用继续留空；旧模型失败不重写。

8. 最后独立复查发现generation_config/backend/prefix/chunked/KV字段可能被编辑后仍通过profile验证，但实际命令仍固定。将固定值纳入严格类型/值验证并显式传KV dtype；新增5个漂移拒绝案例，重新完成全量2240测试。
