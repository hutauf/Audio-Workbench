import io
import json
import shutil
import tempfile
import threading
import time
import unittest
import wave
from array import array
from pathlib import Path
from urllib.parse import parse_qs
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch

import library
import audio_views
import server
from analyzers.chunked import analyze_chunks
from analyzers.whisper_cpp import _segment_time


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_data, self.original_db = server.DATA_DIR, server.DB_PATH
        server.DATA_DIR = Path(self.temp.name)
        server.DB_PATH = server.DATA_DIR/'test.sqlite3'
        server.init_db()

    def tearDown(self):
        server.DATA_DIR, server.DB_PATH = self.original_data, self.original_db
        self.temp.cleanup()

    def seed(self, count=1):
        with server.connect_db() as conn:
            conn.executemany("INSERT INTO recordings(id,original_name,original_path,normalized_path,duration,size_bytes,created_at,status) VALUES(?,?,?,?,?,?,?,?)",
                [(f'{i:032x}', f'aufnahme-{i:05}.wav', 'unused', 'unused', i+1, 32, '2026-09-15T10:00:00+00:00', 'ready') for i in range(count)])

    def page(self, query=''):
        with server.connect_db() as conn:
            return library.list_recordings(conn, parse_qs(query))

    def test_ten_thousand_bounded_page_and_queries(self):
        self.seed(10000)
        started = time.perf_counter()
        with server.connect_db() as conn:
            statements = []
            conn.set_trace_callback(statements.append)
            page = library.list_recordings(conn, {'offset': ['9950']})
        elapsed = time.perf_counter()-started
        self.assertEqual(page['total'], 10000)
        self.assertEqual(len(page['items']), 50)
        self.assertLessEqual(len(statements), 5)  # no jobs query per recording
        self.assertLess(len(json.dumps(page)), 30000)
        self.assertNotIn('jobs', page['items'][0])
        self.assertEqual(len(self.page('limit=100000')['items']), 100)
        print(f'10,000 recordings: last page {elapsed*1000:.1f} ms; {len(statements)} SQL statements; {len(json.dumps(page))} bytes')

    def test_search_tags_confidence_and_rerun_replaces_index(self):
        self.seed(2)
        rid = f'{0:032x}'
        server.save_result(rid, 'yamnet', {'type':'audio_events','events':[{'start':12,'end':14,'label':'Meow','confidence':0.8}]})
        result = self.page('q=Katze&source=sound&confidence=0.5')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['matches'][0]['start'], 12)
        self.assertEqual(self.page('q=Katze&confidence=0.9')['total'], 0)
        server.save_result(rid, 'whisper_cpp', {'type':'transcript','segments':[{'start':30,'end':32,'text':'Besprechung über Urlaub'}]})
        self.assertEqual(self.page('q=Urlaub&source=transcript')['total'], 1)
        with server.connect_db() as conn:
            library.set_tags(conn, rid, ['Garten', 'garten', ' Sommer '])
        self.assertEqual(self.page('tag=GARTEN')['total'], 1)
        self.assertEqual(self.page('q=Sommer&source=tags')['total'], 1)
        server.save_result(rid, 'yamnet', {'type':'audio_events','events':[]})
        self.assertEqual(self.page('q=Katze')['total'], 0)
        self.assertEqual(self.page('q=Urlaub')['total'], 1)

    def test_sort_date_stability_and_plain_text_queries(self):
        self.seed(110)
        first = self.page('sort=newest')
        second = self.page('sort=newest&offset=50')
        self.assertFalse({r['id'] for r in first['items']} & {r['id'] for r in second['items']})
        self.assertEqual(self.page('sort=longest')['items'][0]['duration'], 110)
        self.assertEqual(self.page('before=2026-09-14')['total'], 0)
        self.assertEqual(self.page('after=2026-09-15&before=2026-09-15')['total'], 110)
        self.assertEqual(self.page('q=%22%29%20OR%201%3D1--')['total'], 0)
        self.assertEqual(self.page('q=%22')['total'], 0)
        self.assertEqual(self.page('q=aufnahme-00001.wav')['total'], 1)
        with self.assertRaises(ValueError):
            self.page('confidence=nan')

    def test_upgrade_indexes_existing_results_and_get_is_read_only(self):
        self.seed()
        rid = f'{0:032x}'
        server.save_result(rid, 'whisper_cpp', {'type':'transcript','text':'Altbestand'})
        with server.connect_db() as conn:
            conn.execute("DELETE FROM library_meta WHERE key='index-v1'")
        server.init_db()
        server.init_db()
        self.assertEqual(self.page('q=Altbestand')['total'], 1)
        server.detail_recording(rid)
        self.assertEqual(server.fetch_one('SELECT count(*) AS n FROM jobs')['n'], 0)

    def test_restart_requeues_running_jobs(self):
        self.seed()
        rid = f'{0:032x}'
        server.execute("UPDATE recordings SET status='processing' WHERE id=?", (rid,))
        server.execute("INSERT INTO jobs(id,recording_id,analyzer_id,status,created_at) VALUES('j',?,'audio_profile','running','now')", (rid,))
        server.resume_pending_jobs()
        self.assertEqual(server.recording_row(rid)['status'], 'queued')
        self.assertEqual(server.fetch_one("SELECT status FROM jobs WHERE id='j'")['status'], 'queued')

    def test_http_import_processing_search_tags_and_rerun(self):
        http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        def request(path, data=None, headers=None):
            req = Request(f'http://127.0.0.1:{http.server_port}'+path, data=data, headers=headers or {})
            try:
                with urlopen(req, timeout=10) as response:
                    return response.status, response.read()
            except HTTPError as exc:
                with exc:
                    return exc.code, exc.read()
        try:
            audio = io.BytesIO()
            with wave.open(audio, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(array('h', [1000, -1000]*16000).tobytes())
            body = b'--test-boundary\r\nContent-Disposition: form-data; name="file"; filename="cat.wav"\r\nContent-Type: audio/wav\r\n\r\n'+audio.getvalue()+b'\r\n--test-boundary--\r\n'
            code, data = request('/api/import', body, {'Content-Type':'multipart/form-data; boundary=test-boundary', 'X-File-Modified':'1700000000000'})
            self.assertEqual(code, 201)
            imported = json.loads(data)
            rid = imported['id']
            self.assertEqual(len(imported['jobs']), len(server.ANALYZERS))
            self.assertIsNotNone(imported['file_modified_at'])
            self.assertEqual(request(f'/api/recordings/{rid}/rerun', b'')[0], 409)
            # Fixture already has the required PCM format; no FFmpeg/model install.
            def normalize(original, normalized):
                normalized.write_bytes(original.read_bytes())
            baseline = {key: server.ANALYZERS[key] for key in ('audio_profile', 'waveform', 'vad_energy')}
            with patch.object(server, 'run_ffmpeg', normalize), patch.object(server, 'ANALYZERS', baseline):
                server.process_recording(rid)
            detail = server.detail_recording(rid)
            self.assertEqual(detail['duration'], 2)
            self.assertTrue(all(j['status'] == 'done' for j in detail['jobs'] if j['analyzer_id'] in baseline))
            code, data = request(f'/api/recordings/{rid}/visualization?start=1&end=2')
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(data)['start_seconds'], 1)
            self.assertEqual(request(f'/api/recordings/{rid}/visualization?end=100')[0], 400)
            self.assertEqual(request('/api/recordings?confidence=nan')[0], 400)
            self.assertEqual(request(f'/api/recordings/{rid}/tags', b'{"tags":["Garten"]}', {'Content-Type':'application/json'})[0], 200)
            self.assertEqual(json.loads(request('/api/recordings?q=garten')[1])['total'], 1)
            self.assertEqual(json.loads(request('/api/tags')[1])[0]['tag'], 'garten')
            self.assertEqual(request(f'/media/{rid}/audio', headers={'Range':'bytes=0-9'})[0], 206)
            self.assertEqual(request(f'/api/recordings/{rid}/rerun?analyzer_id=waveform', b'')[0], 200)
            self.assertEqual(request(f'/api/recordings/{rid}/rerun', b'')[0], 409)
            self.assertEqual(request('/api/recordings/ffffffff/rerun', b'')[0], 404)
            self.assertEqual(request(f'/api/recordings/{rid}/rerun?analyzer_id=missing', b'')[0], 400)
        finally:
            http.shutdown()
            http.server_close()
            thread.join()


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'audio.wav'

    def tearDown(self):
        self.temp.cleanup()

    def wav(self, frames):
        with wave.open(str(self.path), 'wb') as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            out.writeframes(array('h', frames).tobytes())

    def test_single_sample_and_empty(self):
        for frames in ([], [1000], [0]*80):
            self.wav(frames)
            result = audio_views.waveform_and_spectrogram(self.path)
            self.assertEqual(len(result['waveform']), len(frames))
            self.assertEqual(audio_views.audio_profile(self.path)['duration_seconds'], round(len(frames)/16000, 3))
            server.vad_energy(self.path)

    def test_late_signal_and_zoom(self):
        self.wav([0]*(16000*19) + [16000]*16000)
        view = audio_views.waveform_and_spectrogram(self.path, 19, 20)
        self.assertEqual(view['start_seconds'], 19)
        self.assertGreater(view['waveform'][-1]['max'], 0.4)
        overview = audio_views.waveform_and_spectrogram(self.path)
        self.assertGreater(overview['waveform'][-1]['max'], 0.4)
        self.assertEqual(overview['end_seconds'], 20)
        self.assertLessEqual(len(overview['waveform']), 1200)
        if audio_views.np is not None:
            self.assertNotEqual(overview['spectrogram']['values'][0], overview['spectrogram']['values'][-1])

    def test_chunk_offsets_and_bounded_input(self):
        self.wav([0]*16000*12)
        sizes=[]
        def fake(path):
            with wave.open(str(path), 'rb') as wav:
                duration=wav.getnframes()/wav.getframerate()
                sizes.append(duration)
            return {'type':'audio_events','events':[{'start':0,'end':duration,'label':'Cat','confidence':0.8}], 'top_labels':[{'label':'Cat','confidence':0.8}]}
        result=analyze_chunks(fake, self.path, 5, 1)
        self.assertEqual([(e['start'],e['end']) for e in result['events']], [(0,5),(5,10),(10,12)])
        self.assertLessEqual(max(sizes), 7)

    def test_byte_ranges(self):
        self.path.write_bytes(b'0123456789')
        class Handler:
            def __init__(self, value): self.headers={'Range':value}; self.wfile=io.BytesIO(); self.response_headers={}
            def send_response(self, status): self.status=status
            def send_header(self, key,value): self.response_headers[key]=value
            def end_headers(self): pass
        for value,code,body in [('bytes=2-4',206,b'234'), ('bytes=-3',206,b'789'), ('bytes=100-',416,b''), ('bytes=8-2',416,b'')]:
            handler=Handler(value)
            server.read_file_range(self.path,handler)
            self.assertEqual(handler.status,code)
            self.assertEqual(handler.wfile.getvalue(),body)

    def test_whisper_offsets_are_milliseconds_including_short_segments(self):
        self.assertEqual(_segment_time({'offsets': {'from': 50}}, 'from'), 0.05)
        self.assertEqual(_segment_time({'timestamps': {'from':'00:00:00.000'}, 'offsets': {'from': 50}}, 'from'), 0)
        self.assertEqual(_segment_time({'start': 125.5}, 'start'), 125.5)

    def test_cancelled_audio_range_is_normal(self):
        self.path.write_bytes(b'0123456789')
        class ClosedClient:
            headers = {'Range': 'bytes=0-4'}
            def __init__(self): self.wfile = self
            def send_response(self, value): pass
            def send_header(self, key, value): pass
            def end_headers(self): pass
            def write(self, data): raise ConnectionResetError('browser seek')
        server.read_file_range(self.path, ClosedClient())

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg not installed; no installation attempted')
    def test_real_ffmpeg_mono_normalization(self):
        self.wav([0, 1000, -1000, 0]*400)
        target = self.path.with_name('normalized.wav')
        server.run_ffmpeg(self.path, target)
        with wave.open(str(target), 'rb') as wav:
            self.assertEqual((wav.getnchannels(), wav.getframerate(), wav.getsampwidth()), (1, 16000, 2))
            self.assertEqual(wav.getnframes(), 1600)


if __name__ == '__main__':
    unittest.main()
