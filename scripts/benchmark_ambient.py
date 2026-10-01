"""Measure the standalone ambient loop without starting an LLM."""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep, monotonic

import psutil

from kk.ambient import AmbientContextManager
from kk.ambient.manager import idle_seconds
from kk.vision.uia import UIAutomationService


def measure(seconds=30):
    process = psutil.Process(os.getpid())
    with TemporaryDirectory() as folder:
        manager = AmbientContextManager(Path(folder)/'ambient.db', uia=UIAutomationService(), interval=2, screen_interval=8)
        manager.start()
        sleep(3)  # exclude the initial window enumeration and imports
        cpu_start = process.cpu_times()
        io_start = process.io_counters()
        t0 = monotonic()
        events_start = len(manager.timeline.events)
        writes_start = manager.timeline.writes
        input_idle_start = idle_seconds()
        sleep(seconds)
        elapsed = monotonic() - t0
        cpu_end = process.cpu_times()
        io_end = process.io_counters()
        result = {
            'elapsed_seconds': round(elapsed,2),
            'cpu_percent_of_one_core': round(100*((cpu_end.user+cpu_end.system)-(cpu_start.user+cpu_start.system))/elapsed,2),
            'rss_mb': round(process.memory_info().rss/1048576,2),
            'disk_write_bytes_per_second': round((io_end.write_bytes-io_start.write_bytes)/elapsed,2),
            'sqlite_writes_per_minute': round((manager.timeline.writes-writes_start)*60/elapsed,2),
            'events_per_minute': round((len(manager.timeline.events)-events_start)*60/elapsed,2),
            'screen_change_count': len(manager.screen_changes),
            'screen_changes_by_monitor': {m['monitor_id']: sum(c['monitor_id']==m['monitor_id'] for c in manager.screen_changes) for m in manager.monitors},
            'monitors': len(manager.monitors),
            'qwen3_vl_calls': 0,
            'input_idle_seconds_start': round(input_idle_start,1),
            'input_idle_seconds_end': round(idle_seconds(),1),
        }
        manager.stop()
        return result


if __name__ == '__main__':
    import sys
    if len(sys.argv)>1 and sys.argv[1]=='--idle':
        deadline = monotonic()+45
        while idle_seconds()<5 and monotonic()<deadline:
            sleep(1)
        result = measure(float(sys.argv[2]) if len(sys.argv)>2 else 20)
        result['idle_qualified'] = result['input_idle_seconds_end'] >= result['elapsed_seconds']
    else:
        result = measure(float(sys.argv[1]) if len(sys.argv)>1 else 30)
    print(json.dumps(result, ensure_ascii=False, indent=2))
