"""Check the revised DOCX coverage and identify changed rendered pages for QA."""
from pathlib import Path
from hashlib import sha256
import re
from PIL import Image
from docx import Document

root = Path(__file__).resolve().parents[1]
qa = root / '.docx-code-walkthrough-revision-qa'
doc = Document(root/'docs/FactoryPilot代码执行链路与评测面试手册_修订版.docx')
text = '\n'.join(p.text for p in doc.paragraphs)
nodes = ['triage', 'mes_data', 'alarm_data', 'maintenance_data', 'knowledge_data',
         'statistical_analysis', 'graph_knowledge', 'vendor_expert', 'rca', 'plan',
         'approval', 'execute', 'reject', 'verify', 'report']
assert all(f'{i:02} {n}' in text for i,n in enumerate(nodes,1))
locations = re.findall(r'源码定位  (.+):(\d+)', text)
for path,line in locations:
    source = (root/path).read_text(encoding='utf-8').splitlines()
    assert 0 < int(line) <= len(source), (path,line)
assert len(doc.inline_shapes) == 2
print(f'DOCX: {len(nodes)} nodes, {len(locations)} source anchors, {len(doc.inline_shapes)} diagrams')

def body_hash(path):
    with Image.open(path) as im:
        return sha256(im.crop((0,0,im.width,1150)).tobytes()).hexdigest()

previous = {body_hash(p):p.name for pattern in ['final-page-*.png','verified-page-*.png'] for p in qa.glob(pattern)}
for path in sorted(qa.glob('release-page-*.png')):
    match = previous.get(body_hash(path))
    print(path.name, 'UNCHANGED '+match if match else 'REVIEW')
