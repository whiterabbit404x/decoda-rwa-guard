#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('--summary-path', default='services/api/artifacts/live_evidence/latest/summary.json')
args=p.parse_args()
s=json.loads(Path(args.summary_path).read_text())
print('controlled_pilot_ready:', s.get('controlled_pilot_ready'))
print('broad_self_serve_ready:', s.get('broad_self_serve_ready'))
print('enterprise_procurement_ready:', s.get('enterprise_procurement_ready'))
