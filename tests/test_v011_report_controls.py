"""Predeclared delivery controls, authored separately from the report renderer."""
import copy
from pathlib import Path
import socket
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.evaluate_reports import (
    FORMATS, check_export, control_receipt, fixture, historical_receipt, run_control,
)
from lablens.reporting import export_report


MATRIX = fixture()


@pytest.fixture(autouse=True)
def no_network_or_model(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Report checks must not call a model or connect to a network')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr('lablens.model.HTTPModel.__init__', forbidden)


@pytest.mark.parametrize('case', MATRIX['cases'], ids=lambda case: case['id'])
@pytest.mark.parametrize('format', FORMATS)
def test_predeclared_report_control(case, format):
    run_control(case, format, export_report)


def test_every_declared_historical_receipt_remains_complete_and_readable():
    assert len(MATRIX['historical_receipts']) == 166
    assert sum(row['historical_contract_pass'] is False for row in MATRIX['historical_receipts']) == 5
    for entry in MATRIX['historical_receipts']:
        receipt = historical_receipt(entry)
        before = copy.deepcopy(receipt)
        for format in FORMATS:
            check_export(receipt, export_report(receipt, format), format)
        assert receipt == before


def test_receipt_is_self_contained_and_does_not_read_source_labels(monkeypatch):
    # Receipt creation is allowed to read local fixture code; delivery itself must be portable.
    receipt = control_receipt(next(case for case in MATRIX['cases'] if case['id'] == 'hostile-text'))
    def forbidden(*args, **kwargs):
        raise AssertionError('Export tried to read a current dataset or provenance path')
    monkeypatch.setattr('pathlib.Path.read_text', forbidden)
    monkeypatch.setattr('pathlib.Path.read_bytes', forbidden)
    for format in FORMATS:
        check_export(receipt, export_report(receipt, format), format)


def test_unknown_output_format_is_rejected():
    receipt = control_receipt(MATRIX['cases'][0])
    with pytest.raises(ValueError):
        export_report(receipt, 'pdf')
