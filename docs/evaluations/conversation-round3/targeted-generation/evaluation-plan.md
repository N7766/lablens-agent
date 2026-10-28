# Round 3 targeted real DPO generation regression

Two previously exposed real-document cases, with eight originally retrieved parent documents each. This tests generation and same-model review only, not fresh retrieval, multi-turn Agent execution, blind evaluation, independent human review, or production correctness.

Use the unchanged repository runner `scripts/evaluate_generation_regressions.py --only-targeted --output .local/round3-targeted/run --env-file <root-approved env path>`. CLI inspection confirms `--only-targeted` selects exactly d01-targeted and h01-targeted, ThreadPoolExecutor parallelism2, no retry. Candidate _generate must remain one draft plus one review; cap2requests/4modelcalls total. Do not invoke until root provides final code freeze and explicit go, and verify copied fixture/runner hashes still match repository inputs. Gold is never passed to tested provider.

Questions:
- d01-targeted: 有人说资料没有 SFT 自由生成基线，这个说法是否成立？请核对基线数值，并判断这些比较能否证明过拟合是唯一根因。
- h01-targeted: 根据 Stage 2 配置 precompute_ref_log_probs: false，能否确认历史运行每一步实际重算参考模型概率？请区分配置记录与实际运行事实。

Frozen semantic rubric:
- d01: reject missing-SFT premise; retain Stage3LoRASFT439/462=95.02% vsStage4FullDPO153/462=33.12%, delta61.90percentagepoints if mentioned. Match sampled462validationquestions and max_new_tokens128 if comparing conditionally. Do not infer unique overfitting cause, modelweightidentity, statisticalsignificance or production performance. Each material claim must cite selected passages that entail it, not just source existence.
- h01: show literal configured false with config citation, but do not infer historical effective runtime/per-step reference recomputation from config/currentcode alone. Need actual deployed code/version, effective arguments and runtime evidence; avoid universal undocumented statement false always causes recomputation. Missing historical evidence is a warranted bounded abstention, not grounds to suppress the documented scalar.

Read full final report and draft/review. Grade semantic claims, selected-passage grounding, false refusal and bounded historical/causal scope separately. Runtime failures remain failures with usageunknown retained; no retries. Label all grades AI developer-side review. Seal raw results before grading. No source/prompt tuning using these outputs after finalfreeze.
