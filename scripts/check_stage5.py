#!/usr/bin/env python3
"""Metrics, tracing, admission/retention and observed dependency failures under paced read traffic."""
import collections
import concurrent.futures
import json
import threading
import time
import uuid
import stage5_common as c
from stage5_common import api
import init_stage4
import load_stage4


class Traffic:
    def __init__(self):
        self.stop=threading.Event(); self.phase="baseline"; self.data=collections.defaultdict(list); self.lock=threading.Lock(); self.dropped=0
        self.thread=threading.Thread(target=self.run,daemon=True)
    def run(self):
        futures=set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
            next_at=time.monotonic(); n=0
            while not self.stop.is_set():
                futures={f for f in futures if not f.done()}
                if len(futures)<64:
                    futures.add(pool.submit(self.one,self.phase,"search" if n%6==0 else "catalog"))
                else:self.dropped+=1
                n+=1;next_at+=1/120
                self.stop.wait(max(0,next_at-time.monotonic()))
    def one(self,phase,kind):
        started=time.monotonic()
        try:status=api.request("/api/search?q=product&limit=1" if kind=="search" else "/api/products/1")[0]
        except OSError:status=0
        with self.lock:self.data[(phase,kind)].append((status,time.monotonic()-started))
    def summarize(self):
        with self.lock:
            return {phase+"/"+kind:{"requests":len(rows),"status":dict(collections.Counter(str(r[0]) for r in rows)),"max_seconds":round(max(r[1] for r in rows),3)} for (phase,kind),rows in self.data.items()}


def crash_durability():
    c.wait("baseline ready",lambda:api.request("/readyz")[0]==200)
    # Crash recovery of a confirmed operation, distinct from graceful restart above.
    before=int(api.sql("SELECT on_hand FROM inventory WHERE product_id=9911"))
    cart=api.ok("/api/cart/items/9911",91,"PUT",{"quantity":1})
    key=uuid.uuid4().hex;body={"expectedCartVersion":cart["version"],"expectedTotalMinor":cart["totalMinor"]}
    confirmed=api.ok("/api/orders",91,"POST",body,headers={"Idempotency-Key":key})["orderId"]
    p=api.pay(91,confirmed)["paymentId"]
    api.provider("/demo/payments/"+p+"/state","POST",{"state":"SUCCEEDED"})
    c.wait("confirmed paid",lambda:api.sql(f"SELECT status FROM orders WHERE id='{confirmed}'")=="PAID")
    durability=api.sql("SELECT current_setting('fsync')||':'||current_setting('synchronous_commit')")
    api.command("kill","-s","SIGKILL","postgres")
    c.wait("crash alert",lambda:c.alert_firing("DatabaseUnavailable"),90)
    recovery_start=time.monotonic();api.command("start","postgres")
    c.wait("crash recovered",lambda:api.request("/readyz")[0]==200)
    assert api.ok("/api/orders",91,"POST",body,headers={"Idempotency-Key":key})["orderId"]==confirmed
    assert api.ok("/api/orders/"+confirmed,91)["status"]=="PAID"
    assert api.sql("SELECT on_hand||':'||reserved FROM inventory WHERE product_id=9911")==str(before-1)+":0"
    return {"settings":durability,"confirmed_paid_order_survived":True,"idempotent_replay":True,"restart_to_ready_seconds":round(time.monotonic()-recovery_start,3)}


def run():
    report={"project":c.PROJECT,"checks":{},"failures":{}}
    def check(name,value=True):
        report["checks"][name]=value;print(name,json.dumps(value),flush=True)
        c.save("checks-live.json",report)
    c.wait("API/search readiness",lambda:all(api.request("/readyz",port=p)[0]==200 for p in (18080,18081,18082,18085)),240)
    init_stage4.COMPOSE=c.COMPOSE;init_stage4.initialize()
    c.wait("scrapes up",lambda:len(c.prom("up"))==17 and not c.prom("up == 0"),180)
    c.wait("canary success",lambda:bool(c.prom("ecom_search_canary_last_success_unixtime > 0")),120)
    targets=api.ok("/api/v1/targets",port=19090)["data"]["activeTargets"]
    check("scrapes_up",len(targets))
    dashboard=api.ok("/api/dashboards/uid/ecom-stage5",port=13000,headers={"Authorization":"Basic YWRtaW46bG9jYWwtZ3JhZmFuYS1wYXNzd29yZA=="})
    assert len(dashboard["dashboard"]["panels"])>=15
    assert api.request("/actuator/prometheus")[0]==401
    check("dashboard_and_private_scrape_ports")
    for query in ["pg_up","redis_up","rabbitmq_queue_messages","ecom_outbox_pending","haproxy_backend_http_responses_total","pg_stat_archiver_archived_count"]:assert c.prom(query),query
    check("infrastructure_metrics_present")

    trace=uuid.uuid4().hex
    cart=api.ok("/api/cart/items/661",61,"PUT",{"quantity":1})
    key=uuid.uuid4().hex;body={"expectedCartVersion":cart["version"],"expectedTotalMinor":cart["totalMinor"]}
    oid=api.ok("/api/orders",61,"POST",body,headers={"Idempotency-Key":key,"traceparent":"00-"+trace+"-0123456789abcdef-01"})["orderId"]
    def traced():
        status,data=api.request("/api/traces/"+trace,port=13200,headers={"Accept":"application/json"})
        return status==200 and "consume.notifications" in json.dumps(data)
    c.wait("async trace visible",traced,90)
    check("trace_through_outbox_and_rabbit",{"trace_id":trace})

    api.command("stop","worker-1","worker-2")
    fake=str(uuid.uuid4())
    api.sql(f"INSERT INTO outbox_events(event_id,aggregate_type,aggregate_id,aggregate_version,event_type,payload,created_at,lease_until) VALUES('{fake}','Product','661',99999,'ProductUpdated','{{}}',now()-interval '601 seconds',now()+interval '1 day');")
    cart=api.ok("/api/cart/items/662",62,"PUT",{"quantity":1})
    c.wait("admission blocks",lambda:api.request("/api/orders",62,"POST",{"expectedCartVersion":cart["version"],"expectedTotalMinor":cart["totalMinor"]},headers={"Idempotency-Key":"blocked-probe"})[0]==503)
    assert api.ok("/api/orders",61,"POST",body,headers={"Idempotency-Key":key})["orderId"]==oid
    # Payment callbacks and release remain available even when new orders are refused.
    payment=api.pay(61,oid)["paymentId"]
    api.provider("/demo/payments/"+payment+"/state","POST",{"state":"SUCCEEDED"})
    c.wait("payment accepted during admission",lambda:api.sql(f"SELECT status FROM orders WHERE id='{oid}'")=="PAID")
    api.sql(f"DELETE FROM outbox_events WHERE event_id='{fake}';")
    api.command("start","worker-1","worker-2")
    check("admission_replay_and_critical_payment")

    expired=str(uuid.uuid4());pending=str(uuid.uuid4())
    api.sql(f"INSERT INTO outbox_events(event_id,aggregate_type,aggregate_id,aggregate_version,event_type,payload,published_at) VALUES('{expired}','Product','662',88888,'ProductUpdated','{{}}',now()-interval '8 days'); INSERT INTO consumer_inbox VALUES('retention-check','{expired}',now()-interval '8 days'); INSERT INTO outbox_events(event_id,aggregate_type,aggregate_id,aggregate_version,event_type,payload,created_at,lease_until) VALUES('{pending}','Product','662',88889,'ProductUpdated','{{}}',now()-interval '8 days',now()+interval '1 day');")
    c.wait("old published/inbox removed",lambda:api.sql(f"SELECT (SELECT count(*) FROM outbox_events WHERE event_id='{expired}')+(SELECT count(*) FROM consumer_inbox WHERE event_id='{expired}')")=="0",100)
    assert api.sql(f"SELECT count(*) FROM outbox_events WHERE event_id='{pending}'")=="1"
    api.sql(f"DELETE FROM outbox_events WHERE event_id='{pending}';")
    check("retention_keeps_unpublished")
    c.wait("admission recovers",lambda:api.request("/api/orders",62,"POST",{"expectedCartVersion":cart["version"],"expectedTotalMinor":cart["totalMinor"]},headers={"Idempotency-Key":"blocked-probe"})[0]==201)

    image_key,_=load_stage4.upload_fixture()
    api.ok("/"+image_key,port=18086)
    traffic=Traffic();traffic.thread.start()
    try:
        for service,alert in [("commerce-1","TargetDown"),("redis","RedisUnavailable"),("rabbitmq","OutboxLag"),("opensearch","EndpointUnavailable"),("postgres","DatabaseUnavailable"),("garage","TargetDown"),("provider","TargetDown")]:
            traffic.phase=service
            print("Stopping",service,flush=True)
            start=time.monotonic();api.command("stop",service)
            if service=="rabbitmq":api.order(71,771)
            if service=="opensearch":
                api.order(72,772)
                assert api.request("/api/search",port=18085)[0]==503
            if service=="garage":
                assert api.ok("/"+image_key,port=18086)
                assert api.request("/"+image_key+"?miss="+uuid.uuid4().hex,port=18086)[0] in (502,504)
                boundary=uuid.uuid4().hex
                body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="test.png"\r\nContent-Type: image/png\r\n\r\n').encode()+load_stage4.png()+f"\r\n--{boundary}--\r\n".encode()
                status,response=api.request("/api/admin/products/99991/images","admin","POST",body,headers={"Content-Type":"multipart/form-data; boundary="+boundary})
                assert status==503 and response["code"]=="MEDIA_UNAVAILABLE"
            if service=="provider":
                oid2=api.order(73,773); assert api.pay(73,oid2)["state"]=="PENDING"
            instances={"commerce-1":"commerce-1:9091","redis":"redis-exporter:9121","opensearch":"http://search:8080/readyz","postgres":"pg-exporter:9187","garage":"garage:3903","provider":"provider:9091"}
            selector='ALERTS{alertstate="firing",alertname="'+alert+'"'+(',instance="'+instances[service]+'"' if service in instances else '')+'}'
            c.wait(alert+" firing",lambda:bool(c.prom(selector)),150)
            alert_seconds=round(time.monotonic()-start,3)
            restart=time.monotonic();api.command("start",service)
            if service in ("commerce-1","postgres"):c.wait("SQL/API recovered",lambda:api.request("/readyz",port=18081 if service=="commerce-1" else 18080)[0]==200)
            if service=="opensearch":
                c.wait("search recovered",lambda:api.request("/readyz",port=18085)[0]==200)
                api.ok("/api/admin/queues/search/replay","admin","POST")
            if service=="provider":
                c.wait("provider session recreated",lambda:len(api.provider("/payments/"+api.ok("/api/orders/"+oid2+"/payments",73)["paymentId"]))>0)
            c.wait("dependency scrapes restored",lambda:not c.prom("up == 0"))
            c.wait("HTTP probes restored",lambda:not c.prom("probe_success == 0"))
            for queue in ("search","notifications"):api.ok("/api/admin/queues/"+queue+"/replay","admin","POST")
            c.wait("outbox drained",lambda:api.sql("SELECT count(*) FROM outbox_events WHERE published_at IS NULL")=="0")
            report["failures"][service]={"alert":alert,"alert_seconds":alert_seconds,"restart_to_ready_or_drain_seconds":round(time.monotonic()-restart,3)}
            check("failure_"+service,report["failures"][service])
            traffic.phase="recovered"
        assert c.invariants()==0
        check("inventory_order_invariants",0)
    finally:
        traffic.stop.set();traffic.thread.join(timeout=30)
        api.command("start","commerce-1","redis","rabbitmq","opensearch","postgres","garage","provider","worker-1","worker-2")
    check("postgres_crash_durability",crash_durability())
    report["read_traffic"]=traffic.summarize();report["generator_dropped"]=traffic.dropped
    assert all(status==200 or status>=500 or status==0 for rows in traffic.data.values() for status,_ in rows), "Unexpected 4xx during public read fault test"
    report["passed"]=True
    (c.ROOT/"docs/stage5_checks.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print("Stage 5 fault checks passed",flush=True)


if __name__=="__main__":run()
