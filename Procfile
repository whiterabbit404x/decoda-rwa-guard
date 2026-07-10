web: uvicorn services.api.app.main:app --host 0.0.0.0 --port ${PORT:-8000}
monitoring-worker: python -m services.api.app.run_monitoring_worker
recovery-drill-worker: python -m services.api.app.run_recovery_drill_worker
retention-worker: python -m services.api.app.retention_worker
realtime-worker: python -m services.api.app.run_realtime_worker
quicknode-live-worker: python -m services.api.app.run_quicknode_live_worker
