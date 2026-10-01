from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from queue import Queue, Empty
from time import sleep
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
from time import monotonic
import logging
import numpy as np

from kk.voice.streaming import TurnManager, StreamingSTT
from kk.voice.loop import VoiceLoop
from kk.voice.stt_faster_whisper import FasterWhisperSTT, Transcription
from kk.core.state import StateMachine, AgentState
from kk.core.desktop_commands import parse_plan, run_desktop, DesktopContext
from kk.core.personal_intents import PersonalIntentRegistry
from kk.core.task import AgentTask, CancellationToken
from kk.core.task_batch import TaskBatch
from kk.ui.mic_pointer import MicPointer
from kk.tools.windows.codex_integration import explicit_task_request


def heard(text, committed=False):
    return Transcription(text, 'ru', 1, 1, .1, text, text, committed=committed)


class CommittedVoiceTests(TestCase):
    def loop(self):
        loop = VoiceLoop(Mock(), Mock(), Mock(), StateMachine(), Mock())
        self.addCleanup(loop.speech.close)
        loop.speech = Mock()
        loop.capture = SimpleNamespace(controls=Queue())
        loop.audio_focus = Mock()
        loop.decoder = Mock()
        return loop

    def test_partial_never_dispatches_even_valid_command(self):
        loop = self.loop()
        loop.dispatch('открой Discord', heard('открой Discord'))
        self.assertTrue(loop.commands.empty())

    def test_final_dispatches(self):
        loop = self.loop()
        loop.worker = Mock(); loop.worker.is_alive.return_value = True
        loop.dispatch('открой Discord', heard('открой Discord', True))
        self.assertEqual(loop.commands.get_nowait()[0], 'открой Discord')

    def test_tap_record_commit_ducking(self):
        loop = self.loop()
        loop.mic_toggle()
        self.assertTrue(loop.manual_recording)
        self.assertEqual(loop.state.state, AgentState.RECORDING)
        loop.audio_focus.acquire.assert_called_once()
        loop.mic_toggle()
        self.assertEqual(loop.state.state, AgentState.COMMITTING)
        self.assertEqual([loop.capture.controls.get_nowait() for _ in range(2)], ['start','commit'])
        loop.audio_focus.release.assert_not_called()

    def test_cancel_invalidates_pending_decode_restores_audio(self):
        loop = self.loop()
        loop.mic_start(); loop.mic_commit(); loop.mic_cancel()
        self.assertFalse(loop.manual_pending)
        loop.decoder.invalidate.assert_called()
        loop.audio_focus.release.assert_called_once()
        self.assertTrue(loop.commands.empty())
        self.assertEqual(loop.state.state, AgentState.IDLE)

    def test_manual_silence_never_commits_and_cancel_erases(self):
        turn = TurnManager()
        turn.begin_manual(0)
        events = []
        for i in range(800): events += turn.feed(bytes(960), i < 20, i*.03)
        self.assertFalse(any(e[0]=='final_audio' for e in events))
        self.assertTrue(turn.frames)
        turn.reset()
        self.assertFalse(turn.frames)

    def test_manual_commit_has_entire_buffer(self):
        turn = TurnManager(); turn.begin_manual(0)
        for i in range(100): turn.feed(bytes([i%255])*960, i < 10, i*.03)
        result = turn.commit_manual()
        self.assertEqual(len(result[0][2]), 96000)
        self.assertFalse(turn.frames)

    def test_unfinished_waits_past_long_pause(self):
        for phrase in ['открой', 'а потом', 'и ещё', 'сделай', 'ну', 'подожди']:
            turn = TurnManager()
            for i in range(10): turn.feed(bytes(960), True, i*.03)
            turn.hypothesis(turn.sequence, phrase)
            events=[]
            for i in range(200): events += turn.feed(bytes(960), False, .3+i*.03)
            self.assertFalse(any(e[0]=='final_audio' for e in events), phrase)

    def test_new_speech_invalidates_endpoint_hypothesis(self):
        turn = TurnManager()
        for i in range(10): turn.feed(bytes(960), True, i*.03)
        turn.hypothesis(turn.sequence, 'открой Chrome')
        for i in range(10): turn.feed(bytes(960), True, .3+i*.03)
        events = []
        for i in range(60): events += turn.feed(bytes(960), False, .6+i*.03)
        self.assertFalse(any(e[0]=='final_audio' for e in events))

    def test_empty_and_limit_discard(self):
        turn = TurnManager(); turn.begin_manual(0)
        self.assertEqual(turn.commit_manual()[0][0], 'manual_cancelled')
        turn.manual_maximum=.1; turn.begin_manual(0)
        self.assertEqual(turn.feed(bytes(960), True, .2)[0][0], 'discard')

    def test_full_decode_reuses_model_cpu_never_called(self):
        stt = FasterWhisperSTT()
        stt.second_pass = Mock()
        bad = heard('громкость стол'); good = heard('громкость сто')
        stt.transcribe = Mock(side_effect=[bad,good])
        audio = np.zeros(16000,dtype=np.float32)
        result=stt.transcribe_final(audio)
        self.assertTrue(result.committed and result.accepted)
        self.assertEqual(result.chosen_stt_pass,'turbo_retry')
        stt.second_pass.transcribe.assert_not_called()
        for call in stt.transcribe.call_args_list: self.assertIs(call.args[0],audio)
        self.assertFalse(stt.transcribe.call_args_list[0].kwargs['vad_filter'])

    def test_partial_tool_boundary(self):
        from kk.memory.database import MemoryDatabase
        from kk.permissions.policy import PermissionPolicy, PermissionLevel
        from kk.tools.registry import ToolRegistry, Tool
        with TemporaryDirectory() as folder:
            db=MemoryDatabase(Path(folder)/'memory.db')
            self.addCleanup(db.close)
            tools=ToolRegistry(PermissionPolicy(PermissionLevel.ACTION,Path(folder)/'STOP',lambda _:True),db,logging.getLogger('test'))
            action=Mock(return_value=True)
            tools.register(Tool('action','',{'type':'object','properties':{}},PermissionLevel.ACTION,action))
            token=CancellationToken(); token.voice_evidence=heard('открой Discord')
            tools.cancellation.set(token)
            self.assertFalse(tools.execute('action').ok)
            action.assert_not_called()
            token.voice_evidence.committed=True
            self.assertTrue(tools.execute('action').ok)
            db.close()

    def streaming_loop(self):
        """Real capture/turn/decoder/loop; only the microphone and STT model are fakes."""
        frames=Queue(); entered=Event(); release=Event()
        class Input:
            def __init__(self,*args): pass
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,count):
                try: return frames.get(timeout=.03), False
                except Empty: return bytes(960),False
        stt=SimpleNamespace(microphone=None,cancel_event=Event(),
            transcribe=Mock(return_value=heard('открой Discord')))
        def final(audio,cancelled):
            entered.set(); release.wait(3)
            return heard('открой Discord',True)
        stt.transcribe_final=Mock(side_effect=final)
        loop=VoiceLoop(Mock(),stt,Mock(),StateMachine(),Mock())
        self.addCleanup(loop.speech.close)
        loop.speech=Mock(); loop.speech.render_reference.return_value=np.zeros(480)
        loop.worker=Mock(); loop.worker.is_alive.return_value=True
        loop.audio_focus=Mock(); loop.wakeword=True
        loop.streaming_options=dict(partial_interval=.03,minimum_speech=.03,silence=.09)
        for target,value in [('kk.voice.audio_input.AudioInput',Input),
                             ('kk.voice.audio_devices.find_device',lambda *a,**kw:0),
                             ('kk.voice.streaming.VAD.process',lambda self,pcm:(pcm[0]!=0,.02))]:
            patcher=patch(target,value); patcher.start(); self.addCleanup(patcher.stop)
        thread=Thread(target=loop._streaming_session)
        thread.start()
        deadline=monotonic()+2
        while loop.capture is None and monotonic()<deadline: sleep(.01)
        def close():
            release.set(); loop._exit.set(); thread.join(3)
            self.assertFalse(thread.is_alive())
        self.addCleanup(close)
        return loop,stt,frames,entered,release

    def test_production_pipeline_partial_then_manual_final(self):
        loop,stt,frames,entered,release=self.streaming_loop()
        loop.mic_start(); sleep(.08)
        for _ in range(12): frames.put(bytes([1,0])*480)
        sleep(.35)
        self.assertGreater(stt.transcribe.call_count,0)
        stt.transcribe_final.assert_not_called()
        self.assertTrue(loop.commands.empty())
        loop.mic_commit()
        self.assertTrue(entered.wait(2)); release.set()
        text,token=loop.commands.get(timeout=2)
        self.assertEqual(text,'открой Discord')
        self.assertTrue(token.voice_evidence.committed)

    def test_production_pipeline_cancel_during_final_decode(self):
        loop,stt,frames,entered,release=self.streaming_loop()
        loop.mic_start(); sleep(.08)
        for _ in range(12): frames.put(bytes([1,0])*480)
        sleep(.1); loop.mic_commit()
        self.assertTrue(entered.wait(2))
        loop.mic_cancel(); release.set(); sleep(.2)
        self.assertTrue(loop.commands.empty())
        self.assertIsNone(loop.last_transcription)

    def test_production_wake_pause_continues_same_utterance(self):
        loop,stt,frames,entered,release=self.streaming_loop()
        stt.transcribe.return_value=heard('Кей Кей, открой')
        for _ in range(12): frames.put(bytes([1,0])*480)
        sleep(.4)
        self.assertTrue(loop.commands.empty())
        self.assertFalse(entered.is_set())
        stt.transcribe.return_value=heard('Кей Кей, открой Discord')
        for _ in range(12): frames.put(bytes([1,0])*480)
        self.assertTrue(entered.wait(2)); release.set()
        self.assertEqual(loop.commands.get(timeout=2)[0],'открой Discord')

    def test_obsolete_final_cannot_race_with_cancel(self):
        loop=self.loop(); loop.decoder.epoch=2
        evidence=heard('открой Discord',True); evidence.capture_epoch=1
        loop.dispatch(evidence.text,evidence)
        self.assertTrue(loop.commands.empty())


class PersonalTests(TestCase):
    def test_initial_aliases_gpt_not_codex(self):
        for phrase in ['DS','дс','дис']:
            self.assertEqual(parse_plan(phrase)[0]['app'],'Discord')
        for phrase in ['TG','тг','телега']:
            self.assertEqual(parse_plan(phrase)[0]['app'],'telegram')
        for phrase in ['GPT','чат GPT','ChatGPT']:
            self.assertEqual(parse_plan(phrase)[0]['url'],'https://chatgpt.com')
        self.assertEqual(parse_plan('Codex')[0]['app'],'Codex')

    def test_learn_and_correct_persist(self):
        with TemporaryDirectory() as folder:
            intents=PersonalIntentRegistry(Path(folder)/'intents.json')
            for phrase in ['Когда я говорю "ДС", открывай Discord.', 'TG — это AyuGram.', 'Нет, под GPT я имею в виду сайт ChatGPT.']:
                plan=parse_plan(phrase,intents=intents)[0]
                intents.remember(plan['phrase'],**plan['entry'])
            intents=PersonalIntentRegistry(intents.path)
            self.assertEqual(parse_plan('Теперь TG',intents=intents)[0]['app'],'AyuGram')
            self.assertEqual(parse_plan('А GPT в браузере',intents=intents)[0]['url'],'https://chatgpt.com')
            intents.remember('рабочий чат','app','Telegram Desktop')
            self.assertEqual(parse_plan('открой рабочий чат',intents=intents)[0]['app'],'Telegram Desktop')

    def test_learn_task_alias_without_source_edit(self):
        intents=PersonalIntentRegistry()
        step=parse_plan('Когда я говорю работа, открывай браузер и DS',intents=intents)[0]
        intents.remember(step['phrase'],**step['entry'])
        self.assertEqual([s['app'] for s in parse_plan('работа',intents=intents)],['браузер','Discord'])

    def test_gpt_uses_configured_browser(self):
        from kk.tools.windows.browser import open_url
        resolver=Mock(); resolver.resolve.return_value=[SimpleNamespace(executable='custom-browser.exe',kind='executable')]
        with patch('subprocess.Popen') as launch:
            self.assertTrue(open_url('https://chatgpt.com',resolver)['opened'])
            self.assertEqual(launch.call_args.args[0],['custom-browser.exe','https://chatgpt.com'])
            with self.assertRaises(ValueError): open_url('file:///private',resolver)

    def test_self_corrections(self):
        self.assertEqual(parse_plan('Открой Chrome... нет, Firefox.')[0]['app'],'Firefox')
        self.assertEqual(parse_plan('Поставь громкость 100... нет, 50 процентов.')[0]['level'],50)
        self.assertEqual(parse_plan('PowerShell на первый... нет, на второй экран.')[0]['monitor'],2)
        plan=parse_plan('Открой PowerShell... потом браузер... хотя сначала браузер, потом PowerShell.')
        self.assertEqual([s['app'] for s in plan],['браузер','PowerShell'])

    def test_batch_dependencies(self):
        text='Открой PowerShell на втором, браузер на первом, GPT в браузере и Discord.'
        batch=TaskBatch.from_plan(text,parse_plan(text))
        self.assertEqual([s.spec['kind'] for s in batch.tasks],['open','place','open','place','url','open'])
        self.assertEqual(batch.tasks[1].dependencies,[batch.tasks[0].id])
        self.assertEqual(batch.tasks[4].dependencies,[batch.tasks[2].id])
        self.assertEqual(batch.original_transcript,text)

    def test_failed_dependency_skips_place_but_runs_independent(self):
        from kk.tools.registry import ToolResult
        tools=SimpleNamespace(execute=Mock(side_effect=[ToolResult(False,error='missing'),ToolResult(True,{'hwnd':9,'verified':True})]))
        task=AgentTask('test')
        run_desktop('открой Missing на втором и Discord',task,tools,StateMachine(),DesktopContext())
        self.assertEqual([s.status for s in task.batch.tasks],['failed','skipped','completed'])
        self.assertEqual(tools.execute.call_count,2)

    def test_context_followup(self):
        context=DesktopContext('Discord',123,monotonic())
        self.assertEqual(parse_plan('перенеси его на второй',context)[0]['app'],'Discord')

    def test_hearthstone_and_general_game_correction(self):
        from kk.skills.games import GameRegistry
        with TemporaryDirectory() as folder:
            registry=GameRegistry(Path(folder)/'games.json')
            self.assertEqual(registry.resolve('Hearthstone')['launcher'],'Battle.net')
            self.assertEqual(parse_plan('Хочу поиграть в Hearthstone')[0]['kind'],'game')
            registry.remember('Another Game','Steam')
            self.assertEqual(GameRegistry(registry.path).resolve('Another Game')['launcher'],'Steam')
            self.assertEqual(parse_plan('Нет, Hearthstone запускай через Battle.net')[0]['launcher'],'Battle.net')

    def test_codex_open_is_not_job_permission(self):
        for text in ['открой Codex','запусти Codex','перенеси Codex','хочу GPT','не отправь задачу в Codex']:
            self.assertFalse(explicit_task_request(text),text)
        for text in ['отправь это задание в Codex','запусти задачу в Codex']:
            self.assertTrue(explicit_task_request(text))

    def test_game_selector_excludes_shop_text_and_other_games(self):
        from kk.skills.games import GameLauncher
        self.assertTrue(GameLauncher.matches_control('Hearthstone','TabItemControl',['Hearthstone']))
        self.assertFalse(GameLauncher.matches_control('Hearthstone','TextControl',['Hearthstone']))
        self.assertTrue(GameLauncher.matches_control('Играть: Hearthstone, Регион: Европа, Версия: 36','ButtonControl',['Играть'],'Hearthstone'))
        self.assertFalse(GameLauncher.matches_control('Играть: Another Game, Регион: Европа','ButtonControl',['Играть'],'Hearthstone'))

    def test_monitor_correction_preserves_open_target(self):
        step=parse_plan('Открой PowerShell на первом... нет, на втором экране')[0]
        self.assertEqual((step['app'],step['monitor']),('PowerShell',2))

    def test_explicit_telegram_desktop_does_not_fall_back_to_ayugram(self):
        from kk.tools.windows.app_resolver import ApplicationResolver, AppEntry
        r=ApplicationResolver(); r.updated=monotonic()
        r.entries=[AppEntry('AyuGram','executable','AyuGram.exe','AyuGram.exe')]
        self.assertFalse(r.inspect('Telegram Desktop')['found'])


class MicUITests(TestCase):
    def pointer(self):
        self.events=[]
        return MicPointer(*[lambda value=name:self.events.append(value) for name in ['toggle','start','commit','cancel']])

    def test_click_and_hold(self):
        p=self.pointer(); p.press((10,10),0); p.release((10,10))
        self.assertEqual(self.events,['toggle'])
        p.press((10,10),1); p.tick(1.4); p.release((10,10))
        self.assertEqual(self.events,['toggle','start','commit'])

    def test_drag_never_toggles_mic(self):
        p=self.pointer(); p.press((10,10),0); p.move((20,10)); p.tick(1); p.release((20,10))
        self.assertEqual(self.events,[])

    def test_movement_during_hold_cancels(self):
        p=self.pointer(); p.press((10,10),0); p.tick(.4); p.move((30,10)); p.release((30,10))
        self.assertEqual(self.events,['start','cancel'])

    def test_recording_visual_is_distinct(self):
        from kk.ui.orb_renderer import OrbRenderer
        idle=np.asarray(OrbRenderer(100).render(AgentState.IDLE))
        recording=np.asarray(OrbRenderer(100).render(AgentState.RECORDING))
        self.assertFalse(np.array_equal(idle,recording))
