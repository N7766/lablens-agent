import json
import os
import copy
from uuid import uuid4
from urllib.parse import urlparse
import httpx
from dotenv import load_dotenv
from .schemas import Answer, answer_request_schema
from .policy import BOUNDARY
from .runtime_telemetry import observe_request


class ModelError(RuntimeError):
    pass


def _inline_schema(schema):
    """Inline local Pydantic refs so independently composed actions cannot collide."""
    definitions = schema.get('$defs', {})

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        if '$ref' in value:
            ref = value['$ref']
            if not ref.startswith('#/$defs/') or ref.split('/')[-1] not in definitions:
                raise ModelError('Unsupported action schema reference')
            return expand(definitions[ref.split('/')[-1]])
        return {key: expand(item) for key, item in value.items() if key not in {'$defs', 'title', 'default'}}

    return expand(schema)


def workspace_action_payload(messages, tools, *, model, max_tokens=1800):
    """Provider-portable structured decision; the model still selects the action.

    No native tool parser or unconstrained JSON-string argument generation is used.
    All arguments are validated again by the selected local tool before execution.
    """
    alternatives = []
    for tool in tools:
        function = tool['function']
        alternatives.append(dict(type='object', additionalProperties=False,
            properties=dict(action=dict(type='string', enum=[function['name']], description=function['description']),
                            arguments=_inline_schema(function['parameters'])),
            required=['action', 'arguments']))
    schema = dict(type='object', additionalProperties=False,
                  properties=dict(decision=dict(anyOf=alternatives)), required=['decision'])
    converted = []
    for message in messages:
        if message.get('tool_calls'):
            decisions = [dict(action=call['function']['name'], arguments=json.loads(call['function']['arguments']))
                         for call in message['tool_calls']]
            converted.append(dict(role='assistant', content=json.dumps(dict(previous_decisions=decisions), ensure_ascii=False)))
        elif message.get('role') == 'tool':
            converted.append(dict(role='user', content=json.dumps(dict(untrusted_tool_result=json.loads(message['content'])), ensure_ascii=False)))
        else:
            converted.append(copy.deepcopy(message))
    return dict(model=model, messages=converted, temperature=0, max_tokens=max_tokens,
        response_format=dict(type='json_schema', json_schema=dict(name='workspace_action', strict=True, schema=schema)))


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

    def choose_workspace_action(self, payload, *, timeout_seconds):
        """One planned request. Invalid decisions preserve known usage and stop.

        There is deliberately no fallback request to native tool calling: a timeout
        may already have incurred usage and must never trigger an unknown retry.
        """
        try:
            timeout = min(self.timeout_seconds, max(.001, timeout_seconds))
            with observe_request(payload, timeout) as observation:
                response = httpx.post(self.base + '/chat/completions', timeout=timeout,
                    headers={'Authorization': 'Bearer ' + self.key}, json=payload)
                observation.received(response)
                response.raise_for_status()
                body = response.json()
                observation.received(response, body)
            choice = body['choices'][0]
            message = choice['message']
            if not isinstance(message, dict):
                raise ValueError
            finish_reason = choice.get('finish_reason')
            # Do not retain rejected prose in traces. Return the shape so the
            # orchestrator can classify the failure without losing known usage.
            rejected = dict(role='assistant', content=bool(message.get('content')),
                            finish_reason=finish_reason)
            usage = body.get('usage', {})
            try:
                parsed = json.loads(message.get('content'))
                if finish_reason != 'stop' or not isinstance(parsed, dict) or set(parsed) != {'decision'}:
                    return rejected, usage
                decision = parsed['decision']
                if not isinstance(decision, dict) or set(decision) != {'action', 'arguments'}:
                    return rejected, usage
                allowed = {item['properties']['action']['enum'][0]
                           for item in payload['response_format']['json_schema']['schema']['properties']['decision']['anyOf']}
                if decision['action'] not in allowed or not isinstance(decision['arguments'], dict):
                    return rejected, usage
                arguments = json.dumps(decision['arguments'], ensure_ascii=False)
                if len(arguments) > 30000:
                    return rejected, usage
            except (TypeError, ValueError, KeyError):
                return rejected, usage
            return dict(role='assistant', content=None, finish_reason=finish_reason,
                tool_calls=[dict(id='action-' + uuid4().hex, type='function',
                    function=dict(name=decision['action'], arguments=arguments))]), usage
        except httpx.TimeoutException:
            raise ModelError('Provider request timed out; usage for this request is unknown. No automatic retry was sent.') from None
        except httpx.HTTPStatusError as exc:
            raise ModelError(f'Provider returned HTTP {exc.response.status_code}; check local configuration') from None
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            raise ModelError('Provider request failed or returned an invalid response') from None

    def complete(self, messages, tools, *, timeout_seconds=None, tool_choice=None):
        if tool_choice is not None and (tool_choice not in ('auto', 'required', 'none') or not tools):
            raise ModelError('tool_choice requires tools and must be auto, required or none')
        try:
            timeout = self.timeout_seconds if timeout_seconds is None else min(self.timeout_seconds, max(.001, timeout_seconds))
            response = httpx.post(self.base + "/chat/completions", timeout=timeout,
                headers={"Authorization": "Bearer " + self.key},
                json={"model": self.model, "messages": messages, **({"tools": tools} if tools else {}),
                      **({"tool_choice": tool_choice} if tool_choice is not None else {}),
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
