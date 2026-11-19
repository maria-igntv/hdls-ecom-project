#!/usr/bin/env python3
"""Idempotent local bootstrap after image build; preserves existing data."""
import stage5_common as c
import init_stage4

if __name__=="__main__":
    c.api.command("up","-d","--no-build")
    c.wait("PostgreSQL",lambda:c.api.sql("SELECT 1")=="1")
    c.api.sql("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
    init_stage4.COMPOSE=c.COMPOSE
    init_stage4.initialize()
    c.wait("API/search readiness",lambda:all(c.api.request("/readyz",port=p)[0]==200 for p in (18080,18081,18082,18085)),240)
    c.wait("17 scrapes",lambda:len(c.prom("up"))==17 and not c.prom("up == 0"),180)
    print("Stage 5 initialized: API, search, S3, metrics and profiling ready")
