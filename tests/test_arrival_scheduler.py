"""Offline protocol tests; no timed experiment, subprocess or service startup."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('arrival_scheduler', ROOT/'scripts/diagnose_arrival_scheduler.py')
scheduler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scheduler)


def test_fixed_schedule_and_observer_slots_are_complete():
    slots = scheduler.planned_slots(6)
    assert len(slots) == 144
    assert [x['sequence'] for x in slots] == list(range(1, 145))
    assert [len([x for x in slots if x['stage']==stage]) for stage in ('under','over','recovery')] == [12,120,12]
    assert slots[12]['scheduled_offset_s'] == 6 and slots[131]['scheduled_offset_s'] == 11.95
    assert slots[-1]['scheduled_offset_s'] == 17.5
    observations = scheduler.planned_observations(18)
    assert len(observations) == 18 and observations[0]['scheduled_offset_s'] == .2
    assert observations[-1]['scheduled_offset_s'] == 17.2


def test_old_111ms_failure_is_still_unsent_at_same_100ms_gate():
    assert scheduler.decision(111.089, 0) == 'scheduler_late'
    assert scheduler.decision(100, 0) is None
    assert scheduler.decision(100.001, 0) == 'scheduler_late'
    assert scheduler.decision(0, 32) == 'client_in_flight_limit'
    assert scheduler.decision(0, 31) is None


def test_missing_observations_are_not_dropped_or_invented_as_completed():
    slots = scheduler.planned_observations(3)
    events = [dict(event='started',sequence=1,started_offset_s=.21),
              dict(event='completed',sequence=1,finished_offset_s=.39,duration_ms=180),
              dict(event='started',sequence=2,started_offset_s=1.21)]
    rows = scheduler.observer_ledger(slots, events)
    assert len(rows) == 3
    assert [x['status'] for x in rows] == ['completed','completion_not_observed','start_not_observed']
    assert rows[0]['lateness_ms'] == pytest.approx(10)
    assert rows[0]['duration_ms'] == 180


@pytest.mark.parametrize('end', [dict(finished_offset_s=.1,duration_ms=1),
    dict(finished_offset_s=.4,duration_ms=-1), dict(finished_offset_s=float('nan'),duration_ms=1),
    dict(finished_offset_s=.4,duration_ms='180')])
def test_invalid_observer_timing_cannot_be_reported_complete(end):
    events = [dict(event='started',sequence=1,started_offset_s=.2),
              dict(event='completed',sequence=1,**end)]
    assert scheduler.observer_ledger(scheduler.planned_observations(1),events)[0]['status']=='invalid_event_record'


def test_duplicate_observer_events_fail_instead_of_selecting_a_convenient_record():
    events = [dict(event='started',sequence=1,started_offset_s=.2)]*2
    assert scheduler.observer_ledger(scheduler.planned_observations(1),events)[0]['status']=='invalid_event_record'


def test_partial_json_from_terminated_observer_is_preserved_and_counted(tmp_path):
    path = tmp_path/'events.jsonl'
    original = '{"event":"started","sequence":1}\n{"event":"comple'
    path.write_text(original)
    rows, invalid = scheduler.read_events(path)
    assert rows == [{'event':'started','sequence':1}] and invalid == 1
    assert path.read_text() == original


def test_existing_evidence_file_is_never_overwritten(tmp_path):
    path = tmp_path/'summary.json'
    scheduler.write_new(path, {'passed':False})
    with pytest.raises(FileExistsError):
        scheduler.write_new(path, {'passed':True})
    assert json.loads(path.read_text()) == {'passed':False}


def test_no_lag_samples_remain_unknown_not_zero():
    actual = scheduler.distribution([None, float('nan'), True])
    assert actual['n']==0 and actual['max_ms'] is None and actual['p99_ms'] is None


def test_isolation_failure_or_frozen_source_change_cannot_pass():
    shared = dict(accounting_valid=True,counts={'unsent':12},unsent_reasons={'scheduler_late':12},loop_lag={'n':10,'max_ms':180})
    isolated = dict(accounting_valid=True,counts={'dispatched':144},unsent_reasons={},loop_lag={'n':10,'max_ms':1})
    assert scheduler.comparison(shared,isolated,True)['mechanism_comparison_passed']
    assert not scheduler.comparison(shared,isolated,False)['mechanism_comparison_passed']
    isolated['counts']['unsent']=1
    assert not scheduler.comparison(shared,isolated,True)['mechanism_comparison_passed']
    isolated['counts']['unsent']=0
    isolated['accounting_valid']=False
    assert not scheduler.comparison(shared,isolated,True)['mechanism_comparison_passed']


@pytest.mark.parametrize('flags',[['--phase-seconds','9'],['--blocking-ms','nan'],
    ['--blocking-ms','301'],['--payload-bytes','65537']])
def test_cli_prevents_unbounded_diagnostic_runs(flags):
    with pytest.raises(SystemExit):
        scheduler.parse_args(['--output','unused','--source-commit','frozen',*flags])


def test_child_cleanup_uses_bounded_escalation_and_confirms_exit():
    calls = []
    class Child:
        exitcode = -9
        alive = True
        def join(self,timeout): calls.append(('join',timeout))
        def is_alive(self): return self.alive
        def terminate(self): calls.append(('terminate',))
        def kill(self): calls.append(('kill',)); self.alive=False
        def close(self): calls.append(('close',))
    stop = SimpleNamespace(set=lambda: calls.append(('stop',)))
    result = scheduler.stop_child(Child(),stop)
    assert calls == [('stop',),('join',2),('terminate',),('join',1),('kill',),('join',1),('close',)]
    assert result['confirmed_exited'] is True and result['escalation']==['terminate','kill']


def test_unconfirmed_process_exit_remains_failure_state():
    child = SimpleNamespace(join=lambda timeout:None,is_alive=lambda:True,
        terminate=lambda:None,kill=lambda:None,exitcode=None)
    result = scheduler.stop_child(child,SimpleNamespace(set=lambda:None))
    assert result['confirmed_exited'] is False and result['exitcode'] is None
