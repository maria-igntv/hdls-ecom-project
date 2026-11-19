#!/usr/bin/env python3
"""Physical base backup + named WAL point + immutable S3 backup, restored in an isolated stack."""
import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
import stage5_common as c
from stage5_common import api
import init_stage4
import load_stage4
import s3_stage5


def fingerprint(sql=api.sql):
    return json.loads(sql("""
        SELECT json_build_object(
          'inventory',(SELECT md5(string_agg(product_id||':'||on_hand||':'||reserved,'|' ORDER BY product_id)) FROM inventory),
          'orders',(SELECT md5(string_agg(id||':'||user_id||':'||status||':'||total_minor,'|' ORDER BY id)) FROM orders),
          'payments',(SELECT md5(string_agg(id||':'||state||':'||amount_minor,'|' ORDER BY id)) FROM payments),
          'images',(SELECT md5(string_agg(product_id||':'||object_key,'|' ORDER BY product_id,object_key)) FROM product_images),
          'provider_payments',(SELECT md5(string_agg(id||':'||state||':'||amount_minor,'|' ORDER BY id)) FROM provider.payments),
          'outbox',(SELECT count(*) FROM outbox_events),
          'items',(SELECT count(*) FROM order_items));
        """))


def run():
    token=uuid.uuid4().hex[:10]
    folder=c.OUTPUT/"backups"/token
    folder.mkdir(parents=True)
    base="/tmp/ecom-base-"+token
    restorepoint="ecom_"+token
    recovery_project="hdls-recovery-"+token
    recipe=folder/"compose.json"
    recovery=["docker","compose","-p",recovery_project,"-f",str(recipe)]
    writers=("commerce-1","commerce-2","worker-1","worker-2","provider")
    report={"project":c.PROJECT,"recovery_project":recovery_project,"restore_point":restorepoint}
    started=time.monotonic()
    api.command("exec","-T","--user","postgres","postgres","pg_basebackup","-D",base,"-Fp","-X","stream","-c","fast","-U","commerce")
    api.command("exec","-T","--user","postgres","postgres","pg_verifybackup",base)
    report["base_backup_verified"]=True
    report["base_backup_seconds"]=round(time.monotonic()-started,3)
    source_id=api.command("ps","-q","postgres").strip()
    subprocess.run(["docker","cp",source_id+":"+base,str(folder/"base")],check=True,capture_output=True)
    oid=api.order(55,555)
    payment=api.pay(55,oid)["paymentId"]
    api.provider("/demo/payments/"+payment+"/state","POST",{"state":"SUCCEEDED"})
    c.wait("paid checkpoint",lambda:api.sql(f"SELECT status FROM orders WHERE id='{oid}'")=="PAID")
    image_key,image_size=load_stage4.upload_fixture()
    c.wait("published checkpoint",lambda:api.sql("SELECT count(*) FROM outbox_events WHERE published_at IS NULL")=="0")
    original_stock=api.sql("SELECT on_hand FROM inventory WHERE product_id=88888")
    try:
        api.command("stop",*writers)
        expected=fingerprint()
        manifest=[]
        for row in json.loads(api.sql("SELECT coalesce(json_agg(r),'[]') FROM (SELECT object_key,content_type FROM product_images ORDER BY object_key) r")):
            data,_=s3_stage5.request("GET",row["object_key"])
            digest=hashlib.sha256(data).hexdigest()
            (folder/digest).write_bytes(data)
            manifest.append({**row,"sha256":digest,"size":len(data)})
        (folder/"objects.json").write_text(json.dumps(manifest,indent=2)+"\n")
        lsn=api.sql(f"SELECT pg_create_restore_point('{restorepoint}')")
        wal=api.sql(f"SELECT pg_walfile_name('{lsn}'::pg_lsn)")
        api.sql("SELECT pg_switch_wal();")
        c.wait("restore WAL archived",lambda:api.command("exec","-T","postgres","sh","-c","test -f /archive/"+wal+" && echo archived").strip()=="archived",90)
        report["target_lsn"]=lsn
        api.sql("UPDATE inventory SET on_hand=on_hand+13 WHERE product_id=88888;")
        assert fingerprint()!=expected
        garage_config=str(c.ROOT/"infra/stage5/garage.toml")
        recipe.write_text(json.dumps({"services":{
            "init":{"image":"postgres:17-alpine","user":"0:0","entrypoint":["sh","-c","cp -a /base/. /data/ && touch /data/recovery.signal && chown -R postgres:postgres /data && chmod 700 /data"],"volumes":[str(folder/"base")+":/base:ro","recovered:/data"],"mem_limit":"128m"},
            "postgres":{"image":"postgres:17-alpine","environment":{"POSTGRES_PASSWORD":"unused"},"volumes":["recovered:/var/lib/postgresql/data","source-wal:/wal:ro"],"command":["postgres","-c","shared_buffers=128MB","-c","restore_command=cp /wal/%f %p","-c","recovery_target_name="+restorepoint,"-c","recovery_target_action=promote","-c","archive_mode=off"],"depends_on":{"init":{"condition":"service_completed_successfully"}},"mem_limit":"768m","cpus":2},
            "garage":{"image":"dxflrs/garage:v2.1.0","command":["/garage","server"],"volumes":[garage_config+":/etc/garage.toml:ro","garage-meta:/var/lib/garage/meta","garage-data:/var/lib/garage/data"],"ports":["127.0.0.1:13910:3900"],"mem_limit":"256m"},
            "redis":{"image":"redis:7.4-alpine","mem_limit":"128m"},
            "commerce":{"image":"hdls-commerce:stage5","environment":{"SPRING_PROFILES_ACTIVE":"demo","DB_URL":"jdbc:postgresql://postgres:5432/commerce","DB_USER":"commerce","DB_PASSWORD":"demo-database-only","REDIS_HOST":"redis","MEDIA_PUBLIC_BASE":"http://localhost:18096","JAVA_TOOL_OPTIONS":"-Xmx256m -XX:ActiveProcessorCount=1"},"ports":["127.0.0.1:18091:8080"],"mem_limit":"640m","cpus":1},
            "media":{"image":"nginx:1.29-alpine","volumes":[str(c.ROOT/"infra/stage4/media.conf")+":/etc/nginx/conf.d/default.conf:ro"],"ports":["127.0.0.1:18096:8080"],"mem_limit":"64m"}},
            "volumes":{"recovered":{},"garage-meta":{},"garage-data":{},"source-wal":{"external":True,"name":c.PROJECT+"_wal-archive"}}},indent=2)+"\n")
        def command(*args,data=None):
            result=subprocess.run(recovery+list(args),input=data,text=True,capture_output=True)
            if result.returncode:raise RuntimeError((result.stdout+result.stderr)[-4000:])
            return result.stdout.strip()
        def restored_sql(query):return command("exec","-T","postgres","psql","-U","commerce","-d","commerce","-qAt","-v","ON_ERROR_STOP=1",data=query)
        restore_started=time.monotonic()
        command("up","-d","postgres","garage","redis")
        c.wait("named WAL target promoted",lambda:restored_sql("SELECT NOT pg_is_in_recovery()") == "t",120)
        assert fingerprint(restored_sql)==expected
        assert restored_sql("SELECT on_hand FROM inventory WHERE product_id=88888")==original_stock
        init_stage4.COMPOSE=recovery
        init_stage4.initialize()
        for obj in manifest:
            s3_stage5.request("PUT",obj["object_key"],endpoint="http://127.0.0.1:13910",body=(folder/obj["sha256"]).read_bytes(),content_type=obj["content_type"])
            data,_=s3_stage5.request("GET",obj["object_key"],endpoint="http://127.0.0.1:13910")
            assert hashlib.sha256(data).hexdigest()==obj["sha256"]
        command("up","-d","commerce","media")
        c.wait("recovered API ready",lambda:api.request("/readyz",port=18091)[0]==200,150)
        assert api.ok("/api/orders/"+oid,55,port=18091)["status"]=="PAID"
        assert api.ok("/api/orders/"+oid+"/payments",55,port=18091)["state"]=="SUCCEEDED"
        image=api.ok("/"+image_key,port=18096)
        assert len(image)==image_size
        report.update({"restore_to_api_seconds":round(time.monotonic()-restore_started,3),"fingerprints_match":True,"post_target_change_excluded":True,"images_restored":len(manifest),"image_bytes":sum(x["size"] for x in manifest),"paid_order_http_verified":True,"objects_sha256_match":True,"recovery_scope":"base backup + archived named WAL point + logical immutable objects; isolated primary; derived index/cache restored separately"})
        (c.ROOT/"docs/stage5_restore_results.json").write_text(json.dumps(report,indent=2)+"\n")
        print(json.dumps(report,indent=2),flush=True)
    finally:
        # Preserve drill volumes for audit; stop only the recovery project.
        if recipe.exists():subprocess.run(recovery+["down"],capture_output=True)
        api.sql("UPDATE inventory SET on_hand="+original_stock+" WHERE product_id=88888;")
        api.command("start",*writers)
        init_stage4.COMPOSE=c.COMPOSE
        api.command("exec","-T","postgres","rm","-rf",base)


if __name__=="__main__":run()
