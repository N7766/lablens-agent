"""Validate an experiment JSON array and write normalized runs.json to a data directory."""
import argparse
import json
from pathlib import Path
from lablens.importing import validate_runs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    runs = validate_runs(json.loads(args.input.read_text()))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'runs.json').write_text(json.dumps(runs, ensure_ascii=False, indent=2) + '\n')
    # Generic datasets must not inherit the DPO-specific methodology notes.
    if not (args.output / 'notes.json').exists():
        (args.output / 'notes.json').write_text('[]\n')
    print(f'Validated and imported {len(runs)} runs')


if __name__ == '__main__':
    main()
