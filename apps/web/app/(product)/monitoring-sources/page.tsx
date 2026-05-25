'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import {
  EmptyStateBlocker,
  StatusPill,
  TabStrip,
  TableShell,
  type PillVariant,
} from '../../components/ui-primitives';
import { isValidApiBaseUrl, normalizeApiBaseUrl, resolveApiUrl } from '../../dashboard-data';
import { usePilotAuth } from '../../pilot-auth-context';
import RuntimeSummaryPanel from '../../runtime-summary-panel';

type TabKey = 'targets' | 'systems';
type AssetRow = { id: string; name?: string; identifier?: string; enabled?: boolean };
type TargetRow = { id: string; name?: string; target_type?: string; provider?: string; enabled?: boolean; monitoring_enabled?: boolean; last_checked_at?: string | null; health_status?: string | null; next_action?: string | null; monitored_system_id?: string | null; systems_count?: number; asset_id?: string | null; };
type MonitoredSystemRow = { id: string; asset_name?: string; target_name?: string; target_id?: string; is_enabled?: boolean; runtime_status?: string | null; last_heartbeat?: string | null; last_event_at?: string | null; coverage_reason?: string | null; freshness_status?: string | null; evidence_source?: string | null; };

function fmt(value?: string | null): string { if (!value) return '-'; const parsed = new Date(value); if (Number.isNaN(parsed.getTime())) return '-'; const diff = Date.now() - parsed.getTime(); if (diff < 60_000) return `${Math.floor(diff / 1000)}s ago`; if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`; if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`; return parsed.toLocaleDateString(); }
const targetStatusPill=(t:TargetRow):{label:string;variant:PillVariant}=>!t.enabled&&!t.monitoring_enabled?{label:'Disabled',variant:'neutral'}:!t.monitored_system_id&&!t.systems_count?{label:'Not Configured',variant:'warning'}:((t.health_status??'').toLowerCase()==='healthy'?{label:'Healthy',variant:'success'}:(t.health_status??'').toLowerCase()==='degraded'?{label:'Degraded',variant:'warning'}:(t.health_status??'').toLowerCase()==='error'?{label:'Error',variant:'danger'}:(t.health_status??'').toLowerCase()==='disabled'?{label:'Disabled',variant:'neutral'}:(t.monitoring_enabled||t.enabled)?{label:'Unknown',variant:'neutral'}:{label:'Not Configured',variant:'warning'});
const targetNextAction=(t:TargetRow)=>t.next_action||((!t.enabled&&!t.monitoring_enabled)?'Enable target':(!t.monitored_system_id&&!t.systems_count)?'Enable monitored system':(t.health_status??'').toLowerCase()==='degraded'?'Check provider':(t.health_status??'').toLowerCase()==='error'?'Repair target':(t.health_status??'').toLowerCase()==='healthy'?'View telemetry':'Wait for poll');
const runtimeStatusPill=(s:MonitoredSystemRow):{label:string;variant:PillVariant}=>!s.is_enabled?{label:'Disabled',variant:'neutral'}:!s.last_heartbeat?{label:'Not Started',variant:'neutral'}:(s.runtime_status??'').toLowerCase()==='reporting'?{label:'Reporting',variant:'success'}:(s.runtime_status??'').toLowerCase()==='degraded'?{label:'Degraded',variant:'warning'}:(s.runtime_status??'').toLowerCase()==='offline'?{label:'Offline',variant:'danger'}:{label:'Unknown',variant:'neutral'};
const coveragePill=(s:MonitoredSystemRow):{label:string;variant:PillVariant}=>!s.is_enabled?{label:'Missing',variant:'danger'}:!s.last_heartbeat?{label:'Unknown',variant:'neutral'}:['covered','full'].includes((s.coverage_reason??'').toLowerCase())?{label:'Covered',variant:'success'}:(s.coverage_reason??'').toLowerCase()==='partial'?{label:'Partial',variant:'warning'}:['stale'].includes((s.coverage_reason??'').toLowerCase())?{label:'Stale',variant:'warning'}:(s.coverage_reason??'').toLowerCase()==='missing'?{label:'Missing',variant:'danger'}:s.last_event_at?{label:'Partial',variant:'warning'}:{label:'Unknown',variant:'neutral'};
const resolveEvidenceSource=(s:MonitoredSystemRow):{label:string;variant:PillVariant}=>['simulator','demo','replay'].includes((s.evidence_source??s.freshness_status??'').toLowerCase())?{label:'simulator',variant:'info'}:['live','live_provider'].includes((s.evidence_source??s.freshness_status??'').toLowerCase())?(s.last_heartbeat&&s.last_event_at?{label:'live_provider',variant:'success'}:{label:'none',variant:'neutral'}):{label:'none',variant:'neutral'};
const TARGET_HEADERS=['Target Name','Type','Provider','Systems','Status','Last Poll','Next Action'];
const SYSTEM_HEADERS=['System Name','Linked Target','Enabled','Runtime Status','Last Heartbeat','Last Telemetry','Coverage','Evidence Source'];

export default function MonitoringSourcesPage(){
  const [activeTab,setActiveTab]=useState<TabKey>('targets'); const [assets,setAssets]=useState<AssetRow[]>([]); const [targets,setTargets]=useState<TargetRow[]>([]); const [systems,setSystems]=useState<MonitoredSystemRow[]>([]); const [loadError,setLoadError]=useState(''); const [loading,setLoading]=useState(true); const [creatingForAsset,setCreatingForAsset]=useState<string| null>(null);
  const apiUrl=resolveApiUrl(); const {authHeaders}=usePilotAuth();
  const missingTargetAssets=useMemo(()=>assets.filter((a)=>Boolean(a.enabled)!==false && !targets.some((t)=>t.asset_id===a.id)),[assets,targets]);
  async function load(){
    setLoading(true);
    const normalizedApiUrl = normalizeApiBaseUrl(apiUrl);
    if (!normalizedApiUrl || !isValidApiBaseUrl(normalizedApiUrl)) { setLoadError('Unable to load monitoring sources: invalid API URL configuration.'); setLoading(false); return; }
    const headers = authHeaders();
    try {
      const [assetsResponse, targetsResponse, systemsResponse] = await Promise.all([
        fetch(`${apiUrl}/assets`, { headers, cache: 'no-store' }),
        fetch(`${apiUrl}/targets`, { headers, cache: 'no-store' }),
        fetch(`${apiUrl}/monitoring/systems`, { headers, cache: 'no-store' }),
      ]);
      const [assetsPayload, targetsPayload, systemsPayload] = await Promise.all([assetsResponse.json().catch(()=>({})),targetsResponse.json().catch(()=>({})),systemsResponse.json().catch(()=>({}))]);
      const bad=[{r:assetsResponse,u:`${apiUrl}/assets`,p:assetsPayload},{r:targetsResponse,u:`${apiUrl}/targets`,p:targetsPayload},{r:systemsResponse,u:`${apiUrl}/monitoring/systems`,p:systemsPayload}].find((x)=>!x.r.ok);
      if (bad){const detail=typeof bad.p?.detail==='string'?bad.p.detail:'request failed'; setLoadError(`Unable to load monitoring sources: HTTP ${bad.r.status} from ${bad.u} (${detail})`); return;}
      setAssets(assetsPayload.assets??[]); setTargets(targetsPayload.targets??[]); setSystems(systemsPayload.systems??[]); setLoadError('');
    } catch (error) { setLoadError(`Network error loading monitoring sources from ${apiUrl}: ${error instanceof Error ? error.message : 'unknown error'}`);
    } finally { setLoading(false); }
  }
  useEffect(()=>{ void load(); },[apiUrl,authHeaders]);
  async function createTargetForAsset(asset: AssetRow){
    setCreatingForAsset(asset.id);
    try {
      const response=await fetch(`${apiUrl}/targets`,{method:'POST',headers:{'Content-Type':'application/json',...authHeaders()},body:JSON.stringify({name:`${asset.name || 'Asset'} Monitoring Target`,asset_id:asset.id,target_type:'contract',enabled:true,monitoring_enabled:true,is_active:true,chain_network:'ethereum-mainnet',chain_id:1,contract_identifier:asset.identifier || '',auto_create_alerts:true,auto_create_incidents:true,monitoring_interval_seconds:30})});
      const payload=await response.json().catch(()=>({}));
      if(!response.ok){setLoadError(`Unable to create monitoring target: HTTP ${response.status} from ${apiUrl}/targets (${payload?.detail || 'request failed'})`);return;}
      await load();
    } finally { setCreatingForAsset(null); }
  }
  const targetNameById = useMemo(() => new Map(targets.map((target) => [target.id, target.name || 'Unnamed target'])), [targets]);
  const noAssets=!loading&&assets.length===0; const hasAssetsNoTargets=!loading&&assets.length>0&&targets.length===0; const hasTargetsNoSystems=!loading&&targets.length>0&&systems.length===0;
  return <main className="productPage"><RuntimeSummaryPanel /><div className="listHeader" style={{ marginBottom: '1.25rem', alignItems: 'flex-start' }}><div><h1 style={{ margin: 0, fontSize: '1.45rem', fontWeight: 700 }}>Monitoring Sources</h1></div><Link href="/monitoring-sources/targets" prefetch={false} className="btn btn-primary">Add Target</Link></div>{loadError ? <p className="statusLine" style={{ color: 'var(--danger-fg)' }}>{loadError}</p> : null}{missingTargetAssets.length>0?<div className="dataCard" style={{marginBottom:'1rem'}}><p className="muted" style={{marginTop:0}}>Active monitored assets with no target linkage:</p>{missingTargetAssets.map((asset)=><div key={asset.id} className="buttonRow" style={{justifyContent:'space-between',marginBottom:'.5rem'}}><span>{asset.name || asset.identifier || asset.id}</span><button type="button" className="btn btn-secondary" disabled={creatingForAsset===asset.id} onClick={()=>void createTargetForAsset(asset)}>{creatingForAsset===asset.id?'Creating…':'Create target'}</button></div>)}</div>:null}<TabStrip tabs={[{ key: 'targets', label: 'Monitoring Targets' },{ key: 'systems', label: 'Monitored Systems' }]} active={activeTab} onChange={(key) => setActiveTab(key as TabKey)} />
{activeTab==='targets'?<div>{noAssets?<EmptyStateBlocker title="No protected assets yet" body="Add a protected asset before configuring monitoring sources." ctaHref="/assets" ctaLabel="Add Asset"/>:hasAssetsNoTargets?<EmptyStateBlocker title="No monitoring target is linked to this asset yet" body="Create a monitoring target so Decoda can begin collecting runtime signals for this asset." ctaHref="/monitoring-sources/targets" ctaLabel="Create monitoring target"/>:<TableShell headers={TARGET_HEADERS} compact>{loading?<tr><td colSpan={TARGET_HEADERS.length}>Loading targets...</td></tr>:targets.map((target)=>{const status=targetStatusPill(target);const systemsDisplay=target.systems_count!=null?String(target.systems_count):target.monitored_system_id?'1':'0';return <tr key={target.id}><td>{target.name || 'Unnamed target'}</td><td>{target.target_type || 'Unknown'}</td><td>{target.provider || <span className="muted">Default</span>}</td><td>{systemsDisplay}</td><td><StatusPill label={status.label} variant={status.variant}/></td><td>{fmt(target.last_checked_at)}</td><td>{targetNextAction(target)}</td></tr>;})}</TableShell>}</div>:null}
{activeTab==='systems'?<div>{noAssets?<EmptyStateBlocker title="No protected assets yet" body="Add a protected asset before configuring monitoring sources." ctaHref="/assets" ctaLabel="Add Asset"/>:hasAssetsNoTargets?<EmptyStateBlocker title="No monitoring target is linked to this asset yet" body="Create a monitoring target so Decoda can begin collecting runtime signals for this asset." ctaHref="/monitoring-sources/targets" ctaLabel="Create monitoring target"/>:hasTargetsNoSystems?<EmptyStateBlocker title="Target exists, but no monitored system is enabled" body="Enable a monitored system to start heartbeat, polling, and telemetry collection." ctaHref="/monitoring-sources/monitored-systems" ctaLabel="Enable monitored system"/>:<TableShell headers={SYSTEM_HEADERS} compact>{loading?<tr><td colSpan={SYSTEM_HEADERS.length}>Loading monitored systems...</td></tr>:systems.map((system)=>{const runtimeStatus=runtimeStatusPill(system);const coverage=coveragePill(system);const evidence=resolveEvidenceSource(system);const linkedTarget=system.target_name || targetNameById.get(system.target_id ?? '') || 'Unlinked';return <tr key={system.id}><td>{system.asset_name || `System ${system.id.slice(0, 8)}`}</td><td>{linkedTarget}</td><td><StatusPill label={system.is_enabled ? 'Yes' : 'No'} variant={system.is_enabled ? 'success' : 'neutral'} /></td><td><StatusPill label={runtimeStatus.label} variant={runtimeStatus.variant}/></td><td>{fmt(system.last_heartbeat)}</td><td>{fmt(system.last_event_at)}</td><td><StatusPill label={coverage.label} variant={coverage.variant}/></td><td><StatusPill label={evidence.label} variant={evidence.variant}/></td></tr>;})}</TableShell>}</div>:null}
</main>;
}
