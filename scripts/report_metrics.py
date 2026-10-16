"""Recompute metrics from frozen summaries and explicit assistant reviews."""
import argparse
import json
import statistics
from pathlib import Path


def aggregate(summary, review):
    ratings={(r['case_id'],r['strategy'],r.get('repeat',1)):r for r in review['ratings']}
    if len(ratings)!=len(review['ratings']):raise ValueError('Duplicate review rows')
    expected={(r['case_id'],r['strategy'],r.get('repeat',1)) for r in summary['cases']}
    if expected!=set(ratings):raise ValueError('Review coverage does not match runs')
    result={}
    for strategy in sorted({r['strategy'] for r in summary['cases']}):
        rows=[r for r in summary['cases'] if r['strategy']==strategy]
        model=[r for r in rows if not r.get('policy_refusal',False)]
        def passed(r):return ratings[r['case_id'],r['strategy'],r.get('repeat',1)]['task_success'] is True
        result[strategy]=dict(total_cases=len(rows),policy_cases=len(rows)-len(model),model_cases=len(model),
            model_task_success=sum(passed(r) for r in model),policy_task_success=sum(passed(r) for r in rows if r.get('policy_refusal')),
            protocol_completed=sum(r['status']=='completed' for r in model),
            executable_plans=sum(r.get('executable_plan',False) for r in model),
            plan_steps_executed=sum(r.get('plan_steps_executed',0) for r in model),
            median_model_seconds=statistics.median(r['latency_seconds'] for r in model) if model else None,
            model_calls=sum(r['model_calls'] for r in rows),tool_calls=sum(r['tool_calls'] for r in rows),
            reported_prompt_tokens=sum(r['reported_prompt_tokens'] for r in rows),reported_completion_tokens=sum(r['reported_completion_tokens'] for r in rows),
            reported_cost_usd=sum(r['reported_estimated_cost_usd'] or 0 for r in rows),
            unknown_usage_cases=[f"{r['case_id']}-r{r.get('repeat',1)}" for r in rows if r['usage_incomplete']],
            per_repeat={str(n):dict(tasks=sum(r.get('repeat',1)==n for r in model),passed=sum(passed(r) for r in model if r.get('repeat',1)==n)) for n in sorted({r.get('repeat',1) for r in model})})
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path);args=parser.parse_args()
    result=aggregate(json.loads((args.directory/'summary.json').read_text()),json.loads((args.directory/'assistant-review.json').read_text()))
    (args.directory/'aggregate.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
