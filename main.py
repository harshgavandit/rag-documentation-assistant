"""RAG CLI: compare, ingest, query, check, or run the full demo."""
import argparse
import json
import sys


def main() -> int:
    """Dispatch commands and return a nonzero exit code on validation/service errors."""
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command')
    commands.add_parser('compare', help='Compare chunking strategies without API calls.')
    ingest = commands.add_parser('ingest', help='Embed documents and upsert into ChromaDB.')
    ingest.add_argument('--strategy', choices=['semantic', 'character'], default='semantic')
    query = commands.add_parser('query', help='Run a Redis-limited, grounded RAG query.')
    query.add_argument('question')
    query.add_argument('--strategy', choices=['semantic', 'character'], default='semantic')
    check = commands.add_parser('check', help='Report configuration and storage readiness.')
    check.add_argument('--live', action='store_true', help='Also ping Redis and check model access.')
    commands.add_parser('demo', help='Compare, ingest semantic chunks, then answer one question.')
    args = parser.parse_args()
    try:
        if args.command == 'check':
            from pipeline.health import check_health
            report = check_health(args.live)
            print(json.dumps(report, indent=2))
            return 0 if report['status'] == 'PASS' else 1
        if args.command in ('query', 'ingest') and args.strategy == 'character':
            from config import settings
            settings.COLLECTION_NAME = settings.CHAR_COLLECTION_NAME
        if args.command == 'compare':
            from demo.chunking_comparison import main as compare
            compare()
        elif args.command == 'ingest':
            if args.strategy == 'character':
                from demo.ingest_char import main as ingest_docs
            else:
                from demo.ingest_structure_based import main as ingest_docs
            ingest_docs()
        elif args.command == 'query':
            from demo.user_query import main_with_query
            main_with_query(args.question)
        else:
            from demo.chunking_comparison import main as compare
            from demo.ingest_structure_based import main as ingest_docs
            from demo.user_query import main_with_query
            compare()
            ingest_docs()
            main_with_query('How does FastMCP register a tool with the MCP server?')
        return 0
    except (RuntimeError, ValueError, OSError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
