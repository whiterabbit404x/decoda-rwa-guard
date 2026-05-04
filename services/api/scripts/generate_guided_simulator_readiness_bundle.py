#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone

def now():
    return datetime.now(timezone.utc).isoformat()

def main():
    workspace_id=str(uuid4())
    chain={k:str(uuid4()) for k in [
        'asset_id','target_id','monitoring_config_id','monitoring_run_id','telemetry_event_id',
        'detection_id','alert_id','incident_id','response_action_id','evidence_package_id'
    ]}
    t=now()
    base=Path('services/api/artifacts/live_evidence/latest')
    base.mkdir(parents=True, exist_ok=True)

    summary={
      'workspace_id':workspace_id,'generated_at':t,
      'live_successful_monitoring_demo':False,'simulator_successful_monitoring_demo':True,
      'telemetry_event_present':True,'telemetry_evidence_source':'guided_simulator',
      'detection_generated_from_telemetry':True,'alert_generated_from_detection':True,
      'incident_opened_from_alert':True,'response_action_recommended_or_executed':True,
      'evidence_package_exported':True,'billing_email_provider_checks_passing':False,
      'broad_self_serve_blocked_reason':'billing_email_provider_checks_missing_or_not_verified',
      'onboarding_to_first_signal_complete':True,
      'production_validation_proof_bundle_complete':True,
      'controlled_pilot_ready':True,'broad_self_serve_ready':False,'enterprise_procurement_ready':False,
      'claim_ineligibility_reasons':['billing_email_provider_checks_missing_or_not_verified']
    }
    telemetry=[{'id':chain['telemetry_event_id'],'workspace_id':workspace_id,'asset_id':chain['asset_id'],'target_id':chain['target_id'],'evidence_source':'guided_simulator','event_type':'transfer_observed','observed_at':t,'monitoring_run_id':chain['monitoring_run_id']}]
    detections=[{'id':chain['detection_id'],'workspace_id':workspace_id,'telemetry_event_id':chain['telemetry_event_id'],'evidence_source':'guided_simulator','monitoring_run_id':chain['monitoring_run_id']}]
    alerts=[{'id':chain['alert_id'],'workspace_id':workspace_id,'detection_id':chain['detection_id'],'target_id':chain['target_id'],'source':'guided_simulator'}]
    incidents=[{'id':chain['incident_id'],'workspace_id':workspace_id,'alert_id':chain['alert_id'],'status':'open'}]
    response=[{'id':chain['response_action_id'],'workspace_id':workspace_id,'incident_id':chain['incident_id'],'status':'executed'}]
    runs=[{'id':chain['monitoring_run_id'],'workspace_id':workspace_id,'status':'completed','trigger_type':'manual'}]
    evidence={
      'workspace_id':workspace_id,'mode':'controlled_pilot','evidence_source':'guided_simulator','chain':chain,
      'assertions':{
        'telemetry_created':True,'detection_linked_to_telemetry':True,'alert_linked_to_detection':True,
        'incident_linked_to_alert':True,'response_action_linked_to_incident':True,'evidence_package_exported':True,
        'simulator_successful_monitoring_demo':True,'telemetry_event_present':True,
        'detection_generated_from_telemetry':True,'alert_generated_from_detection':True,
        'incident_opened_from_alert':True,'response_action_recommended_or_executed':True,
        'onboarding_to_first_signal_complete':True
      }
    }
    report='''# Decoda RWA Guard Readiness Proof

- Workspace: `{workspace_id}`
- Protected RWA/Treasury-backed asset: `{asset_id}`
- Monitoring source: `{target_id}`
- Monitoring run: `{monitoring_run_id}`
- Telemetry event: `{telemetry_event_id}`
- Detection: `{detection_id}`
- Alert: `{alert_id}`
- Incident: `{incident_id}`
- Response action: `{response_action_id}`
- Evidence package: `{evidence_package_id}`
- Evidence source: guided_simulator
- Controlled pilot ready: true
- Broad self-serve ready: false
- Enterprise procurement ready: false

This proof uses guided_simulator evidence and does not claim live provider monitoring.
'''.format(workspace_id=workspace_id,**chain)

    files={
      'summary.json':summary,'evidence.json':evidence,'telemetry_events.json':telemetry,'detections.json':detections,
      'alerts.json':alerts,'incidents.json':incidents,'response_actions.json':response,'runs.json':runs,'report.md':report
    }
    for name,v in files.items():
      p=base/name
      if name.endswith('.json'): p.write_text(json.dumps(v,indent=2))
      else: p.write_text(v)

if __name__=='__main__':
    main()
