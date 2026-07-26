import json, pathlib, urllib.request
paths=[pathlib.Path.home()/'.hermes/profiles/default/paperclip-credentials.json',pathlib.Path.home()/'.hermes/paperclip-credentials.json']
p=next(x for x in paths if x.exists()); d=json.loads(p.read_text())
key=d.get('PAPERCLIP_BOARD_API_KEY') or d.get('PAPERCLIP_API_KEY') or d.get('apiKey')
base='http://127.0.0.1:3100'
h={'Authorization':'Bearer'+' '+key,'Accept':'application/json'}
def get(path):
 r=urllib.request.Request(base+path,headers=h)
 with urllib.request.urlopen(r,timeout=30) as x:return json.loads(x.read().decode())

i=get('/api/issues/NFM-1763')
print(json.dumps({k:i.get(k) for k in ['id','identifier','title','status','priority','assigneeAgentId','assigneeUserId','projectId','executionWorkspaceId','executionState','createdAt','updatedAt']}, ensure_ascii=False,indent=2))
print('\n--- description (3000 chars) ---')
print(str(i.get('description') or '')[:3000])
print('\n--- recent activity ---')
act=get('/api/issues/NFM-1763/activity')
if isinstance(act,list):
 for a in act[-15:]:
  print(a.get('createdAt'),'|',a.get('action'),'|',str(a.get('details',''))[:300].replace('\n',' '))
