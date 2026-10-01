"""Measure an actual Windows ambient loop; never synthesize idle input."""
from __future__ import annotations

import json
import os
from pathlib import Path
import statistics
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep

import psutil

from kk.ambient import AmbientContextManager
from kk.ambient.manager import idle_seconds
from kk.vision.uia import UIAutomationService


def stats(values):
    if not values:
        return None
    ordered = sorted(values)
    return {'median': round(statistics.median(values), 3),
            'p95': round(ordered[min(len(ordered)-1, int(.95*len(ordered)))], 3),
            'max': round(max(values), 3)}


def measure(seconds=300, screen_interval=8):
    process = psutil.Process(os.getpid())
    psutil.cpu_percent(interval=None)
    with TemporaryDirectory() as folder:
        path = Path(folder)/'ambient.db'
        manager = AmbientContextManager(path, uia=UIAutomationService(), screen_interval=screen_interval)
        manager.start()
        sleep(3)
        samples = []
        start = monotonic()
        prior_cpu = process.cpu_times()
        prior_io = process.io_counters()
        prior_events = len(manager.timeline.events)
        prior_writes = manager.timeline.writes
        prior_screens = manager.screen_samples
        initial = {'rss_mb': round(process.memory_info().rss/1048576,3),
                   'handles': process.num_handles(), 'threads': process.num_threads(),
                   'db_size_bytes': path.stat().st_size}
        while monotonic()-start < seconds:
            sleep(min(1, max(.01, seconds-(monotonic()-start))))
            now = monotonic()
            cpu = process.cpu_times()
            io = process.io_counters()
            elapsed = max(.001, now-(samples[-1]['at'] if samples else start))
            sample = {'at': now, 'idle_seconds': idle_seconds(),
                      'process_cpu_percent_one_core': 100*((cpu.user+cpu.system)-(prior_cpu.user+prior_cpu.system))/elapsed,
                      'machine_cpu_percent': psutil.cpu_percent(interval=None),
                      'rss_mb': process.memory_info().rss/1048576,
                      'disk_write_bytes': max(0, io.write_bytes-prior_io.write_bytes),
                      'events': max(0,len(manager.timeline.events)-prior_events),
                      'sqlite_writes': manager.timeline.writes-prior_writes,
                      'screen_samples': manager.screen_samples-prior_screens}
            samples.append(sample)
            prior_cpu, prior_io = cpu, io
            prior_events = len(manager.timeline.events)
            prior_writes = manager.timeline.writes
            prior_screens = manager.screen_samples
        duration = monotonic()-start
        idle_anchor = [s['at']-s['idle_seconds'] for s in samples]
        result = {'duration_seconds': round(duration,2),
                  'idle_qualified': min(s['idle_seconds'] for s in samples) >= 5 and max(idle_anchor)-min(idle_anchor) < 2,
                  'idle_seconds_start': round(samples[0]['idle_seconds'],2),
                  'idle_seconds_end': round(samples[-1]['idle_seconds'],2),
                  'monitors': manager.monitors,
                  'screen_samples_by_monitor': manager.screen_samples_by_monitor,
                  'db_size_bytes': path.stat().st_size,
                  'initial': initial,
                  'final': {'rss_mb': round(process.memory_info().rss/1048576,3),
                            'handles': process.num_handles(), 'threads': process.num_threads(),
                            'db_size_bytes': path.stat().st_size,
                            'queue_events': len(manager.timeline.events),
                            'frames': len(manager.frames),
                            'sample_errors': manager.sample_errors,
                            'uia_errors': manager.uia_errors,
                            'screen_errors': manager.screen_errors},
                  'qwen3_vl_calls': 0}
        for name in ('process_cpu_percent_one_core','machine_cpu_percent','rss_mb','disk_write_bytes','events','sqlite_writes','screen_samples'):
            values = [s[name] for s in samples]
            result[name] = {**stats(values), 'total': round(sum(values),3)}
        manager.stop()
        return result


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    duration = float(sys.argv[1]) if len(sys.argv)>1 else 300
    interval = float(sys.argv[2]) if len(sys.argv)>2 else 8
    print(json.dumps(measure(duration,interval),ensure_ascii=False,indent=2))
