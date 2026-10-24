"""Prepare an offline additive campus import bundle; never connects to cloud."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.portal_import import prepare

if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--campus-backup', type=Path, required=True)
    parser.add_argument('--cloud-restore', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--import-id', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.campus_backup, args.cloud_restore, args.output_dir, args.import_id)))
