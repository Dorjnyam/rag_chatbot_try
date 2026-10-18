"""A second client in the same process does not test durable HNSW search."""
import subprocess
import sys


def test_small_collection_search_survives_process_exit(tmp_path):
    script = '''
import sys
from flask import Flask
from app.services.vectorstore import VectorStore
app = Flask(__name__)
app.config['DATA_DIR'] = sys.argv[1]
with app.app_context():
    store = VectorStore()
    if sys.argv[2] == 'write':
        store.upsert('restart_test', [dict(id='chunk', text='policy', metadata={'version_id': 'v1'})], [[1., 0., 0.]])
    else:
        hits = store.query('restart_test', [1., 0., 0.], ['v1'], 1)
        assert [hit['id'] for hit in hits] == ['chunk']
        assert abs(hits[0]['distance']) < 0.00001
'''
    for operation in ('write', 'read', 'read'):
        subprocess.run([sys.executable, '-c', script, str(tmp_path), operation], check=True, capture_output=True, timeout=45)
