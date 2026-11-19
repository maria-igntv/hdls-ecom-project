#!/usr/bin/env python3
"""Render retained evidence, including failed load; acceptance is a separate gate."""
import argparse
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
START='<!-- stage5-results:start -->';END='<!-- stage5-results:end -->'


def render():
    get=lambda name:json.loads((ROOT/'docs'/name).read_text())
    status=get('stage5_status.json');report=get('stage5_load_results.json')
    initial=get('stage5_first_hour_results.json')
    soak=initial['soak'];warm=initial['warmup'];latest=report['soak'];probe=get('stage5_generator_results.json')['soak']
    smoke=get('stage5_smoke_results.json')['soak']
    follow=get('stage5_followup_results.json');restore=get('stage5_restore_results.json')
    resources=get('stage5_resource_results.json');config=get('stage5_generator_config.json')
    assert follow['passed'] and follow['invariant_violations']==0
    assert restore['fingerprints_match'] and restore['post_target_change_excluded'] and restore['objects_sha256_match']
    assert soak['duration']==3600 and soak['actual_test_seconds']>=3600
    rows=[]
    profiles=[('Прогрев перед первым часом',warm),('Первый полный час',soak),('Проба с предвыделением 50 мс',probe)]
    if latest['run']!=soak['run']:profiles.append(('Последний полный час',latest))
    for name,run in profiles:
        assert run['operations']==run['technical_errors']['passes']+run['technical_errors']['fails']
        verdict='пройден' if not run['failed_thresholds'] and not run['oom_killed'] else 'не пройден'
        rows.append(f"| {name} | {run['duration']} | {run['operations']/run['duration']:.2f} | {run['latency_ms']['p(95)']:.2f} / {run['latency_ms']['p(99)']:.2f} | {int(run['technical_errors']['passes'])} | {run['dropped']} | {verdict} |")
    accepted=status['load_acceptance_complete'] and status['current_generator']['tested_under_load'] and all(not r['failed_thresholds'] and not r['oom_killed'] and r['k6_exit']==0 and r['dropped']==0 and r['invariant_violations']==0 and r['deadlocks']==0 for r in (latest,smoke)) and latest['duration']>=3600 and latest['actual_test_seconds']>=3600 and report.get('client_samples',0)>=60 and report['all_17_targets_healthy_at_end'] and latest.get('preallocatedVUs')==config['preallocated_vus']
    headline='Нагрузочная приёмка завершена.' if accepted else '**Нагрузочная приёмка не завершена. Повторный прогон отложен пользователем; текущая конфигурация генератора под нагрузкой ещё не проверена.**'
    memory=resources['sampled_max_stack_memory_bytes_including_generator']
    resource_text=f'Выборочный максимум Docker working set с генератором — {memory/2**30:.2f} ГиБ' if memory else 'Общий Docker working set с генератором не измерен; ресурсы приведены раздельно'
    next_step='Последний час и финальный smoke прошли пороги.' if accepted else 'Новый час и финальный smoke ещё предстоят; пороги не ослаблены.'
    block=f"""{START}
{headline}

Реализованы Prometheus/Alertmanager, 22 панели Grafana, 17 scrape targets, Tempo с W3C-контекстом через outbox/RabbitMQ и ECS JSON logs. Admission/retention и 34 модульных теста Commerce проверены; после SIGKILL PostgreSQL подтверждённый PAID и replay сохранены.

| Измерение | Секунды | Фактический primary RPS | HTTP p95 / p99, мс | Технические ошибки | Dropped всех сценариев | Пороги |
| --- | --- | --- | --- | --- | --- | --- |
{chr(10).join(rows)}

В первом полном часу выполнено {soak['operations']:,} primary запросов при заданных 640 RPS. Инварианты / deadlocks / OOM — 0, но latency и пропуски нагрузки не прошли пороги. Зелёные up/readiness не подтверждают выполнение клиентского SLO. {resource_text}; CPU k6 во второй части упирался в выделенные 2 CPU. Связь с ограничением Go memory — рабочая гипотеза, а не доказанный профиль GC.

Исправленная конфигурация: {config['preallocated_vus']} предвыделенных VU, максимум {config['max_vus']} сохранён, задано {config['configured_primary_rps']:.2f} primary RPS. Предвыделение рассчитано на 200 мс и настраивается через VU_PREALLOC_SECONDS. Лимиты k6 — 2 ГиБ / Go 1536 МиБ; добавлен live-контроль клиентских RPS, dropped, p95 завершённой минуты и cgroup CPU/memory. Лимиты Tempo/Grafana/blackbox увеличены по измеренным пикам. {next_step}

Локальный PITR восстановил оплаченный заказ и 2 изображения с совпадающим SHA-256; изменение после точки исключено. Base backup/verify {restore['base_backup_seconds']:.3f} с, recovery → API {restore['restore_to_api_seconds']:.3f} с. Это локальный drill; многозонные RPO/RTO остаются целями архитектуры.

Сохранены первый прерванный опыт с OOM Tempo, неуспешный полный час и короткие пробы. Ошибочные 401 при отказе SQL исправлены и отдельно перепроверены как 503. [Все результаты и ограничения](docs/stage5_verification.md), [SLO](docs/reliability.md), [runbook](docs/runbooks.md).
{END}"""
    return block,accepted


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--write',action='store_true');parser.add_argument('--check',action='store_true');parser.add_argument('--require-passed',action='store_true');args=parser.parse_args()
    block,accepted=render()
    for file in (ROOT/'README.md',ROOT/'docs/stage5_verification.md'):
        text=file.read_text();desired=block if file.name=='README.md' else block.replace('(docs/stage5_verification.md)','(stage5_verification.md)').replace('(docs/reliability.md)','(reliability.md)').replace('(docs/runbooks.md)','(runbooks.md)')
        assert START in text and END in text
        old=text[text.index(START):text.index(END)+len(END)]
        if args.check:assert old==desired,f'Stale results in {file}'
        if args.write:file.write_text(text.replace(old,desired))
    print('Evidence summaries match JSON; load acceptance: '+('PASSED' if accepted else 'PENDING'))
    if args.require_passed and not accepted:raise SystemExit('Stage 5 load acceptance is not complete')
