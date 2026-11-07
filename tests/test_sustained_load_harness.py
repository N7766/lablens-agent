"""Sustained measurement semantics, without networks or local daemons."""
import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sustained', ROOT/'scripts/evaluate_sustained_load.py')
sustained = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sustained)


def sample(status, measurements):
    return {'actuator': {'test': {'http_status': status, 'body': {'measurements': measurements}}}}


def test_timer_counters_are_not_mislabelled_latency_percentiles():
    rows = [sample(200, [{'statistic': 'COUNT', 'value': 4}, {'statistic': 'TOTAL_TIME', 'value': .3}, {'statistic': 'MAX', 'value': .2}]),
            sample(200, [{'statistic': 'COUNT', 'value': 6}, {'statistic': 'TOTAL_TIME', 'value': .4}, {'statistic': 'MAX', 'value': .1}])]
    result = sustained.metric_summary(rows, 'test')
    assert result['statistics']['COUNT']['first'] == 4
    assert result['statistics']['COUNT']['last'] == 6
    assert result['statistics']['TOTAL_TIME']['last'] == .4
    assert result['statistics']['MAX']['max'] == .2
    assert 'p95' not in result['statistics']


def test_unavailable_metrics_and_nonfinite_values_never_become_zero():
    result = sustained.metric_summary([sample(404, []), sample(200, [{'statistic': 'VALUE', 'value': float('nan')}])], 'test')
    assert result['availability'] == 'unavailable'
    assert result['statistics'] == {}
    assert result['http_status_counts'] == {'404': 1, '200': 1}


def test_cold_timer_404_is_preserved_before_valid_samples():
    result = sustained.metric_summary([sample(404, []), sample(200, [{'statistic': 'VALUE', 'value': 3}])], 'test')
    assert result['availability'] == 'observed'
    assert result['statistics']['VALUE']['min'] == 3
    assert result['http_status_counts']['404'] == 1


def test_resource_profile_separates_heap_and_executor_tags():
    assert sustained.METRICS['jvm.heap.used'] == 'jvm.memory.used?tag=area:heap'
    assert sustained.METRICS['executor.worker.active'].endswith('tag=executor:worker')
    assert sustained.METRICS['executor.dispatch.queued'].endswith('tag=executor:dispatch')
    assert sustained.base.build_parser().get_default('phase_seconds') == 60  # Legacy CLI unchanged.
    slots = sustained.base.planned_slots(180, (2,20,2))
    assert len(slots) == 4320 and slots[-1]['scheduled_offset_s'] == 539.5


def test_a_transient_timeout_is_not_hidden_by_counter_reset():
    reset = sustained.metric_summary([sample(200, [{'statistic': 'COUNT', 'value': n}]) for n in [0,1,0]], 'test')
    assert not sustained.no_observed_timeout(reset)
    assert not sustained.no_observed_timeout(sustained.metric_summary([], 'test'))
    zero = sustained.metric_summary([sample(200, [{'statistic': 'COUNT', 'value': 0}])], 'test')
    assert sustained.no_observed_timeout(zero)
