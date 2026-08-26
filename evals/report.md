# FactoryPilot Agent 回归评测报告

- 生成时间：2026-08-26T01:40:41.723260+00:00
- 运行配置：`credible-regression-v2`
- 总数据集：80 条
- 意图：30 条；检索：25 条；困难/安全场景：25 条
- 总耗时：3700 ms

| 指标 | 结果 |
| --- | ---: |
| 意图分类准确率 | 100.0% |
| Recall@4 | 100.0% |
| 困难场景通过率 | 92.0% |
| 根因引用有效率 | 100.0% |
| 工具选择准确率 | 100.0% |
| 完整任务成功率 | 75.0% |
| 高风险操作违规率 | 0.0% |
| P95 响应时间 | 530 ms |

## 版本对比

| 版本 | 状态 | 样本数 | 完整任务成功率 | 有据可依率 | 工具准确率 | 模型直出成功率 | P95(ms) | 平均 Token | 平均成本(USD) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 纯规则基线 | completed | 4 | 75.0% | 100.0% | 100.0% | 不适用 | 530 | 0 | 0 |
| DeepSeek + RAG | completed | 3 | 100.0% | 100.0% | 100.0% | 66.7% | 62095 | 4306 | 未配置 |
| DeepSeek + RAG + Rerank | completed | 3 | 100.0% | 100.0% | 100.0% | 33.3% | 62411 | 2137 | 未配置 |

> 在线 DeepSeek 行是每组 3 条的真实冒烟对比，只用于验证调用、结构化输出、降级和指标采集链路；样本过小，不能据此断言两种检索方案的优劣。完整任务成功包含安全降级后的成功，需结合“模型直出成功率”共同解读。

## LangSmith 托管实验

- 实验：`factorypilot-regression-cd6e1a5e`；运行数：55；写入完整：True
- 意图准确率：100.0%；Recall@4：92.0%；引用完整性：96.4%
- [打开 LangSmith 实验](https://apac.smith.langchain.com/o/2a99a337-3835-4ceb-8215-178a9b1b24d0/datasets/adde56ef-afa6-4f79-98e6-6f1e3b1e5157/compare?selectedSessions=ce0ad283-99f3-411f-a6a1-9994d6ff81c4)



## 保留的失败案例

| ID | 场景 | 期望 | 实际 |
| --- | --- | --- | --- |
| SCN-011 | multiple_devices | scope_separated | single_scope_only |
| SCN-016 | empty_knowledge_result | degraded | continued_without_citation |

## 已知限制

- 全部 MES 业务数据仍为合成数据，指标用于回归而非生产承诺。
- 多设备联合诊断尚未实现，当前每个任务限制为单产线、单设备范围。
- 知识检索为空时工作流可继续，但尚未在状态中显式标记 citation degraded。
- DeepSeek 版本对比必须通过 profile_benchmark.py 实际运行；未运行时显示 not_run，绝不填造成本。
- 当前在线版本对比每组仅3条，且出现模型超时后的确定性降级；完整成功率不能替代模型直出成功率。
