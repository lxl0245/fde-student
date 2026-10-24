#!/usr/bin/env python3
import subprocess,os,tempfile,threading,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'platform'))
from fde_product.tag_mock import make_server
s=make_server(Path(tempfile.mkdtemp(prefix='fde-custom-api-'))/'labels.sqlite',0);t=threading.Thread(target=s.serve_forever,daemon=True);t.start()
try:
 env=os.environ.copy();env['FDE_MOCK_URL']='http://127.0.0.1:'+str(s.server_port);(ROOT/'artifacts/w01').mkdir(parents=True,exist_ok=True)
 code=subprocess.run(['node',str(ROOT/'tools/probe_custom_contract.mjs')],env=env).returncode
finally:s.shutdown();s.server_close();t.join()
raise SystemExit(code)
