"""Summarize paired, externally recorded logo replay exports. No inference."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.uniform_logo_evaluation import compare_logo_runs
from core.uniform_model_registry import read_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--torso-only', type=Path, required=True)
    parser.add_argument('--torso-plus-logo', type=Path, required=True)
    parser.add_argument('--criteria', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New JSON file; existing files are never overwritten')
    args = parser.parse_args(argv)
    report = compare_logo_runs(read_json(args.manifest), read_json(args.torso_only),
                              read_json(args.torso_plus_logo), criteria=read_json(args.criteria))
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print('Comparison saved; this report does not authorize enablement or activation.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
