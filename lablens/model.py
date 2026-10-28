import json
import os
from urllib.parse import urlparse
import httpx
from dotenv import load_dotenv
from .schemas import Answer, answer_request_schema
from .policy import BOUNDARY


class ModelError(RuntimeError):
    pass


class HTTPModel:
    mode = "live"

    def __init__(self):
        load_dotenv()
        self.base = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.model = os.getenv("LLM_MODEL", "deepseek-chat")
        self.key = os.getenv("LLM_API_KEY", "")
        try:
            self.timeout_seconds = int(os.getenv("LLM_TIMEOUT_SECONDS", "45"))
            if not 1 <= self.timeout_seconds <= 180:
                raise ValueError
        except ValueError:
            raise ModelError("LLM_TIMEOUT_SECONDS must be an integer from 1 to 180") from None
        try:
            parsed = urlparse(self.base)
            # Access port now: malformed or out-of-range ports must fail before HTTP construction.
            parsed.port
            if not parsed.hostname:
                raise ValueError
        except ValueError:
            raise ModelError("LLM_BASE_URL must contain a valid hostname and port") from None
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ModelError("LLM_BASE_URL must not contain credentials, query, or fragment")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
            raise ModelError("Use HTTPS for remote model endpoints")
        if not self.key:
            raise ModelError("Set LLM_API_KEY in your local .env; no live request was sent")

    def complete(self, messages, tools, *, timeout_seconds=None):
        try:
            timeout = self.timeout_seconds if timeout_seconds is None else min(self.timeout_seconds, max(.001, timeout_seconds))
            response = httpx.post(self.base + "/chat/completions", timeout=timeout,
                headers={"Authorization": "Bearer " + self.key},
                json={"model": self.model, "messages": messages, **({"tools": tools} if tools else {}),
                      "temperature": 0, "max_tokens": 2600,
                      **({"response_format": {"type": "json_schema", "json_schema": {"name": "answer", "strict": True, "schema": answer_request_schema()}}} if not tools else {})})
            response.raise_for_status()
            body = response.json()
            message = body["choices"][0]["message"]
            message["finish_reason"] = body["choices"][0].get("finish_reason")
            return message, body.get("usage", {})
        except httpx.TimeoutException:
            raise ModelError("Provider request timed out; usage for this request is unknown. No automatic retry was sent.") from None
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
            answer["limitations"] = []
            answer["limitation_ids"] = ["note:units", "note:validation", "note:significance", "note:latency"]
            def measure(metric, value, unit, candidate=None, direction="absolute", baseline="run:sft"):
                return dict(metric=metric, baseline_id=baseline, candidate_id=candidate, value=value, unit=unit, direction=direction)
            answer["claims"][0]["measurements"] = [measure("correct", a["correct"], "count"), measure("correct", b["correct"], "count", baseline=b["id"]), measure("num_samples", a["num_samples"], "count"), measure("accuracy", a["accuracy"], "fraction"), measure("accuracy", b["accuracy"], "fraction", baseline=b["id"]), measure("accuracy_delta_percentage_points", comparison["accuracy_delta_percentage_points"], "percentage_points", b["id"], "candidate_minus_baseline")]
            answer["claims"][1]["measurements"] = [measure("mean_length", a["mean_length"], a["length_unit"]), measure("mean_length", b["mean_length"], b["length_unit"], baseline=b["id"]), measure("length_reduction_percent", comparison["length_reduction_percent"], "percent", b["id"], "baseline_minus_candidate_over_baseline")]
            return {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)}, {}
        return {"role": "assistant", "content": None, "tool_calls": [{"id": f"replay-{self.step}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}, {}
