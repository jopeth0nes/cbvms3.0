"""Run the isolated protocol-v2 D1/R2 authentication and synchronization suite."""
from pathlib import Path
import subprocess

def main():
    subprocess.run(['node', 'tests/sync.mjs'], cwd=Path(__file__).resolve().parents[1] / 'sites/student-portal', check=True)

if __name__ == '__main__':
    main()
