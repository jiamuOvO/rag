"""Interactive credentials remain in memory; no key is printed, logged, or written."""
import argparse
import getpass
import sys
from dataclasses import replace
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1] / 'src'))


def main():
    from rag.config import Settings
    from evaluate import run_isolated
    parser = argparse.ArgumentParser()
    parser.parse_args()
    settings = Settings.load()
    if not settings.embedding_api_key:
        settings = replace(settings, embedding_api_key=getpass.getpass('Embedding API key (hidden; not saved): '))
    if not settings.embedding_api_key:
        raise SystemExit('No credential supplied.')
    run_isolated(argparse.Namespace(offline_bm25=False), settings_override=settings)


if __name__ == '__main__':
    main()
