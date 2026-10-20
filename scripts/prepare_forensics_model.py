"""Verify a locally acquired TruFor checkpoint and emit an auditable receipt.

An expected SHA-256 from a trusted distribution channel is required. This tool
never loads pickle, fetches unverified mirrors or modifies the app configuration.
"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.forensics_model import file_hash,source_hash,SOURCE_REVISION,SOURCE_SHA256

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True,help='Pinned upstream test_docker/src directory')
    parser.add_argument('--weights',type=Path,required=True)
    parser.add_argument('--expected-sha256',required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args()
    actual=file_hash(args.weights)
    if actual != args.expected_sha256.lower():parser.error('Checkpoint SHA-256 does not match the expected value')
    if source_hash(args.source)!=SOURCE_SHA256:parser.error('Inference source differs from the reviewed source bundle')
    receipt={'model':'TruFor','source_revision':SOURCE_REVISION,'source_sha256':SOURCE_SHA256,'weights_sha256':actual,
             'weights_bytes':args.weights.stat().st_size,'mode':'experimental','calibrated':False,
             'license':'GRIP-UNINA informational/nonprofit terms; see upstream LICENSE.txt and CMX notices',
             'production_threshold':None,'evaluation_required':True}
    args.receipt.write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2))
if __name__=='__main__':main()
