"""Bounded-memory mono PCM metrics and time-window visualization."""
import math
import wave
from array import array

try:
    import numpy as np
except ImportError:
    np = None


def samples(raw):
    if np is not None:
        return np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768.0
    return [v / 32768.0 for v in array('h', raw)]


def open_mono(path):
    wav = wave.open(str(path), 'rb')
    if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
        wav.close()
        raise ValueError('Die Arbeitskopie muss Mono / 16-bit PCM sein.')
    return wav


def audio_profile(path):
    count, squares, total, peak = 0, 0.0, 0.0, 0.0
    with open_mono(path) as wav:
        rate = wav.getframerate()
        while raw := wav.readframes(65536):
            values = samples(raw)
            count += len(values)
            if np is not None:
                squares += float(np.sum(values.astype(np.float64) ** 2))
                total += float(np.sum(values, dtype=np.float64))
                peak = max(peak, float(np.max(np.abs(values))))
            else:
                squares += sum(v*v for v in values)
                total += sum(values)
                peak = max(peak, max(abs(v) for v in values))
    rms = math.sqrt(squares / max(1, count))
    return {'type': 'audio_profile', 'duration_seconds': round(count/rate, 3), 'sample_rate': rate,
            'channels': 1, 'peak_dbfs': round(20*math.log10(max(peak, 1e-9)), 1),
            'rms_dbfs': round(20*math.log10(max(rms, 1e-9)), 1), 'crest_factor': round(peak/max(rms, 1e-9), 2),
            'dc_offset': round(total/max(count, 1), 6), 'analysis_note': 'Blockweise über die vollständige Mono-Arbeitskopie'}


def energy_levels(path):
    levels = []
    with open_mono(path) as wav:
        rate = wav.getframerate()
        duration = wav.getnframes()/rate
        frame_size = max(1, rate // 4)
        while raw := wav.readframes(frame_size):
            values = samples(raw)
            rms = math.sqrt(sum(float(v)*float(v) for v in values)/len(values))
            levels.append(20*math.log10(rms+1e-7))
    return levels, rate, duration, frame_size


def waveform_and_spectrogram(path, start=0.0, end=None):
    """Overview scans bounded blocks. Spectrum samples the WHOLE selected range.

    HTTP callers restrict zoom windows to 60s. Overview is a coarse sample, not
    a claim that every event is visible; the waveform retains every peak.
    """
    with open_mono(path) as wav:
        rate, count = wav.getframerate(), wav.getnframes()
        duration = count/rate
        start = max(0.0, min(float(start), duration))
        end = duration if end is None else min(duration, max(start, float(end)))
        first, last = int(start*rate), min(count, int(end*rate))
        length = last-first
        bins = []
        n_bins = min(1200, length)
        wav.setpos(first)
        for i in range(n_bins):
            size = (i+1)*length//n_bins - i*length//n_bins
            remaining, low, high = size, 1.0, -1.0
            while remaining:
                values = samples(wav.readframes(min(65536, remaining)))
                if not len(values):
                    break
                remaining -= len(values)
                block_low = float(values.min()) if np is not None else min(values)
                block_high = float(values.max()) if np is not None else max(values)
                low, high = min(low, block_low), max(high, block_high)
            bins.append({'min': round(low, 4), 'max': round(high, 4)})
        spec = None
        if np is not None and length:
            window_size = max(256, rate//10)
            columns = min(800, max(1, math.ceil(length/(rate*0.02))))
            spectra = []
            window = np.hanning(window_size).astype(np.float32)
            for i in range(columns):
                position = first + int(i*max(0, length-window_size)/max(1, columns-1))
                wav.setpos(position)
                frame = samples(wav.readframes(min(window_size, last-position)))
                frame = np.pad(frame, (0, window_size-len(frame)))
                spectra.append(np.abs(np.fft.rfft(frame*window)))
            db = 20*np.log10(np.asarray(spectra)+1e-6)
            low, high = float(np.percentile(db, 8)), float(np.percentile(db, 99))
            normalized = np.clip((db-low)/max(high-low, 1e-6), 0, 1)
            edges = np.linspace(0, normalized.shape[1], 97, dtype=int)
            matrix = np.round(np.add.reduceat(normalized, edges[:-1], axis=1) / np.diff(edges), 3).tolist()
            spec = {'values': matrix, 'frequency_max': rate/2, 'frequency_bins': 96,
                    'time_bins': columns, 'sampled': length > columns*rate*0.02}
        return {'type': 'waveform', 'sample_rate': rate, 'waveform': bins, 'spectrogram': spec,
                'start_seconds': start, 'end_seconds': end, 'duration_seconds': duration,
                'visualization_version': 2}
