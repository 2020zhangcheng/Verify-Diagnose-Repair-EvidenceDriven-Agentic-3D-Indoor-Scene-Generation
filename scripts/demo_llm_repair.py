"""Invoke the running API using your configured model; no mocked provider."""
import argparse
import json
from pathlib import Path
from uuid import uuid4
import httpx
from app.config import settings


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--api',default='http://127.0.0.1:8000')
    parser.add_argument('--input',default='configs/geometry-diagnosis-demo.json')
    parser.add_argument('--max-iterations',type=int,default=5)
    parser.add_argument('--idempotency-key',default=None)
    parser.add_argument('--output',default=None)
    args=parser.parse_args()
    key=args.idempotency_key or str(uuid4())
    headers={'Authorization':f'Bearer {settings.demo_token}','Idempotency-Key':key}
    body={'scene':json.loads(Path(args.input).read_text()),'max_iterations':args.max_iterations}
    with httpx.Client(timeout=1800,follow_redirects=False) as client:
        response=client.post(args.api.rstrip('/')+'/geometry/repair',headers=headers,json=body)
    result=response.json()
    output=Path(args.output or f'outputs/llm-repair/{key}/response.json')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2))
    print(json.dumps({'http_status':response.status_code,'run_id':result.get('run_id'),'status':result.get('status'),
        'error':result.get('error'),'idempotency_key':key,'output':str(output.resolve())},indent=2))
    if response.is_error:
        raise SystemExit(1)


if __name__=='__main__':
    main()
