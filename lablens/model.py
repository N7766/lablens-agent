import json
import os
from urllib.parse import urlparse
import httpx
from dotenv import load_dotenv


class ModelError(RuntimeError):
    pass


class HTTPModel:
    mode = "live"

    def __init__(self):
        load_dotenv()
        self.base = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.model = os.getenv("LLM_MODEL", "deepseek-chat")
        self.key = os.getenv("LLM_API_KEY", "")
        parsed = urlparse(self.base)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ModelError("LLM_BASE_URL must not contain credentials, query, or fragment")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
            raise ModelError("Use HTTPS for remote model endpoints")
        if not self.key:
            raise ModelError("Set LLM_API_KEY in your local .env; no live request was sent")

    def complete(self, messages, tools):
        try:
            response = httpx.post(self.base + "/chat/completions", timeout=45,
                headers={"Authorization": "Bearer " + self.key},
                json={"model": self.model, "messages": messages, "tools": tools,
                      "temperature": 0, "max_tokens": 1800})
            response.raise_for_status()
            body = response.json()
            return body["choices"][0]["message"], body.get("usage", {})
        except httpx.HTTPStatusError as exc:
            raise ModelError(f"Provider returned HTTP {exc.response.status_code}; check local configuration") from None
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            raise ModelError("Provider request failed or returned an invalid response") from None


class ReplayModel:
    """Fixed scenario for offline engineering checks. This is NOT model inference."""
    mode = "scripted_replay"

    def __init__(self):
        self.step = 0

    def complete(self, messages, tools):
        self.step += 1
        if self.step == 1:
            name, args = "list_runs", {}
        elif self.step == 2:
            name, args = "compare_runs", {"baseline_id": "run:sft", "candidate_id": "run:dpo-best"}
        elif self.step == 3:
            name, args = "search_notes", {"query": "验证集 显著性 token 词数 latency"}
        else:
            comparison = next(json.loads(m["content"]) for m in messages if m.get("role") == "tool" and "accuracy_delta_percentage_points" in m["content"])
            a, b = comparison["baseline"], comparison["candidate"]
            answer = {"status": "answered", "claims": [
                {"text": f"SFT 在验证集上答对 {a['correct']}/{a['num_samples']}（{a['accuracy']:.2%}），DPO 答对 {b['correct']}/{b['num_samples']}（{b['accuracy']:.2%}），增加 {comparison['accuracy_delta_percentage_points']:.3f} 个百分点。", "source_ids": [a["id"], b["id"]]},
                {"text": f"平均输出长度从 {a['mean_length']:.2f} 降至 {b['mean_length']:.2f} 个按空格切分的词，减少 {comparison['length_reduction_percent']:.2f}%。", "source_ids": [a["id"], b["id"], "note:units"]}],
                "limitations": ["此页面使用脚本回放，未调用真实模型。", "验证集参与检查点选择，不能当作独立测试集。", "这些摘要不能证明统计显著性、推理时延或显存收益。"]}
            return {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)}, {}
        return {"role": "assistant", "content": None, "tool_calls": [{"id": f"replay-{self.step}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}, {}
