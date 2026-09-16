"""Bounded audio input for existing stateless analyzers; absolute timestamps."""
import tempfile
import wave
from pathlib import Path


def analyze_chunks(run, path, chunk_seconds=300, context_seconds=2):
    with wave.open(str(path), 'rb') as source:
        rate = source.getframerate()
        frames = source.getnframes()
        duration = frames/rate
        if duration <= chunk_seconds:
            return run(path)
        step = max(1, int(chunk_seconds*rate))
        context = int(context_seconds*rate)
        merged = None
        events, segments, best = [], [], {}
        with tempfile.TemporaryDirectory(prefix='audio-workbench-chunks-') as directory:
            target = Path(directory)/'chunk.wav'
            for first in range(0, frames, step):
                last = min(frames, first+step)
                read_first, read_last = max(0, first-context), min(frames, last+context)
                source.setpos(read_first)
                with wave.open(str(target), 'wb') as out:
                    out.setparams(source.getparams())
                    remaining = read_last-read_first
                    while remaining:
                        size = min(65536, remaining)
                        out.writeframes(source.readframes(size))
                        remaining -= size
                result = run(target)
                if merged is None:
                    merged = {k: v for k, v in result.items() if k not in ('events','segments','text','top_labels')}
                for key, destination in [('events', events), ('segments', segments)]:
                    for row in result.get(key, []):
                        start = float(row['start'])+read_first/rate
                        end = float(row['end'])+read_first/rate
                        if key == 'segments':
                            # One owning core window per transcript segment avoids overlap duplicates.
                            if not first/rate <= (start+end)/2 < last/rate:
                                continue
                            start, end = max(0, start), min(duration, end)
                        else:
                            start, end = max(first/rate, start), min(last/rate, end)
                        if end > start:
                            destination.append({**row, 'start': round(start, 3), 'end': round(end, 3)})
                for label in result.get('top_labels', []):
                    best[label['label']] = max(best.get(label['label'], 0), label['confidence'])
        merged['chunk_seconds'] = chunk_seconds
        merged['chunk_context_seconds'] = context_seconds
        merged['analysis_note'] = merged.get('analysis_note', '') + ' Blockweise Analyse; an Blockgrenzen kann Kontext verloren gehen.'
        if segments or merged.get('type') == 'transcript':
            merged['segments'] = segments
            merged['text'] = ' '.join(s['text'] for s in segments)
        else:
            merged['events'] = events
        if merged.get('type') == 'audio_events':
            merged['top_labels'] = [{'label': k, 'confidence': v} for k,v in sorted(best.items(), key=lambda pair: -pair[1])[:20]]
        if merged.get('type') == 'vad':
            active = sum(e['end']-e['start'] for e in events)
            merged['active_seconds'] = round(active, 2)
            merged['activity_ratio'] = round(active/max(duration, 1e-9), 3)
        return merged
