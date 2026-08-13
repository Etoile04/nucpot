import json, pathlib, urllib.request
paths=[pathlib.Path.home()/'.hermes/profiles/default/paperclip-credentials.json',pathlib.Path.home()/'.hermes/paperclip-credentials.json']
p=next(x for x in paths if x.exists()); d=json.loads(p.read_text())
key=d.get('PAPERCLIP_BOARD_API_KEY') or d.get('PAPERCLIP_API_KEY') or d.get('apiKey')
for url in ['http://127.0.0.1:3101/api/health','http://127.0.0.1:3100/__cg_health','http://127.0.0.1:3101/api/cli-auth/me','http://127.0.0.1:3100/api/cli-auth/me']:
  try:
    r=urllib.request.Request(url, headers={'Authorization':'Bearer '+key,'Accept':'application/json'})
    with urllib.request.urlopen(r,timeout=10) as x:
      print(f'  {url} -> {x.status} | {x.read().decode()[:150]}')
  except urllib.error.HTTPError as e:
    print(f'  {url} -> HTTPError {e.code}')
  except Exception as e:
    print(f'  {url} -> {repr(e)[:100]}')
