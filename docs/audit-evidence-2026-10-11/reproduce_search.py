import json
import os
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
DATA=Path(tempfile.mkdtemp(prefix='mavris-review-search-'))
os.environ.update({'LENGRVIS_DATA_DIR':str(DATA/'data'),'LENGRVIS_CONFIG_FILE':str(DATA/'absent.yaml'),'LENGRVIS_ENV_FILE':str(DATA/'absent.env'),'LENGRVIS_TEST':'1','LENGRVIS_AUDIT_HMAC_SECRET':'local-review-test-secret'})
sys.path.insert(0,str(ROOT/'backend'))
from app.core import db
from app.indexer.fts_index import FTSIndex
from app.indexer.vector_index import VectorIndex

workspace=DATA/'workspace'
workspace.mkdir()
marker='azimuthreceipt'
path=workspace/'handbook.txt'
path.write_text('\n\n'.join([('Routine introduction ' * 60)[:1100] for _ in range(100)]+[f'{marker}: the unique answer is chapter 101.']),encoding='utf-8')
embed=lambda texts:[[1.0,0.0] if marker in text else [0.0,1.0] for text in texts]
index=FTSIndex(embedder=embed)
index.index_file(path,[str(workspace)])
vector=VectorIndex(embedder=embed)
candidates=vector._candidate_chunks(marker,80)
result=vector.search(marker,allowed_directories=[str(workspace)])
with db.connect() as conn:
    actual=[dict(row) for row in conn.execute('SELECT chunk_index FROM document_chunks WHERE text LIKE ?',('%'+marker+'%',)).fetchall()]
print(json.dumps({'matching_chunks_in_index':actual,'candidate_count':len(candidates),'matching_chunks_in_candidates':[r['chunk_index'] for r in candidates if marker in r['text']],'semantic_result':result,'fts_result_count':len(index.search(marker,allowed_directories=[str(workspace)]))},ensure_ascii=False,indent=2))
