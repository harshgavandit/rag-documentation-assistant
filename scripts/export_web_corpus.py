"""Export the shared Markdown chunker output for the read-only hosted corpus."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from chunking.semantic_splitter import split_by_headers
root=Path(__file__).resolve().parents[1]
chunks=[]
for path in sorted((root/'docs').glob('*.md')):
    chunks.extend({'content':c.content,'metadata':c.metadata} for c in split_by_headers(path.read_text(encoding='utf-8'),path.name,400))
(root/'web/data/corpus.json').write_text(json.dumps(chunks,ensure_ascii=False),encoding='utf-8')
print(f'Exported {len(chunks)} chunks')
