#!/usr/bin/env python3
"""Real JVM -> Python context-budget rejection, with zero provider calls.

Uses an isolated database, Redis, JVM, Python worker and a loopback provider
sentinel. No model response is fabricated: the budget must reject the actual
Agent request before contacting the sentinel. This is a failure-handling proof,
not a semantic-quality evaluation. Existing main/QA servers are not modified.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluate_workspace_agent_fault import Experiment, ROOT, now, sha


class BudgetExperiment(Experiment):
    def __init__(self, args):
        super().__init__(args)
        self.database = 'lablens_conversation_budget_' + self.run_id
        self.env['LABLENS_CONTEXT_WINDOW_TOKENS'] = str(args.context_window)
        self.summary.update(database=self.database,
            scope='Real JVM/MySQL/Redis and Python Agent; tiny context budget rejects before any provider request.',
            configuration=dict(context_window_tokens_setting=args.context_window,
                budget_estimator='serialized_utf8_bytes_plus_framing_v1', exact_tokenizer=False,
                output_reserve=2600, safety_margin=1024, workers=1, worker_timeout_seconds=10, lease_seconds=30),
            limitations=[
                'The deliberately tiny context window tests a controlled rejection, not normal-window answer quality.',
                'The UTF-8 byte estimate is not the exact provider tokenizer; no provider inference is performed.',
                'HTTP refresh plus direct SQL establish persistence of this task, not multi-process recovery or semantic correctness.'])
        self.inventory.extend([Path(__file__).resolve(), ROOT / 'lablens/conversation.py', ROOT / 'lablens/lexical.py'])
        self.summary['source_sha256'] = {str(p.relative_to(ROOT)): sha(p) for p in self.inventory}

    def run_case(self):
        self.provider.case = 'context_budget_exceeded'
        workspace = self.request('POST', '/api/workspaces', expected=201,
            json={'name': 'Controlled conversation context budget'})['id']
        self.request('PUT', f'/api/workspaces/{workspace}/documents/control', json={
            'title': 'Synthetic observation',
            'text': 'Synthetic validation accuracy was 86 percent. This is only a context-budget fixture.',
            'source': 'synthetic:conversation-budget'})
        revision = self.request('GET', f'/api/workspaces/{workspace}/documents')['revision']
        pinned_state = dict(goal='Compare only the current validation observations.',
                            constraints=['Do not call validation data an independent test set.'], focus='synthetic-control')
        conversation = self.request('POST', f'/api/workspaces/{workspace}/conversations', expected=201,
            json={'title': 'Tiny-window rejection proof', **pinned_state})
        cid = conversation['id']
        route = f'/api/workspaces/{workspace}/conversations/{cid}/turns'
        payload = dict(question='What validation accuracy was observed?', strategy='bm25', mode='agent', top_k=1,
                       approved_revision=revision, expected_conversation_revision=conversation['revision'])
        created = self.request('POST', route, expected=202,
                               headers={'Idempotency-Key': 'context-budget-proof'}, json=payload)
        tid = created['id']

        def terminal():
            value = self.request('GET', f'/api/workspaces/{workspace}/tasks/{tid}')
            return value if value['status'] in {'FAILED', 'SUCCEEDED'} else None

        final = self.wait(terminal, seconds=20)
        refreshed = self.request('GET', f'/api/workspaces/{workspace}/tasks/{tid}')
        history = self.request('GET', f'/api/workspaces/{workspace}/conversations/{cid}')
        replay = self.request('POST', route, headers={'Idempotency-Key': 'context-budget-proof'}, json=payload)
        db_row = json.loads(self.sql("SELECT JSON_OBJECT('id',id,'status',status,'error_code',error_code,"
            "'conversation_id',conversation_id,'conversation_seq',conversation_seq,'snapshot_revision',snapshot_revision,"
            "'snapshot',snapshot_json,'execution_usage',execution_usage_json,'error_result',error_result_json) "
            f"FROM tasks WHERE id='{tid}';", self.database))
        error = final.get('error_result') or {}
        agent = error.get('agent') or {}
        context = error.get('context') or {}
        budget = context.get('context_budget') or {}
        usage = final.get('execution_usage') or {}
        turns = history.get('turns') or []
        checks = dict(
            terminal_failed=final['status'] == 'FAILED',
            exact_error_code=final.get('error_code') == 'context_budget_exceeded',
            structured_worker_error=error.get('status') == 'context_budget_exceeded',
            agent_state=agent.get('state') == 'context_budget_exceeded',
            conversation_identity=context.get('conversation_id') == cid,
            conversation_revision=context.get('conversation_revision') == 1,
            pinned_state_preserved=context.get('pinned_state') == pinned_state,
            context_window_preserved=budget.get('window_units') == self.args.context_window,
            estimator_explicit=budget.get('exact_tokenizer') is False,
            failed_decision_recorded=len(budget.get('calls', [])) == 1 and budget['calls'][0].get('fits') is False,
            zero_model_calls=usage.get('model_calls') == 0,
            zero_embedding_calls=usage.get('embedding_requests') == 0 and usage.get('embedded_inputs') == 0,
            zero_tokens=usage.get('generation_tokens') == dict(prompt_tokens=0, completion_tokens=0, total_tokens=0)
                and usage.get('embedding_tokens') == 0,
            known_usage=usage.get('provider_usage_unknown') is False,
            budget_exhausted=usage.get('budget_exhausted') is True,
            no_agent_steps_or_tools=agent.get('steps') == 0 and agent.get('tool_calls') == 0,
            terminal_trace_preserved=bool(agent.get('trace')) and agent['trace'][-1].get('event') == 'stopped',
            immutable_snapshot=agent.get('snapshot_id') == workspace + ':' + str(revision),
            original_snapshot_context=db_row['snapshot'].get('conversation_context', {}).get('id') == cid
                and db_row['snapshot']['conversation_context'].get('revision') == 1,
            zero_provider_sentinel_requests=not self.provider.events,
            refreshed_identical=refreshed == final,
            history_contains_same_failed_turn=len(turns) == 1 and turns[0].get('task_id') == tid
                and turns[0].get('status') == 'FAILED' and turns[0].get('error_code') == 'context_budget_exceeded',
            db_usage_matches_api=db_row['execution_usage'] == usage,
            db_context_and_trace_match_api=db_row['error_result'] == error,
            db_conversation_matches=db_row['conversation_id'] == cid and db_row['conversation_seq'] == 1,
            idempotency_replay_same_failure=replay.get('reused') is True and replay.get('id') == tid
                and replay.get('status') == 'FAILED')
        case = dict(name='context_budget_exceeded_before_provider', passed=all(checks.values()), checks=checks,
            workspace_id=workspace, conversation_id=cid, task_id=tid, final=final, refreshed=refreshed,
            conversation_history=history, replay=replay, database_row=db_row)
        self.summary['cases'].append(case)
        self.save()
        print(json.dumps(dict(case=case['name'], passed=case['passed'], checks=len(checks),
                              error_code=final.get('error_code'), provider_requests=len(self.provider.events))), flush=True)
        if not case['passed']:
            raise AssertionError('Failed checks: ' + ', '.join(key for key, value in checks.items() if not value))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials', type=Path, default=Path('/Users/nn/PycharmProjects/lablens-agent/.local/services.env'))
    parser.add_argument('--mysql-socket', type=Path, default=Path('/Users/nn/PycharmProjects/lablens-agent/.local/mysql.sock'))
    parser.add_argument('--mysql', default='/opt/homebrew/opt/mysql/bin/mysql')
    parser.add_argument('--java', default='/Users/nn/Library/Java/JavaVirtualMachines/openjdk-24.0.2/Contents/Home/bin/java')
    parser.add_argument('--redis', default='/tmp/lablens-tooling/redis-8.2.2/src/redis-server')
    parser.add_argument('--jar', type=Path, default=ROOT / 'services/workspace/target/lablens-workspace-0.12.0.jar')
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/evaluations/conversation-round1/budget-fault')
    parser.add_argument('--jvm-port', type=int, default=8084)
    parser.add_argument('--python-port', type=int, default=8768)
    parser.add_argument('--provider-port', type=int, default=8770)
    parser.add_argument('--redis-port', type=int, default=6392)
    parser.add_argument('--context-window', type=int, default=4096)
    args = parser.parse_args()
    if not 1024 <= args.context_window <= 4096:
        parser.error('This zero-provider control requires a context window between 1024 and 4096')
    if {args.jvm_port, args.python_port, args.provider_port, args.redis_port} & {8081, 8083, 8765, 8766, 8767, 6380}:
        parser.error('Main and parent QA ports must not be reused')
    experiment = BudgetExperiment(args)
    try:
        experiment.prepare()
        experiment.run_case()
        experiment.summary['status'] = 'passed'
    except Exception as exc:
        experiment.summary['status'] = 'failed'
        experiment.summary['failure'] = dict(type=type(exc).__name__,
            message=str(exc) if isinstance(exc, AssertionError) else 'Inspect private process logs for the controlled failure.')
    finally:
        experiment.cleanup()
    print(json.dumps(dict(status=experiment.summary['status'], cases=len(experiment.summary['cases']),
                         report=str(experiment.out.relative_to(ROOT) / 'summary.json'))), flush=True)
    return 0 if experiment.summary['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
