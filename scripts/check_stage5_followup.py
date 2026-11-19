#!/usr/bin/env python3
"""Focused regressions before the final healthy hourly soak: SQL outage and expiry alert."""
import base64
import collections
import concurrent.futures
import json
import time
import stage5_common as c
from stage5_common import api


def run():
    report={"project":c.PROJECT,"scope":"exception classification and expiry alert after diagnostic run; separate from hourly load"}
    c.wait("all APIs ready",lambda:all(api.request('/readyz',port=p)[0]==200 for p in (18080,18081,18082)))
    bad={"Authorization":"Basic "+base64.b64encode(b'buyer-1:wrong-password').decode()}
    assert api.request('/api/cart',headers=bad)[0]==401
    assert api.request('/api/cart')[0]==401
    report['healthy_bad_credentials_status']=401
    order=api.order(92,9912)
    results=[]
    api.command('stop','postgres')
    try:
        def probe(n):
            private=n%2==1
            status,body=api.request('/api/cart' if private else '/api/products?categoryId=1&limit=1',1 if private else None,port=18081 if n%4<2 else 18082)
            return ('private' if private else 'public',status,body.get('code') if isinstance(body,dict) else None)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(probe,range(64)))
        report['database_outage']={kind:{'statuses':dict(collections.Counter(str(status) for k,status,_ in results if k==kind)),'codes':dict(collections.Counter(code for k,_,code in results if k==kind))} for kind in ('public','private')}
        assert all(status==503 for _,status,_ in results),results
        assert {code for kind,_,code in results if kind=='public'}=={'DATABASE_UNAVAILABLE'}
        assert {code for kind,_,code in results if kind=='private'}=={'AUTHENTICATION_UNAVAILABLE'}
        assert api.request('/api/cart',port=18081)[0]==401
    finally:
        api.command('start','postgres')
        c.wait('database recovered',lambda:api.request('/readyz')[0]==200)
    assert api.ok('/api/orders/'+order,92)['id']==order
    assert api.request('/api/cart',headers=bad)[0]==401
    report['healthy_auth_restored']=True
    api.command('stop','worker-1','worker-2')
    try:
        api.sql(f"UPDATE orders SET expires_at=now()-interval '20 seconds' WHERE id='{order}' AND status='PENDING_PAYMENT'")
        started=time.monotonic()
        c.wait('overdue reservation metric',lambda:bool(c.prom('max(ecom_expired_reservations) > 0')))
        c.wait('expiry alert',lambda:c.alert_firing('ReservationExpiryDelayed'),100)
        report['expiry_alert_detection_seconds']=round(time.monotonic()-started,3)
    finally:
        api.command('start','worker-1','worker-2')
    c.wait('reservation released',lambda:api.sql(f"SELECT status FROM orders WHERE id='{order}'")=='EXPIRED')
    c.wait('expiry alert resolves',lambda:not c.alert_firing('ReservationExpiryDelayed'))
    report['expiry_alert_resolved']=True
    c.wait('outbox drained',lambda:api.sql('SELECT count(*) FROM outbox_events WHERE published_at IS NULL')=='0')
    report['invariant_violations']=c.invariants()
    assert report['invariant_violations']==0
    report['passed']=True
    (c.ROOT/'docs/stage5_followup_results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':run()
