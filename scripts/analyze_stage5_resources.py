#!/usr/bin/env python3
"""Summarize sampled Docker resources from a completed stage-5 load artifact."""
import json
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def size(text):
    number,unit=re.fullmatch(r'([\d.]+)\s*(B|KiB|MiB|GiB|TiB|kB|MB|GB|TB)',text.strip()).groups()
    return float(number)*{'B':1,'KiB':2**10,'MiB':2**20,'GiB':2**30,'TiB':2**40,'kB':1000,'MB':1000**2,'GB':1000**3,'TB':1000**4}[unit]


def run():
    report=json.loads((ROOT/'docs/stage5_load_results.json').read_text())
    load=report['soak'];raw=ROOT/'artifacts/stage5/load'/(load['run']+'-resources.json')
    data=json.loads(raw.read_text());roles={};totals=[]
    def add(containers,include_total):
        total=0
        for row in containers:
            role=row['Name'].removeprefix(report['project']+'-')
            role='k6' if role.startswith('k6-run-') else re.sub(r'-\d+$','',role)
            used,limit=map(size,row['MemUsage'].split('/'))
            total+=used
            values=roles.setdefault(role,{'sampled_max_memory_bytes':0,'sampled_max_cpu_percent':0,'memory_limit_bytes':limit,'samples':0})
            values['sampled_max_memory_bytes']=max(values['sampled_max_memory_bytes'],used)
            values['sampled_max_cpu_percent']=max(values['sampled_max_cpu_percent'],float(row['CPUPerc'].strip('%')))
            values['samples']+=1
        if include_total:totals.append(total)
    for row in data:
        if row.get('containers'):add(row['containers'],True)
    # Optional operator snapshots include the one-shot generator omitted by Compose ps.
    manual=ROOT/'artifacts/stage5/resource-live.json'
    start=next(s['outbox'][0]['value'][0] for s in report['monitor_samples'] if s.get('outbox'))
    manual_count=0;totals_with_generator=[]
    if manual.exists():
        for row in json.loads(manual.read_text()):
            if start<=row['epoch']<=start+load['duration']+15:
                add(row['containers'],False);manual_count+=1
                totals_with_generator.append(sum(size(r['MemUsage'].split('/')[0]) for r in row['containers']))
    result={'project':report['project'],'run':load['run'],'business_resource_samples':len(data),'manual_samples_in_arrival_window':manual_count,'sampled_max_stack_memory_bytes_excluding_generator':max(totals),'sampled_max_stack_memory_bytes_including_generator':max(totals_with_generator) if totals_with_generator else None,'containers':roles,'interpretation':'Sampled Docker working set and CPU; maxima are not necessarily simultaneous; no claim about cloud disk/network or unsampled short peaks. Generator only covered if optional manual snapshots exist.'}
    generators=[s['client']['generator'] for s in report['monitor_samples'] if (s.get('client') or {}).get('generator')]
    result['generator_cgroup_memory_sampled_max_bytes']=max((g['memory_bytes'] for g in generators),default=None)
    result['generator_cgroup_interval_cpu_sampled_max_percent']=max((s['generator_interval_cpu_percent'] for s in report['monitor_samples'] if 'generator_interval_cpu_percent' in s),default=None)
    result['interpretation']+=' Generator cgroup memory.current includes cache and is reported separately from Docker working set; separately sampled maxima are not added together.'
    (ROOT/'docs/stage5_resource_results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':run()
