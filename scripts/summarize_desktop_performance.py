"""Summarize opt-in probe data without treating idle 60 FPS as interaction PASS."""
import argparse
from bisect import bisect_right
import json
from pathlib import Path


def statistics(samples):
    values = sorted(sample[1] for sample in samples)
    if not values:
        return dict(samples=0)
    def percentile(fraction):
        position = (len(values)-1)*fraction
        lower = int(position)
        return round(values[lower] + (values[min(lower+1, len(values)-1)]-values[lower])*(position-lower), 3)
    return dict(samples=len(values), p50_ms=percentile(.5), p95_ms=percentile(.95),
                p99_ms=percentile(.99), max_ms=values[-1], over_50ms=sum(v>50 for v in values),
                over_100ms=sum(v>100 for v in values), over_250ms=sum(v>250 for v in values))


def summarize(data, radius=.8):
    contexts = data.get('contexts', [])
    stamps = [row[0] for row in contexts]
    def context_at(stamp):
        index = bisect_right(stamps, stamp)-1
        return contexts[index][1] if index >= 0 else {}
    def visible(context):
        return context.get('visible') and context.get('exposed') and context.get('visibility') != 'Minimized'
    geometry = [row[0] for row in data.get('events', []) if row[1] in ('Move', 'Resize', 'WindowStateChange')]
    changes = [row[0] for row in data.get('events', []) if row[1]=='MessageContentChanged']
    def near(stamp, events):
        index = bisect_right(events, stamp)
        return any(abs(stamp-event) <= radius for event in events[max(0, index-1):index+1])
    def categories(sample):
        end, duration = sample
        start = end-duration/1000
        before, after = context_at(start), context_at(end)
        during = contexts[bisect_right(stamps, start):bisect_right(stamps, end)]
        output = ['all_samples']
        # Both ends must be visible; an unocclusion gap is not rendering work.
        if visible(before) and visible(after) and all(visible(c) for _, c in during):
            output.append('visible')
            if near(end, geometry):
                output.append('geometry_interaction')
            if before.get('response_active') and after.get('response_active'):
                output.append('response_active')
                if near(end, changes):
                    output.append('streaming')
                if near(end, geometry):
                    output.append('response_during_geometry')
                if before.get('section') == after.get('section') == 'Settings':
                    output.append('settings_during_response')
            if before.get('section') == after.get('section') == 'Settings':
                output.append('settings')
            if before.get('model_picker') and after.get('model_picker'):
                output.append('model_picker')
        else:
            output.append('hidden_or_transition')
        return output
    names = ('all_samples', 'visible', 'geometry_interaction', 'response_active', 'streaming',
             'response_during_geometry', 'settings', 'settings_during_response', 'model_picker', 'hidden_or_transition')
    groups = {name: {metric: [] for metric in ('frames', 'gui_intervals')} for name in names}
    for metric in ('frames', 'gui_intervals'):
        for sample in data.get(metric, []):
            for name in categories(sample):
                groups[name][metric].append(sample)
    screens = {str((c.get('screen'), c.get('dpr'))) for _, c in contexts}
    return dict(schema=1, geometry_events=len(geometry), content_events=len(changes),
                context_samples=len(contexts), observed_screen_dpr=sorted(screens),
                groups={name: {metric: statistics(samples) for metric, samples in values.items()} for name, values in groups.items()},
                limitations=['No automatic PASS: inspect the captured interactions and long intervals.',
                             'Native Windows modal moves may pause GUI timers while the render thread continues.',
                             'No mixed-DPI or acoustic claim is inferred from these timings.'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('probe', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = json.dumps(summarize(json.loads(args.probe.read_text(encoding='utf-8'))), indent=2)
    if args.output:
        args.output.write_text(result, encoding='utf-8')
    print(result)


if __name__ == '__main__':
    main()
