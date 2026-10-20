"""Download pinned public embedding artifacts at build time, never during queries."""
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parent
MODEL='sentence-transformers/all-MiniLM-L6-v2'


def prepare() -> None:
    """Fetch the model/tokenizer with immutable revision and verified artifact hashes."""
    manifest=json.loads((ROOT/'model_manifest.json').read_text(encoding='utf-8-sig'))
    folder=ROOT/'models'
    folder.mkdir(exist_ok=True)
    for name, info in manifest['files'].items():
        path=folder/name
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=info['sha256']:
            with urlopen(f"https://huggingface.co/{MODEL}/resolve/{manifest['revision']}/{info['path']}",timeout=120) as response:
                content=response.read()
            if hashlib.sha256(content).hexdigest()!=info['sha256']:
                raise RuntimeError('Embedding artifact checksum mismatch: '+name)
            path.write_bytes(content)
    print('Pinned embedding artifacts ready.')


if __name__=='__main__':
    prepare()
