#!/usr/bin/env python3
"""Mixed load with observations; warmup and measured interval remain separate."""
import argparse
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.parse
import stage5_common as c
from stage5_common import api
import load_stage4 as load


def client_snapshot(minute):
    ids=subprocess.check_output(['docker','ps','-q','--filter','label=com.docker.compose.project='+c.PROJECT,'--filter','label=com.docker.compose.service=k6','--filter','label=com.docker.compose.oneoff=True'],text=True,timeout=5).split()
    if not ids:return None
    if len(ids)!=1:raise RuntimeError('Expected one active k6 container in the selected project')
    result={}
    for name in ('operation_requests','technical_errors','dropped_iterations','minute_latency_'+str(minute),'minute_technical_errors_'+str(minute)):
        raw=subprocess.check_output(['docker','exec',ids[0],'wget','-qO-','http://127.0.0.1:6565/v1/metrics/'+name],text=True,timeout=5)
        result[name]=json.loads(raw)['data']['attributes']['sample']
        if name=='operation_requests':result['counter_sample_monotonic']=time.monotonic()
    try:
        cpu=subprocess.check_output(['docker','exec',ids[0],'cat','/sys/fs/cgroup/cpu.stat'],text=True,timeout=5)
        result['generator']={'cpu':{k:int(v) for k,v in (line.split() for line in cpu.splitlines())},'cpu_sample_monotonic':time.monotonic(),'memory_bytes':int(subprocess.check_output(['docker','exec',ids[0],'cat','/sys/fs/cgroup/memory.current'],text=True,timeout=5))}
    except (subprocess.SubprocessError,OSError,ValueError) as error:result['generator_error']=str(error)[:200]
    return result


def run(args):
    output=c.OUTPUT/'load'
    output.mkdir(parents=True,exist_ok=True)
    os.environ['LOAD_RESULTS_DIR']=str(output)
    c.wait('full readiness',lambda:all(api.request('/readyz',port=p)[0]==200 for p in (18080,18081,18082,18085)))
    c.wait('all scrapes healthy',lambda:len(c.prom('up'))==17 and not c.prom('up == 0'))
    if api.sql("SELECT count(*) FROM orders WHERE idempotency_key='history-1'")=='0':api.sql((c.ROOT/'infra/stage2/history_fixture.sql').read_text())
    key,size=load.upload_fixture()
    warmup=None
    if args.warmup_seconds:
        api.command('exec','-T','redis','redis-cli','FLUSHDB')
        warmup=load.profile('stage5-warmup',640,args.warmup_seconds,key,size,output)
    samples=[]
    stop=threading.Event()
    started=time.monotonic();epoch=time.time()
    def collect():
        while not stop.is_set():
            try:
                elapsed=time.monotonic()-started
                row={'elapsed':elapsed,'host_disk_free_bytes':shutil.disk_usage(c.ROOT).free,'alerts':c.prom('ALERTS{alertstate="firing"}'),'targets_down':c.prom('up == 0'),'outbox':c.prom('max(ecom_outbox_pending)'),'visibility':c.prom('sum(ecom_search_visibility_seconds_count)'),'invariants':c.prom('max(ecom_inventory_violations)')}
                try:row['client']=client_snapshot(max(0,min((args.seconds-1)//60,int(elapsed//60)-1)))
                except Exception as error:row['client_error']=str(error)[:200]
                previous=next((s for s in reversed(samples) if s.get('client')),None)
                if previous and row.get('client'):
                    row['client_interval_rps']=(row['client']['operation_requests']['count']-previous['client']['operation_requests']['count'])/(row['client']['counter_sample_monotonic']-previous['client']['counter_sample_monotonic'])
                    if row['client'].get('generator') and previous['client'].get('generator'):
                        current_cpu=row['client']['generator'];previous_cpu=previous['client']['generator']
                        row['generator_interval_cpu_percent']=(current_cpu['cpu']['usage_usec']-previous_cpu['cpu']['usage_usec'])/(current_cpu['cpu_sample_monotonic']-previous_cpu['cpu_sample_monotonic'])/10000
                    row['client_load_shortfall']=elapsed>90 and row['client_interval_rps']<640*0.98
                samples.append(row)
                c.save('soak-live.json',samples)
                print(json.dumps({'elapsed':round(elapsed),'client_interval_rps':row.get('client_interval_rps'),'client_load_shortfall':row.get('client_load_shortfall'),'generator_interval_cpu_percent':row.get('generator_interval_cpu_percent'),'client':row.get('client'),'alerts':[a['metric']['alertname'] for a in row['alerts']]}),flush=True)
            except Exception as e:samples.append({'elapsed':time.monotonic()-started,'error':str(e)[:200]})
            stop.wait(30)
    thread=threading.Thread(target=collect,daemon=True);thread.start()
    try:result=load.profile('stage5-soak-640' if args.seconds>=3600 else 'stage5-smoke-640',640,args.seconds,key,size,output)
    finally:stop.set();thread.join(timeout=15)
    raw=json.loads((output/(result['run']+'-k6.json')).read_text())
    result['actual_test_seconds']=raw['state']['testRunDurationMs']/1000
    result['minute_windows']={str(i):{'latency_ms':raw['metrics']['minute_latency_'+str(i)]['values'],'technical_errors':raw['metrics']['minute_technical_errors_'+str(i)]['values'],'operations':raw['metrics']['minute_operations_'+str(i)]['values']} for i in range((args.seconds+59)//60)}
    result['invariant_violations']=c.invariants()
    # Evaluate at the end of the arrival window, not after invariant/queue validation.
    def at_end(query):
        return api.ok('/api/v1/query?'+urllib.parse.urlencode({'query':query,'time':epoch+args.seconds}),port=19090)['data']['result']
    report={'project':c.PROJECT,'warmup':warmup,'soak':result,'monitor_samples':samples,'visibility_observations':at_end(f'sum by (success) (increase(ecom_search_visibility_seconds_count[{args.seconds}s]))'),'visibility_histogram':at_end(f'sum by (le) (increase(ecom_search_visibility_seconds_bucket[{args.seconds}s]))'),'all_17_targets_healthy_at_end':len(c.prom('up'))==17 and not c.prom('up == 0')}
    report['client_samples']=sum(bool(s.get('client')) for s in samples)
    report['client_sample_errors']=sum('client_error' in s for s in samples)
    path=c.ROOT/'docs'/args.output_name;path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    assert not result['failed_thresholds'] and not result['oom_killed'] and result['invariant_violations']==0
    assert report['all_17_targets_healthy_at_end'] and not any(s.get('targets_down') for s in samples)
    assert report['client_samples']>=max(1,args.seconds//60),'Insufficient live client observations; inspect client_error in the saved report'


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--seconds',type=int,default=3600)
    parser.add_argument('--warmup-seconds',type=int,default=180)
    parser.add_argument('--output-name',default='stage5_load_results.json')
    args=parser.parse_args()
    assert args.seconds>0 and args.warmup_seconds>=0
    run(args)
