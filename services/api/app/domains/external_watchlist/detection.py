"""Detection rules for external public monitoring.

Pure: no database, no network. The worker hands in one decoded event plus the
rule state it needs (rolling baselines, the target's last known configuration)
and gets back finding drafts and state updates to persist.

The existing Threat Detection Engineer does the judging wherever it can:

  * privileged / administrative events   → ``detectors.detect_privileged_actions``
  * unusually large transfers            → ``detectors.detect_unusual_transfers``
  * large mint / burn                    → ``detectors.detect_mint_burn_irregularity``
  * severity and confidence              → ``scoring.compute_severity`` /
                                           ``scoring.compute_confidence``

Each detector is called with ONE event, so a finding always maps to exactly one
on-chain log and the engine's batch heuristics (fan-out, burst frequency) never
turn ordinary protocol activity into a finding. Thresholds are RELATIVE: a
transfer is "large" against this token's own rolling baseline, and no relative
finding is made until the baseline has enough samples.

Language: titles, explanations and classes use careful vocabulary only —
"administrative change", "privileged configuration change", "observed
anomaly", "unusual activity", "review". Nothing here calls an event an attack,
a hack, an exploit or a compromise: public telemetry shows what changed, not
who intended it or whether it was authorized. ``assert_careful_language``
enforces that on every draft.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from services.api.app.domains.external_watchlist import abi
from services.api.app.domains.external_watchlist import config as ewc
from services.api.app.domains.threat_detection import config as tdc
from services.api.app.domains.threat_detection import detectors as tdd
from services.api.app.domains.threat_detection import scoring as tds

_BANNED_RE = re.compile(ewc.BANNED_LANGUAGE_PATTERN, re.IGNORECASE)

#: Rule state kept per target is bounded so a busy role never grows it without limit.
_MAX_TRACKED_ROLE_HOLDERS = 50


class LanguageViolation(ValueError):
    pass


def assert_careful_language(*texts: Any) -> None:
    for text in texts:
        if text is None:
            continue
        match = _BANNED_RE.search(str(text))
        if match:
            raise LanguageViolation(f'External finding text may not say {match.group(0)!r}.')


def short(address: Any) -> str:
    text = str(address or '')
    return f'{text[:6]}…{text[-4:]}' if len(text) == 42 else text


# ── rolling baselines ────────────────────────────────────────────────────────
@dataclass
class Baseline:
    sample_count: int = 0
    mean: Decimal = Decimal(0)
    max_value: Decimal | None = None
    updated_block: int | None = None
    dirty: bool = False

    def add(self, value: Decimal, block: int | None) -> None:
        """Running mean (Welford). Exact in Decimal for raw token units."""
        self.sample_count += 1
        self.mean = self.mean + (value - self.mean) / Decimal(self.sample_count)
        self.max_value = value if self.max_value is None or value > self.max_value else self.max_value
        self.updated_block = block
        self.dirty = True


@dataclass
class RuleState:
    """Everything a rule may read or change, loaded once per chunk."""

    baselines: dict[tuple[str, str], Baseline] = field(default_factory=dict)
    runtime_state: dict[str, Any] = field(default_factory=dict)
    runtime_state_dirty: bool = False

    def baseline(self, token: str, kind: str) -> Baseline:
        return self.baselines.setdefault((token, kind), Baseline())

    def section(self, name: str) -> dict[str, Any]:
        value = self.runtime_state.get(name)
        if not isinstance(value, dict):
            value = {}
            self.runtime_state[name] = value
        return value

    def mark_dirty(self) -> None:
        self.runtime_state_dirty = True


@dataclass
class FindingDraft:
    rule_key: str
    detection_profile: str
    finding_type: str
    finding_class: str
    title: str
    severity: str
    confidence: float
    explanation: str
    interpretation: str
    decoded: dict[str, Any]
    previous_state: dict[str, Any] | None
    new_state: dict[str, Any] | None
    dedupe_key: str
    score_inputs: dict[str, Any]
    event: abi.DecodedLog | None = None
    observed_at: datetime | None = None
    contract_address: str | None = None
    initiator: str | None = None
    tx_hash: str | None = None
    block_number: int | None = None
    log_index: int | None = None


@dataclass
class RuleContext:
    watchlist_id: str
    target: dict[str, Any]
    profiles: tuple[str, ...]
    config: dict[str, Any]
    engine_config: dict[str, Any]


def build_context(*, watchlist: dict[str, Any], target: dict[str, Any]) -> RuleContext:
    profiles = watchlist.get('detection_profiles')
    if not isinstance(profiles, list):
        profiles = list(ewc.DETECTION_PROFILE_KEYS)
    return RuleContext(
        watchlist_id=str(watchlist['id']),
        target=target,
        profiles=tuple(str(p) for p in profiles if str(p) in ewc.DETECTION_PROFILE_KEYS),
        config=ewc.effective_detection_config(watchlist.get('detection_config')),
        engine_config=tdc.engine_config(),
    )


def _cap_severity(level: str) -> str:
    level = str(level or 'low').lower()
    return 'high' if level == 'critical' else (level if level in ewc.EXTERNAL_SEVERITIES else 'low')


def _telemetry_event(
    event: abi.DecodedLog, *, observed_at: datetime, from_address: str | None,
    to_address: str | None = None, amount: Decimal | None = None, asset_key: str | None = None,
    chain_id: int | None = None,
) -> tdd.TelemetryEvent:
    """The threat-detection engine's normalized event, built from an external log.

    ``workspace_id`` carries the external scope marker: there is no workspace,
    and the detectors never read it.
    """
    return tdd.TelemetryEvent(
        id=f'{event.tx_hash}:{event.log_index}',
        workspace_id=ewc.MONITORING_SCOPE_EXTERNAL_PUBLIC,
        event_type=event.spec.event_type,
        observed_at=observed_at,
        evidence_source='live',
        asset_id=asset_key,
        chain_id=chain_id,
        tx_hash=event.tx_hash,
        block_number=event.block_number,
        from_address=from_address,
        to_address=to_address,
        amount=amount,
        contract_address=event.contract_address,
    )


def _draft(
    ctx: RuleContext, event: abi.DecodedLog, *, rule_key: str, profile: str, finding_type: str,
    finding_class: str, title: str, severity: str, confidence: float, explanation: str,
    interpretation: str, previous_state: dict[str, Any] | None, new_state: dict[str, Any] | None,
    score_inputs: dict[str, Any], observed_at: datetime, dedupe_suffix: str | None = None,
) -> FindingDraft:
    assert_careful_language(title, explanation, interpretation)
    return FindingDraft(
        rule_key=rule_key,
        detection_profile=profile,
        finding_type=finding_type,
        finding_class=finding_class,
        title=title[:200],
        severity=_cap_severity(severity),
        confidence=round(float(confidence), 3),
        explanation=explanation,
        interpretation=interpretation,
        decoded=dict(event.decoded),
        previous_state=previous_state,
        new_state=new_state,
        dedupe_key=f'{rule_key}:{event.tx_hash}:{event.log_index}' + (f':{dedupe_suffix}' if dedupe_suffix else ''),
        score_inputs=score_inputs,
        event=event,
        observed_at=observed_at,
        contract_address=event.contract_address,
        initiator=event.initiator,
        tx_hash=event.tx_hash,
        block_number=event.block_number,
        log_index=event.log_index,
    )


# ── privileged / administrative events ───────────────────────────────────────
def _privileged(
    ctx: RuleContext, event: abi.DecodedLog, *, observed_at: datetime, actor: str | None,
    high_impact: bool, rule_key: str, profile: str, finding_type: str, finding_class: str,
    title: str, explanation: str, interpretation: str,
    previous_state: dict[str, Any] | None, new_state: dict[str, Any] | None,
    severity_override: str | None = None,
) -> FindingDraft | None:
    if profile not in ctx.profiles:
        return None
    candidates = tdd.detect_privileged_actions(
        [_telemetry_event(event, observed_at=observed_at, from_address=actor, chain_id=ctx.target.get('chain_id'))],
        config=ctx.engine_config,
    )
    if not candidates:
        # The engine's own self-guard: only decoded privileged event types
        # reach a finding. A mapping mistake fails closed (no finding), never
        # into a finding the engine would not stand behind.
        return None
    candidate = candidates[0]
    severity = tds.compute_severity(
        detection_type=candidate.detection_type, privileged=high_impact, actor_count=max(1, candidate.actor_count),
    )
    confidence = tds.compute_confidence(
        evidence_quality=candidate.evidence_quality,
        evidence_count=len(candidate.evidence),
        signal_count=candidate.signal_count,
        actor_repetition=candidate.actor_repetition,
        temporal_correlation=candidate.temporal_correlation,
    )
    return _draft(
        ctx, event, rule_key=rule_key, profile=profile, finding_type=finding_type,
        finding_class=finding_class, title=title,
        severity=severity_override or severity['level'], confidence=confidence['confidence'],
        explanation=explanation, interpretation=interpretation,
        previous_state=previous_state, new_state=new_state,
        score_inputs={
            'engine_detection_type': candidate.detection_type,
            'severity': severity['inputs'],
            'confidence': confidence['inputs'],
            'high_impact': high_impact,
            'detector_version': ewc.DETECTOR_VERSION,
        },
        observed_at=observed_at,
    )


def _role_holders(state: RuleState, role: str) -> list[str]:
    roles = state.section('roles')
    holders = roles.get(role)
    if not isinstance(holders, list):
        holders = []
        roles[role] = holders
    return holders


def _evaluate_access_control(ctx: RuleContext, event: abi.DecodedLog, state: RuleState, observed_at: datetime) -> list[FindingDraft]:
    d = event.decoded
    name = event.event_name
    if name in ('RoleGranted', 'RoleRevoked'):
        role = str(d.get('role') or '')
        label = d.get('role_label') or f'role {short(role)}'
        account = d.get('account')
        holders = _role_holders(state, role)
        previously_held = account in holders
        if name == 'RoleGranted':
            if account and account not in holders and len(holders) < _MAX_TRACKED_ROLE_HOLDERS:
                holders.append(account)
                state.mark_dirty()
            return [draft for draft in [_privileged(
                ctx, event, observed_at=observed_at, actor=d.get('sender'),
                high_impact=role in abi.HIGH_IMPACT_ROLES,
                rule_key='access_control.role_granted', profile='privileged_role_changes',
                finding_type='privileged_role_granted', finding_class='privileged_configuration_change',
                title=f'Privileged role granted: {label}',
                explanation=(
                    f'{label} was granted to {short(account)} by {short(d.get("sender"))} on '
                    f'contract {short(event.contract_address)}.'
                ),
                interpretation=(
                    'A privileged role was granted to a new address. Holders of this role can perform '
                    'the operations the contract restricts to it, so this is a privileged configuration '
                    'change worth reviewing. Routine administration commonly produces this event.'
                    if not previously_held else
                    'A privileged role was granted to an address already observed holding it. This is a '
                    'redundant privileged configuration change.'
                ),
                previous_state={'account_held_role': True if previously_held else 'not observed in monitored history'},
                new_state={'account_holds_role': True, 'role': role, 'role_label': d.get('role_label')},
            )] if draft]
        if account in holders:
            holders.remove(account)
            state.mark_dirty()
        return [draft for draft in [_privileged(
            ctx, event, observed_at=observed_at, actor=d.get('sender'),
            high_impact=role in abi.HIGH_IMPACT_ROLES,
            rule_key='access_control.role_revoked', profile='privileged_role_changes',
            finding_type='privileged_role_revoked', finding_class='privileged_configuration_change',
            title=f'Privileged role revoked: {label}',
            explanation=(
                f'{label} was revoked from {short(account)} by {short(d.get("sender"))} on '
                f'contract {short(event.contract_address)}.'
            ),
            interpretation=(
                'A privileged role was removed from an address. This narrows who can perform the '
                'restricted operations and is a privileged configuration change.'
            ),
            previous_state={'account_held_role': True if previously_held else 'not observed in monitored history'},
            new_state={'account_holds_role': False, 'role': role, 'role_label': d.get('role_label')},
        )] if draft]
    if name == 'RoleAdminChanged':
        label = d.get('role_label') or f'role {short(d.get("role"))}'
        return [draft for draft in [_privileged(
            ctx, event, observed_at=observed_at, actor=event.initiator, high_impact=True,
            rule_key='access_control.role_admin_changed', profile='privileged_role_changes',
            finding_type='role_admin_changed', finding_class='privileged_configuration_change',
            title=f'Role administration changed: {label}',
            explanation=(
                f'The admin role that governs {label} changed on contract {short(event.contract_address)}.'
            ),
            interpretation=(
                'The role that can grant and revoke this privilege changed. This alters who controls '
                'the privilege itself and is a privileged configuration change.'
            ),
            previous_state={'admin_role': d.get('previous_admin_role'), 'admin_role_label': d.get('previous_admin_role_label')},
            new_state={'admin_role': d.get('new_admin_role'), 'admin_role_label': d.get('new_admin_role_label')},
        )] if draft]
    if name in ('OwnershipTransferred', 'OwnershipTransferStarted'):
        previous_owner, new_owner = d.get('previous_owner'), d.get('new_owner')
        if name == 'OwnershipTransferStarted':
            return [draft for draft in [_privileged(
                ctx, event, observed_at=observed_at, actor=previous_owner, high_impact=False,
                rule_key='access_control.ownership_transfer_started', profile='ownership_changes',
                finding_type='ownership_transfer_initiated', finding_class='administrative_change',
                title='Ownership transfer initiated',
                explanation=(
                    f'A two-step ownership transfer of contract {short(event.contract_address)} to '
                    f'{short(new_owner)} was initiated by {short(previous_owner)}.'
                ),
                interpretation=(
                    'Ownership will change only if the proposed owner accepts. This is an administrative '
                    'change in progress.'
                ),
                previous_state={'owner': previous_owner},
                new_state={'pending_owner': new_owner},
            )] if draft]
        if previous_owner == abi.ZERO_ADDRESS:
            title, finding_class, high_impact, override = 'Initial contract ownership assigned', 'review', False, 'low'
            interpretation = (
                'Ownership was assigned from the zero address, which is how a contract records its first '
                'owner at deployment or initialization. Recorded for review.'
            )
        elif new_owner == abi.ZERO_ADDRESS:
            title, finding_class, high_impact, override = 'Contract ownership renounced', 'administrative_change', True, None
            interpretation = (
                'Ownership was transferred to the zero address. Owner-restricted functions can no longer be '
                'called by anyone. This is an administrative change.'
            )
        else:
            title, finding_class, high_impact, override = 'Contract ownership transferred', 'administrative_change', True, None
            interpretation = (
                'Control of owner-restricted functions moved to a different address. This is an '
                'administrative change.'
            )
        section = state.section('ownership')
        section['owner'] = new_owner
        state.mark_dirty()
        return [draft for draft in [_privileged(
            ctx, event, observed_at=observed_at, actor=previous_owner if previous_owner != abi.ZERO_ADDRESS else event.initiator,
            high_impact=high_impact,
            rule_key='access_control.ownership_transferred', profile='ownership_changes',
            finding_type='ownership_transferred', finding_class=finding_class, title=title,
            explanation=(
                f'Ownership of contract {short(event.contract_address)} moved from {short(previous_owner)} '
                f'to {short(new_owner)}.'
            ),
            interpretation=interpretation,
            previous_state={'owner': previous_owner}, new_state={'owner': new_owner},
            severity_override=override,
        )] if draft]
    return []


def _evaluate_upgradeability(ctx: RuleContext, event: abi.DecodedLog, state: RuleState, observed_at: datetime) -> list[FindingDraft]:
    d = event.decoded
    proxy = state.section('proxy')
    name = event.event_name
    if name == 'Upgraded':
        previous = proxy.get('implementation')
        proxy['implementation'] = d.get('implementation')
        state.mark_dirty()
        return [draft for draft in [_privileged(
            ctx, event, observed_at=observed_at, actor=event.initiator, high_impact=True,
            rule_key='upgradeability.upgraded', profile='contract_upgrades',
            finding_type='contract_upgrade', finding_class='privileged_configuration_change',
            title='Contract implementation upgraded',
            explanation=(
                f'Proxy {short(event.contract_address)} now points to implementation '
                f'{short(d.get("implementation"))}.'
            ),
            interpretation=(
                'The logic behind this contract address changed. Every function the proxy exposes now '
                'runs the new implementation, so this is a privileged configuration change.'
            ),
            previous_state={'implementation': previous or 'not observed in monitored history'},
            new_state={'implementation': d.get('implementation')},
        )] if draft]
    if name == 'BeaconUpgraded':
        previous = proxy.get('beacon')
        proxy['beacon'] = d.get('beacon')
        state.mark_dirty()
        return [draft for draft in [_privileged(
            ctx, event, observed_at=observed_at, actor=event.initiator, high_impact=True,
            rule_key='upgradeability.beacon_upgraded', profile='contract_upgrades',
            finding_type='contract_upgrade', finding_class='privileged_configuration_change',
            title='Proxy beacon changed',
            explanation=f'Proxy {short(event.contract_address)} now uses beacon {short(d.get("beacon"))}.',
            interpretation=(
                'The beacon that supplies this proxy\'s implementation changed, which changes the logic '
                'behind the contract address. This is a privileged configuration change.'
            ),
            previous_state={'beacon': previous or 'not observed in monitored history'},
            new_state={'beacon': d.get('beacon')},
        )] if draft]
    if name == 'DiamondCut':
        cuts = d.get('facet_cuts') or []
        return [draft for draft in [_privileged(
            ctx, event, observed_at=observed_at, actor=event.initiator, high_impact=True,
            rule_key='upgradeability.diamond_cut', profile='contract_upgrades',
            finding_type='contract_upgrade', finding_class='privileged_configuration_change',
            title='Diamond facets changed',
            explanation=(
                f'Diamond {short(event.contract_address)} changed {len(cuts)} facet(s)'
                + (f' and ran initializer {short(d.get("init"))}.' if d.get('init') not in (None, abi.ZERO_ADDRESS) else '.')
            ),
            interpretation=(
                'Functions were added, replaced or removed on this diamond, which changes the logic behind '
                'the contract address. This is a privileged configuration change.'
            ),
            previous_state=None,
            new_state={'facet_cuts': cuts, 'init': d.get('init')},
        )] if draft]
    if name == 'AdminChanged':
        proxy['admin'] = d.get('new_admin')
        state.mark_dirty()
        return [draft for draft in [_privileged(
            ctx, event, observed_at=observed_at, actor=d.get('previous_admin'), high_impact=True,
            rule_key='upgradeability.admin_changed', profile='proxy_admin_changes',
            finding_type='proxy_admin_change', finding_class='privileged_configuration_change',
            title='Proxy admin changed',
            explanation=(
                f'The admin of proxy {short(event.contract_address)} changed from '
                f'{short(d.get("previous_admin"))} to {short(d.get("new_admin"))}.'
            ),
            interpretation=(
                'The address allowed to upgrade this proxy changed. This is a privileged configuration '
                'change affecting who controls future upgrades.'
            ),
            previous_state={'admin': d.get('previous_admin')},
            new_state={'admin': d.get('new_admin')},
        )] if draft]
    return []


def _evaluate_emergency(ctx: RuleContext, event: abi.DecodedLog, state: RuleState, observed_at: datetime) -> list[FindingDraft]:
    d = event.decoded
    paused = event.event_name == 'Paused'
    section = state.section('pause')
    section['paused'] = paused
    state.mark_dirty()
    return [draft for draft in [_privileged(
        ctx, event, observed_at=observed_at, actor=d.get('account'), high_impact=False,
        rule_key='emergency_control.paused' if paused else 'emergency_control.unpaused',
        profile='pause_unpause', finding_type='pause_state_change', finding_class='administrative_change',
        title='Contract paused' if paused else 'Contract unpaused',
        explanation=(
            f'Contract {short(event.contract_address)} was '
            f'{"paused" if paused else "unpaused"} by {short(d.get("account"))}.'
        ),
        interpretation=(
            'An emergency control was used to halt the contract\'s pausable functions. This is an '
            'administrative change; pausing is also a routine operational tool.'
            if paused else
            'The contract\'s pausable functions were resumed. This is an administrative change.'
        ),
        previous_state={'paused': not paused},
        new_state={'paused': paused},
    )] if draft]


def _evaluate_multisig(ctx: RuleContext, event: abi.DecodedLog, state: RuleState, observed_at: datetime) -> list[FindingDraft]:
    d = event.decoded
    safe = state.section('safe')
    name = event.event_name
    safe_address = short(event.contract_address)
    common = dict(ctx=ctx, event=event, observed_at=observed_at, actor=event.initiator,
                  profile='multisig_configuration', finding_class='privileged_configuration_change')
    if name in ('AddedOwner', 'RemovedOwner'):
        owners = safe.get('owners') if isinstance(safe.get('owners'), list) else None
        previous = list(owners) if owners is not None else None
        added = name == 'AddedOwner'
        if owners is not None:
            if added and d.get('owner') not in owners:
                owners.append(d.get('owner'))
            if not added and d.get('owner') in owners:
                owners.remove(d.get('owner'))
            state.mark_dirty()
        draft = _privileged(
            **common, high_impact=True,
            rule_key='multisig.owner_added' if added else 'multisig.owner_removed',
            finding_type='multisig_owner_change',
            title='Multisig owner added' if added else 'Multisig owner removed',
            explanation=f'{short(d.get("owner"))} was {"added to" if added else "removed from"} the owners of multisig {safe_address}.',
            interpretation=(
                'The set of signers who can approve this multisig\'s transactions changed. This is a '
                'privileged configuration change.'
            ),
            previous_state={'owners': previous} if previous is not None else {'owners': 'not observed in monitored history'},
            new_state={'owner_added' if added else 'owner_removed': d.get('owner')},
        )
        return [draft] if draft else []
    if name == 'ChangedThreshold':
        previous = safe.get('threshold')
        new = d.get('threshold')
        safe['threshold'] = new
        state.mark_dirty()
        lowered = isinstance(previous, int) and isinstance(new, int) and new < previous
        draft = _privileged(
            **common, high_impact=bool(lowered or new == 1),
            rule_key='multisig.threshold_changed', finding_type='multisig_threshold_change',
            title='Multisig threshold changed',
            explanation=(
                f'Multisig {safe_address} now requires {new} signature(s)'
                + (f' (previously {previous}).' if previous is not None else '.')
            ),
            interpretation=(
                'The number of signatures required to execute this multisig\'s transactions changed. '
                + ('It was lowered, which reduces how many signers must agree. ' if lowered else '')
                + 'This is a privileged configuration change.'
            ),
            previous_state={'threshold': previous if previous is not None else 'not observed in monitored history'},
            new_state={'threshold': new},
        )
        return [draft] if draft else []
    labels = {
        'EnabledModule': ('Multisig module enabled', 'module', True,
                          'A module can execute transactions through this multisig without collecting owner signatures, so enabling one is a privileged configuration change.'),
        'DisabledModule': ('Multisig module disabled', 'module', False,
                           'A module that could execute transactions through this multisig was removed. This is a privileged configuration change.'),
        'ChangedGuard': ('Multisig guard changed', 'guard', False,
                         'The guard that checks this multisig\'s transactions changed. This is a privileged configuration change.'),
        'ChangedFallbackHandler': ('Multisig fallback handler changed', 'handler', False,
                                   'The contract that handles calls this multisig does not implement itself changed. This is a privileged configuration change.'),
        'ChangedMasterCopy': ('Multisig implementation changed', 'implementation', True,
                              'The implementation behind this multisig changed. This is a privileged configuration change.'),
    }
    if name not in labels:
        return []
    title, key, high_impact, interpretation = labels[name]
    previous = safe.get(key) if key != 'module' else None
    if key != 'module':
        safe[key] = d.get(key)
        state.mark_dirty()
    draft = _privileged(
        **common, high_impact=high_impact,
        rule_key=f'multisig.{name.lower()}', finding_type='multisig_configuration_change',
        title=title,
        explanation=f'{title} on {safe_address}: {short(d.get(key))}.',
        interpretation=interpretation,
        previous_state=({key: previous} if previous is not None else None),
        new_state={key: d.get(key)},
    )
    return [draft] if draft else []


# ── token operations ─────────────────────────────────────────────────────────
def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _evaluate_transfer(ctx: RuleContext, event: abi.DecodedLog, state: RuleState, observed_at: datetime) -> list[FindingDraft]:
    d = event.decoded
    if d.get('token_standard') != 'erc20':
        return []
    amount = _decimal(d.get('value'))
    if amount is None or amount <= 0:
        return []
    token = event.contract_address
    sender, receiver = d.get('from'), d.get('to')
    is_mint = sender == abi.ZERO_ADDRESS
    is_burn = receiver in abi.BURN_ADDRESSES
    min_samples = int(ctx.config['min_baseline_samples'])
    drafts: list[FindingDraft] = []
    chain_id = ctx.target.get('chain_id')

    if is_mint or is_burn:
        baseline = state.baseline(token, 'mint_burn')
        if 'mint_burn' in ctx.profiles:
            multiple = Decimal(str(ctx.config['mint_burn_multiple']))
            telemetry = _telemetry_event(event, observed_at=observed_at, from_address=sender, to_address=receiver,
                                         amount=amount, asset_key=token, chain_id=chain_id)
            candidates = tdd.detect_mint_burn_irregularity(
                [telemetry],
                baselines={token: tdd.AssetBaseline(mint_burn_mean=baseline.mean if baseline.sample_count else None,
                                                    mint_burn_samples=baseline.sample_count)},
                config={'mint_burn_deviation_high': multiple, 'min_baseline_samples': min_samples},
            )
            if candidates:
                candidate = candidates[0]
                deviation = float(amount / baseline.mean) if baseline.mean > 0 else None
                kind = 'mint' if is_mint else 'burn'
                severity = tds.compute_severity(detection_type=candidate.detection_type, amount_deviation=deviation)
                confidence = tds.compute_confidence(
                    evidence_quality=candidate.evidence_quality, evidence_count=len(candidate.evidence),
                    signal_count=candidate.signal_count, baseline_deviation=deviation,
                    baseline_samples=baseline.sample_count, min_baseline_samples=min_samples,
                )
                drafts.append(_draft(
                    ctx, event, rule_key=f'token_operation.large_{kind}', profile='mint_burn',
                    finding_type=f'large_{kind}', finding_class='observed_anomaly',
                    title=f'Large {kind} observed',
                    severity=severity['level'], confidence=confidence['confidence'],
                    explanation=(
                        f'A {kind} of {amount} raw units of token {short(token)} was about {deviation:.1f}x '
                        f'the mean mint/burn size observed for this token ({baseline.sample_count} '
                        f'mint/burn events observed so far).' if deviation else f'A large {kind} of token {short(token)} was observed.'
                    ),
                    interpretation=(
                        f'This {kind} is unusually large relative to this token\'s own observed history. '
                        'Supply changes of this size are an observed anomaly worth reviewing; issuers also '
                        'perform large supply operations as part of normal business.'
                    ),
                    previous_state=None,
                    new_state={'kind': kind, 'amount_raw': str(amount), 'from': sender, 'to': receiver},
                    score_inputs={
                        'engine_detection_type': candidate.detection_type,
                        'severity': severity['inputs'], 'confidence': confidence['inputs'],
                        'baseline_mean': str(baseline.mean), 'baseline_samples': baseline.sample_count,
                        'multiple': str(multiple), 'detector_version': ewc.DETECTOR_VERSION,
                    },
                    observed_at=observed_at,
                ))
        baseline.add(amount, event.block_number)
        return drafts

    baseline = state.baseline(token, 'transfer')
    if 'large_transfers' in ctx.profiles:
        multiple = Decimal(str(ctx.config['large_transfer_multiple']))
        telemetry = _telemetry_event(event, observed_at=observed_at, from_address=sender, to_address=receiver,
                                     amount=amount, asset_key=token, chain_id=chain_id)
        candidates = tdd.detect_unusual_transfers(
            [telemetry],
            baselines={token: tdd.AssetBaseline(transfer_mean=baseline.mean if baseline.sample_count else None,
                                                transfer_samples=baseline.sample_count)},
            config={
                'amount_deviation_medium': multiple,
                'amount_deviation_high': multiple * 2,
                'min_baseline_samples': min_samples,
                # Single-event evaluation: fan-out needs several destinations and
                # can never fire here; the values are set for explicitness.
                'fan_out_min_destinations': 10 ** 9,
                'fan_out_window_seconds': 60,
            },
        )
        floor_raw = ctx.config.get('large_transfer_min_amount')
        above_floor = floor_raw is not None and amount >= Decimal(str(floor_raw))
        if candidates or above_floor:
            deviation = float(amount / baseline.mean) if baseline.mean > 0 and baseline.sample_count else None
            candidate = candidates[0] if candidates else None
            severity = tds.compute_severity(
                detection_type=candidate.detection_type if candidate else 'unusual_transfer',
                amount_deviation=deviation if candidate else None,
            )
            confidence = tds.compute_confidence(
                evidence_quality=candidate.evidence_quality if candidate else 'normalized_telemetry',
                evidence_count=len(candidate.evidence) if candidate else 1,
                baseline_deviation=deviation, baseline_samples=baseline.sample_count,
                min_baseline_samples=min_samples,
            )
            basis = (
                f'about {deviation:.1f}x the mean transfer observed for this token '
                f'({baseline.sample_count} transfers observed so far)' if candidate and deviation
                else f'at or above the configured floor of {floor_raw} raw units for this protocol'
            )
            drafts.append(_draft(
                ctx, event, rule_key='token_operation.large_transfer', profile='large_transfers',
                finding_type='large_transfer', finding_class='unusual_activity',
                title='Unusually large transfer observed',
                severity=severity['level'], confidence=confidence['confidence'],
                explanation=(
                    f'{amount} raw units of token {short(token)} moved from {short(sender)} to '
                    f'{short(receiver)}, {basis}.'
                ),
                interpretation=(
                    'This transfer is unusually large for this token. It is unusual activity worth '
                    'reviewing; treasury operations, redemptions and custody moves also produce transfers '
                    'of this size.'
                ),
                previous_state=None,
                new_state={'amount_raw': str(amount), 'from': sender, 'to': receiver, 'token': token},
                score_inputs={
                    'engine_detection_type': candidate.detection_type if candidate else 'unusual_transfer',
                    'severity': severity['inputs'], 'confidence': confidence['inputs'],
                    'baseline_mean': str(baseline.mean), 'baseline_samples': baseline.sample_count,
                    'multiple': str(multiple), 'absolute_floor': floor_raw,
                    'detector_version': ewc.DETECTOR_VERSION,
                },
                observed_at=observed_at,
            ))
    baseline.add(amount, event.block_number)
    return drafts


# ── oracle ───────────────────────────────────────────────────────────────────
def _oracle_expected_interval(ctx: RuleContext, stats: Baseline) -> float | None:
    configured = ctx.config.get('oracle_heartbeat_seconds')
    if configured:
        return float(configured)
    if stats.sample_count >= max(5, min(int(ctx.config['min_baseline_samples']), 20)) and stats.mean > 0:
        return float(stats.mean)
    return None


def _evaluate_oracle(ctx: RuleContext, event: abi.DecodedLog, state: RuleState, observed_at: datetime) -> list[FindingDraft]:
    if 'oracle_updates' not in ctx.profiles:
        return []
    d = event.decoded
    feed = event.contract_address
    feeds = state.section('oracle')
    feed_state = feeds.get(feed) if isinstance(feeds.get(feed), dict) else {}
    answer = _decimal(d.get('current'))
    updated_at = int(d.get('updated_at') or 0) or int(observed_at.timestamp())
    previous_answer = _decimal(feed_state.get('last_answer'))
    previous_at = feed_state.get('last_updated_at')
    drafts: list[FindingDraft] = []

    threshold = float(ctx.config['oracle_deviation_pct'])
    if answer is not None and previous_answer not in (None, Decimal(0)):
        change_pct = float(abs(answer - previous_answer) / abs(previous_answer) * 100)
        if change_pct >= threshold:
            severity = tds.compute_severity(detection_type='oracle_deviation', amount_deviation=change_pct / threshold)
            confidence = tds.compute_confidence(evidence_quality='event_logs', evidence_count=2, signal_count=1)
            drafts.append(_draft(
                ctx, event, rule_key='oracle.price_deviation', profile='oracle_updates',
                finding_type='oracle_price_deviation', finding_class='observed_anomaly',
                title='Unusual oracle price change observed',
                severity=severity['level'], confidence=confidence['confidence'],
                explanation=(
                    f'Feed {short(feed)} moved from {previous_answer} to {answer} ({change_pct:.2f}%) in one '
                    f'update, at or above the {threshold:g}% review threshold for this protocol.'
                ),
                interpretation=(
                    'The reported value changed by more than this protocol\'s review threshold in a single '
                    'update. This is an observed anomaly; genuine market or NAV moves also produce it.'
                ),
                previous_state={'answer': str(previous_answer)},
                new_state={'answer': str(answer), 'round_id': d.get('round_id'), 'updated_at': updated_at},
                score_inputs={'severity': severity['inputs'], 'confidence': confidence['inputs'],
                              'change_pct': round(change_pct, 4), 'threshold_pct': threshold,
                              'detector_version': ewc.DETECTOR_VERSION},
                observed_at=observed_at,
            ))

    interval_stats = state.baseline(feed, 'oracle_interval')
    if isinstance(previous_at, int) and updated_at > previous_at:
        interval = updated_at - previous_at
        expected = _oracle_expected_interval(ctx, interval_stats)
        multiple = float(ctx.config['oracle_stale_multiple'])
        if expected and interval > expected * multiple:
            severity = tds.compute_severity(detection_type='oracle_deviation', amount_deviation=interval / expected)
            drafts.append(_draft(
                ctx, event, rule_key='oracle.update_gap', profile='oracle_updates',
                finding_type='oracle_update_gap', finding_class='observed_anomaly',
                title='Oracle update gap observed',
                severity=severity['level'],
                confidence=tds.compute_confidence(evidence_quality='event_logs', evidence_count=2)['confidence'],
                explanation=(
                    f'Feed {short(feed)} went {interval} seconds between updates, more than {multiple:g}x its '
                    f'expected interval of about {int(expected)} seconds.'
                ),
                interpretation=(
                    'Consumers of this feed read a value older than its usual update cadence during the gap. '
                    'This is an observed anomaly in the feed\'s heartbeat.'
                ),
                previous_state={'last_updated_at': previous_at},
                new_state={'updated_at': updated_at, 'interval_seconds': interval},
                score_inputs={'severity': severity['inputs'], 'expected_interval_seconds': expected,
                              'multiple': multiple, 'detector_version': ewc.DETECTOR_VERSION},
                observed_at=observed_at,
            ))
        interval_stats.add(Decimal(interval), event.block_number)

    feeds[feed] = {
        'last_answer': str(answer) if answer is not None else feed_state.get('last_answer'),
        'last_updated_at': updated_at,
        'last_round_id': d.get('round_id'),
        'last_block': event.block_number,
    }
    state.mark_dirty()
    return drafts


def evaluate_heartbeat(
    ctx: RuleContext, *, state: RuleState, now: datetime, network: str, chain_id: int,
) -> list[FindingDraft]:
    """Open-ended heartbeat interruption for oracle targets, checked at poll time.

    No new log is needed: silence past the expected cadence IS the observation.
    Deduplicated per (feed, last update) so one interruption yields one finding.
    """
    if 'oracle_updates' not in ctx.profiles or ctx.target.get('target_type') != 'oracle':
        return []
    drafts: list[FindingDraft] = []
    feeds = state.section('oracle')
    multiple = float(ctx.config['oracle_stale_multiple'])
    for feed, feed_state in feeds.items():
        if not isinstance(feed_state, dict) or not isinstance(feed_state.get('last_updated_at'), int):
            continue
        stats = state.baseline(feed, 'oracle_interval')
        expected = _oracle_expected_interval(ctx, stats)
        if not expected:
            continue
        last = int(feed_state['last_updated_at'])
        elapsed = int(now.timestamp()) - last
        if elapsed <= expected * multiple:
            continue
        title = 'Oracle heartbeat interruption observed'
        explanation = (
            f'Feed {short(feed)} has not reported an update for {elapsed} seconds, more than {multiple:g}x its '
            f'expected interval of about {int(expected)} seconds.'
        )
        interpretation = (
            'The feed is quieter than its usual cadence, so its latest value may be stale for consumers. '
            'This is an observed anomaly; a feed can also pause updates deliberately.'
        )
        assert_careful_language(title, explanation, interpretation)
        severity = tds.compute_severity(detection_type='oracle_deviation', amount_deviation=elapsed / expected)
        drafts.append(FindingDraft(
            rule_key='oracle.heartbeat_interruption', detection_profile='oracle_updates',
            finding_type='oracle_heartbeat_interruption', finding_class='observed_anomaly',
            title=title, severity=_cap_severity(severity['level']),
            confidence=tds.compute_confidence(evidence_quality='normalized_telemetry', evidence_count=1)['confidence'],
            explanation=explanation, interpretation=interpretation,
            decoded={'feed': feed, 'last_updated_at': last, 'elapsed_seconds': elapsed},
            previous_state={'last_updated_at': last}, new_state={'elapsed_seconds': elapsed},
            dedupe_key=f'oracle.heartbeat_interruption:{feed}:{last}',
            score_inputs={'severity': severity['inputs'], 'expected_interval_seconds': expected,
                          'multiple': multiple, 'detector_version': ewc.DETECTOR_VERSION},
            observed_at=now, contract_address=feed,
        ))
    return drafts


# ── dispatcher ───────────────────────────────────────────────────────────────
_CATEGORY_RULES = {
    'access_control': _evaluate_access_control,
    'upgradeability': _evaluate_upgradeability,
    'emergency_control': _evaluate_emergency,
    'multisig': _evaluate_multisig,
    'token_operation': _evaluate_transfer,
    'oracle': _evaluate_oracle,
}


def evaluate_event(
    ctx: RuleContext, event: abi.DecodedLog, state: RuleState, *, observed_at: datetime | None = None,
) -> list[FindingDraft]:
    """Every finding one newly observed event produces (possibly none)."""
    when = observed_at or event.block_timestamp or datetime.now(timezone.utc)
    rule = _CATEGORY_RULES.get(event.spec.category)
    if rule is None or event.decode_status == 'undecoded':
        return []
    return rule(ctx, event, state, when)
