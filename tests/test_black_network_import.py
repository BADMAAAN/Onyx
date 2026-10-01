import struct
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Timer
from unittest import TestCase
from unittest.mock import patch,Mock
from kk.core.gguf_metadata import inspect_gguf
from kk.core.desktop_settings import DesktopSettings
from kk.core.project_git import ProjectGit


class BlackNetworkImportTests(TestCase):
    def test_non_tools_model_cannot_execute_invented_call(self):
        from kk.providers.ollama_provider import OllamaProvider
        from kk.providers.base import ProviderError
        provider=OllamaProvider('http://127.0.0.1:11434',context_window=4096)
        provider.model_manager=Mock();provider.model_manager.verify.return_value={'capabilities':['completion'],'context_limit':2048}
        raw={'done':True,'message':{'tool_calls':[{'function':{'name':'run_terminal','arguments':{}}}]}}
        with patch('kk.providers.ollama_provider.request_json',return_value=raw) as api:
            with self.assertRaises(ProviderError):provider.chat('fixture:1b',[],tools=[{'function':{'name':'run_terminal'}}])
            body=api.call_args.kwargs['body']
            self.assertNotIn('tools',body);self.assertNotIn('think',body);self.assertEqual(body['options']['num_ctx'],2048)

    def test_gguf_metadata_rejects_adapter_shards_and_truncation(self):
        def string(s):
            b=s.encode();return struct.pack('<Q',len(b))+b
        with TemporaryDirectory() as d:
            p=Path(d)/'model.gguf'
            for extra,error in [({},None),({'general.type':'adapter'},'Adapters'),({'split.count':2},'Split')]:
                rows={'general.architecture':'llama',**extra}
                b=b'GGUF'+struct.pack('<IQQ',3,1,len(rows))
                for key,value in rows.items():
                    b+=string(key)+struct.pack('<I',8 if isinstance(value,str) else 4)+(string(value) if isinstance(value,str) else struct.pack('<I',value))
                p.write_bytes(b)
                if error:
                    with self.assertRaisesRegex(ValueError,error):inspect_gguf(p)
                else:self.assertEqual(inspect_gguf(p)['architecture'],'llama')
            p.write_bytes(b'GGUF'+struct.pack('<IQQ',3,1,100))
            with self.assertRaises(ValueError):inspect_gguf(p)

    def test_git_off_blocks_process_creation_and_toggle_stops_owned_process(self):
        with TemporaryDirectory() as d:
            settings=DesktopSettings(Path(d));git=ProjectGit(settings)
            with patch('subprocess.Popen') as start:
                with self.assertRaises(PermissionError):git.network_run([sys.executable,'-c','pass'],d,2)
                start.assert_not_called()
            settings.save('internet','explicit')
            timer=Timer(.3,lambda:settings.save('internet','off'));timer.start()
            try:
                with self.assertRaisesRegex(PermissionError,'cancelled'):
                    git.network_run([sys.executable,'-c','import time;time.sleep(20)'],d,3)
            finally:timer.join()

    def test_missing_tts_never_opens_network(self):
        from kk.voice.tts_silero import SileroTTS
        with TemporaryDirectory() as d, patch('socket.socket') as socket:
            tts=SileroTTS.__new__(SileroTTS);tts.model_path=Path(d)/'missing.pt'
            with self.assertRaises(FileNotFoundError):tts.ensure_model()
            socket.assert_not_called()

    def test_missing_stt_stays_local_only(self):
        from kk.voice.stt_faster_whisper import FasterWhisperSTT
        from huggingface_hub.errors import LocalEntryNotFoundError
        with patch('faster_whisper.utils.download_model',side_effect=LocalEntryNotFoundError('not installed')) as cache,patch('kk.voice.stt_faster_whisper.WhisperModel',side_effect=FileNotFoundError('not installed')) as load:
            stt=FasterWhisperSTT(model_name='missing-fixture',device='cpu')
            with self.assertRaises(FileNotFoundError):stt._load()
            self.assertTrue(cache.call_args.kwargs['local_files_only'])
            self.assertTrue(load.call_args.kwargs['local_files_only'])
