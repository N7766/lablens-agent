# 重启补验协议（执行前冻结）

结束36次检索并封存后，真实停止并重新启动隔离Python worker，保留同一SQLite文件。先以0次provider调用检查BM25/dense索引ID与原结果一致。随后额外允许一次dense检索，问题固定q02-sft-present，top_k=3、同一冻结工作区。

该次最多1请求、1input，预留UTF8字节数+16 token，上限20秒providerHTTP；无重试。先检查此前embedding账本全部已知，累计预留仍在原60请求/250inputs/50000tokens/$0.05之内；未知立即停止。期待同一semantic index_id，embedded_inputs=1且embedding_requests=1，证明新进程只编码查询而复用已保存的10份文档向量。失败不重跑挑选结果。该次不加入12题检索质量分母、不调用生成模型。
