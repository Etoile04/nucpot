import json, pathlib, urllib.request, urllib.parse, urllib.error
paths=[pathlib.Path.home()/'.hermes/profiles/default/paperclip-credentials.json',pathlib.Path.home()/'.hermes/paperclip-credentials.json']
p=next(x for x in paths if x.exists()); d=json.loads(p.read_text())
key=d.get('PAPERCLIP_BOARD_API_KEY') or d.get('PAPERCLIP_API_KEY') or d.get('apiKey'); base='http://127.0.0.1:3101'
h={'Authorization':'Bearer'+' '+key,'Accept':'application/json'}
def get(path):
 r=urllib.request.Request(base+path,headers=h)
 try:
  with urllib.request.urlopen(r,timeout=30) as x:return x.status,json.loads(x.read().decode())
 except urllib.error.HTTPError as e: return e.code,{}

# Get all NFM-561 + parent epic + description
s,x=get('/api/issues/NFM-561')
print('NFM-561 description first 2000 chars:')
print(str(x.get('description',''))[:2000])
print()
# List comments to find file references
s,comments=get('/api/issues/NFM-561/comments')
if isinstance(comments,list):
 for c in comments[-10:]:
  body=str(c.get('body') or '')
  if any(w in body for w in ['附件','.docx','.pdf','项目申报书','上传','提交','合稿']):
   print('--- comment', c.get('createdAt'),'---')
   print(body[:1500])
   print()
