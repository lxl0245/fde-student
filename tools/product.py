#!/usr/bin/env python3
"""W1 product entry point. Credentials are read only from explicit environment."""
import argparse,json,sys,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'platform'))
from fde_product.business import import_sample

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['init','mock','run','serve','start']);p.add_argument('--home',default=str(ROOT/'.runtime/product'));p.add_argument('--port',type=int);p.add_argument('--text');p.add_argument('--session');p.add_argument('--actions',action='store_true');p.add_argument('--confirm-hash');p.add_argument('--reject',action='store_true')
    args=p.parse_args();home=Path(args.home);mock=None;mock_thread=None
    if args.command=='init':print(json.dumps(import_sample(ROOT/'sample_data',home/'sample.sqlite')));return
    if args.command=='mock':
        from fde_product.tag_mock import make_server
        server=make_server(home/'tag-system.sqlite',args.port if args.port is not None else 8766,os.getenv('FDE_MOCK_TOKEN','training-demo-token'))
        print('Sample tag API: http://127.0.0.1:'+str(server.server_port),flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:server.server_close()
        return
    if args.command=='start':
        from fde_product.tag_mock import make_server
        import threading
        if not (home/'sample.sqlite').exists():import_sample(ROOT/'sample_data',home/'sample.sqlite')
        mock=make_server(home/'tag-system.sqlite',0,os.getenv('FDE_MOCK_TOKEN','training-demo-token'))
        mock_thread=threading.Thread(target=mock.serve_forever,daemon=True);mock_thread.start()
        print('Sample tag API: http://127.0.0.1:'+str(mock.server_port),flush=True)
    from fde_product.agent import Agent
    from fde_product.models import CompatibleModel,ModelError
    from fde_product.actions import ActionError
    try:
        model=CompatibleModel(timeout=90)
        agent=Agent(home,model,mock_url=('http://127.0.0.1:'+str(mock.server_port)) if mock else os.getenv('FDE_MOCK_URL','http://127.0.0.1:8766'),mock_token=os.getenv('FDE_MOCK_TOKEN','training-demo-token'),role='operator' if args.actions else 'analyst')
        if args.command in ('serve','start'):
            from fde_product.web import serve
            serve(agent,args.port if args.port is not None else 8765,args.actions)
        else:
            resume={'approved':not args.reject,'draft_hash':args.confirm_hash} if args.confirm_hash else None
            print(json.dumps(agent.run(args.text,args.session,args.actions,resume),ensure_ascii=False,indent=2))
    except ModelError as e:raise SystemExit(e.code)
    except ActionError as e:raise SystemExit(e.code)
    finally:
        if 'agent' in locals():agent.close()
        if mock:mock.shutdown();mock.server_close();mock_thread.join()
if __name__=='__main__':main()
