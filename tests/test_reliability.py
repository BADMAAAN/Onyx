from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
from time import monotonic
import numpy as np

from kk.core.numbers import spoken_number
from kk.core.desktop_commands import parse_plan, run_desktop, DesktopContext
from kk.core.task import AgentTask, TaskStatus
from kk.core.state import StateMachine
from kk.tools.registry import ToolResult
from kk.tools.windows.app_resolver import ApplicationResolver, AppEntry, canonical
from kk.tools.windows.apps import ApplicationLauncher
from kk.voice.stt_faster_whisper import Transcription
from kk.voice.recognition import recognize_final, suspicion, fragment_question, Clarification


def transcript(text, accepted=True, confidence=.95):
    return Transcription(text, 'ru', 1, 1, .1, text, text, accepted, '', [], [], confidence)


class NumberTests(TestCase):
    def test_spoken_ru_en(self):
        for source, expected in [('сто процентов',100),('пятьдесят процентов',50),('тридцать пять',35),
            ('one hundred percent',100),('ninety-nine',99),('two hundred and five',205),('ноль',0),('100%',100)]:
            with self.subTest(source=source): self.assertEqual(spoken_number(source), expected)

    def test_no_guess_or_clamp(self):
        for text in ['стол процентов','один два','сто сто','twenty ten','-1','one hundred and','35 мусора']:
            self.assertIsNone(spoken_number(text), text)
        self.assertFalse(parse_plan('громкость 101')[0]['valid'])
        self.assertEqual(parse_plan('сделай громкость на сто процентов')[0]['level'],100)


class PlanTests(TestCase):
    def test_volume_runs_registry_without_llm(self):
        from kk.core.fast_commands import run_fast
        tools=SimpleNamespace(execute=Mock(side_effect=[ToolResult(True,{'percent':100}),ToolResult(True,{'percent':100})]))
        task=AgentTask('test')
        self.assertEqual(run_fast('сделай громкость на сто процентов',task,tools,StateMachine()),'Громкость 100 процентов.')
        self.assertEqual(tools.execute.call_args_list[0].args,('set_volume',{'level':100}))
        self.assertEqual(task.metrics['brain_route'],'deterministic_no_llm')

    def test_compound_two_monitors(self):
        plan = parse_plan('Открой PowerShell на втором экране, браузер на первом и Codex на втором.')
        self.assertEqual([(s['app'],s['monitor']) for s in plan], [('PowerShell',2),('браузер',1),('Codex',2)])

    def test_mixed_compound_and_literal_search_tail(self):
        plan=parse_plan('громкость сто и открой PowerShell на втором экране')
        self.assertEqual([s['kind'] for s in plan],['volume','open'])
        plan=parse_plan('Открой Яндекс Музыку и открой поиск и напиши Ojobudo, A и B')
        self.assertEqual([s['kind'] for s in plan],['open','search'])
        self.assertEqual(plan[-1]['text'],'Ojobudo, A и B')

    def test_context_pronoun_and_search(self):
        context = DesktopContext('PowerShell',123,monotonic())
        self.assertEqual(parse_plan('Перенеси его на второй экран.',context)[0]['app'],'PowerShell')
        context.app = 'Яндекс Музыка'
        self.assertEqual(parse_plan('Открой поиск и напиши Ojobudo',context)[0],
            {'kind':'search','app':'Яндекс Музыка','text':'Ojobudo','submit':False})

    def test_literal_payload_and_submit(self):
        plan = parse_plan('В Яндекс Музыке открой поиск и напиши Ojobudo, A и B')
        self.assertEqual(plan[0]['text'], 'Ojobudo, A и B')
        self.assertFalse(plan[0]['submit'])
        self.assertTrue(parse_plan('В Яндекс Музыке найди Ojobudo')[0]['submit'])
        self.assertEqual(parse_plan('Кей Кей, в Яндекс Музыке открой поиск и напиши Ojobudo')[0]['text'],'Ojobudo')

    def test_explicit_app_correction_route(self):
        self.assertEqual(parse_plan('Запомни: моя файловка это Explorer')[0],
            {'kind':'alias','spoken':'моя файловка','application':'Explorer'})

    def test_stale_context(self):
        plan = parse_plan('Перенеси его на второй экран',DesktopContext('PowerShell',123,0))
        self.assertEqual(plan[0]['app'],'')

    def test_independent_steps_continue_after_failure(self):
        tools=Mock()
        def execute(name,args):
            if name=='open_application':
                return ToolResult(False,error='not found') if args['name']=='Missing' else ToolResult(True,{'verified':True,'hwnd':101})
            return ToolResult(True,{'after':{'monitor_index':args['monitor_index']}})
        tools.execute.side_effect=execute
        task=AgentTask('test'); context=DesktopContext()
        answer=run_desktop('Открой Missing, PowerShell на втором экране и Codex на первом',task,tools,StateMachine(),context)
        self.assertIn('Приложение не найдено',answer)
        self.assertIn('Codex на экране 1',answer)
        self.assertEqual(task.status,TaskStatus.FAILED)
        self.assertEqual(context.app,'Codex')
        self.assertEqual(tools.execute.call_count,5)

    def test_unverified_launch_never_claims_success(self):
        tools=Mock(); tools.execute.return_value=ToolResult(True,{'process_id':3})
        task=AgentTask('test')
        answer=run_desktop('открой PowerShell',task,tools,StateMachine(),DesktopContext())
        self.assertEqual(task.status,TaskStatus.FAILED)
        self.assertIn('не подтверждено',answer)


class ResolverTests(TestCase):
    def resolver(self):
        r=ApplicationResolver(powershell='powershell'); r.updated=monotonic()
        r.entries=[AppEntry(n,'executable',n+'.exe',n+'.exe') for n in ['PowerShell','Explorer','Codex','AyuGram','Yandex Music','Visual Studio Code']]
        return r

    def test_aliases_fuzzy_transliteration(self):
        r=self.resolver()
        for name, expected in [('пауэршелл','PowerShell'),('повер шел','PowerShell'),('файловка','Explorer'),
            ('кодекс','Codex'),('телега','AyuGram'),('яндекс музыка','Yandex Music'),('VS Code','Visual Studio Code'),
            ('Powershel','PowerShell'),('експлорер','Explorer')]:
            self.assertEqual(r.resolve(name)[0].name,expected,name)

    def test_unknown_and_ambiguous(self):
        r=self.resolver()
        with self.assertRaises(LookupError): r.resolve('Completely unknown application 971')
        r.entries += [AppEntry('TestApp1','executable','1'),AppEntry('TestApp2','executable','2')]
        with self.assertRaises(LookupError): r.resolve('TestApp')

    def test_remembered_correction(self):
        with TemporaryDirectory() as d:
            r=self.resolver(); r.corrections_path=Path(d)/'aliases.json'
            r.remember('моя файловка','Explorer')
            other=ApplicationResolver(corrections_path=r.corrections_path)
            other.entries=r.entries; other.updated=monotonic()
            self.assertEqual(other.resolve('моя файловка')[0].name,'Explorer')

    def test_failed_launch_fallback_and_refresh(self):
        resolver=Mock()
        resolver.resolve.return_value=[AppEntry('a','aumid','id'),AppEntry('a','executable','a.exe')]
        launcher=ApplicationLauncher(resolver=resolver)
        launcher._launch=Mock(side_effect=[OSError('bad aumid'),42])
        launcher._verify=Mock(return_value={'hwnd':7,'process_id':42,'executable':'a.exe','monitor_index':2})
        result=launcher.open('a')
        self.assertTrue(result['verified']); self.assertEqual(result['method'],'executable')
        self.assertEqual(len(result['attempts']),1)

    def test_unverified_launch_fails(self):
        r=Mock(); r.resolve.return_value=[AppEntry('a','executable','a.exe')]
        launcher=ApplicationLauncher(resolver=r); launcher._launch=Mock(return_value=3)
        launcher._verify=Mock(side_effect=TimeoutError('no window'))
        with self.assertRaises(OSError): launcher.open('a')
        self.assertEqual(r.resolve.call_args.kwargs,{'refresh':True})


class RecognitionTests(TestCase):
    def stt(self, first, second):
        accurate=SimpleNamespace(transcribe=Mock(return_value=second))
        return SimpleNamespace(transcribe=Mock(return_value=first),second_pass=accurate,resolver=None)

    def test_suspicious_phrase_uses_same_audio(self):
        audio=np.zeros(16000,dtype=np.float32)
        stt=self.stt(transcript('сделай громкость на стол процентов'),transcript('сделай громкость на сто процентов'))
        result=recognize_final(stt,audio)
        self.assertTrue(result.accepted); self.assertEqual(result.chosen_stt_pass,'accurate')
        self.assertIs(stt.second_pass.transcribe.call_args.args[0],audio)
        self.assertIn('стол',result.passes[0]['raw_transcript'])
        self.assertEqual(parse_plan(result.text)[0]['level'],100)

    def test_good_command_keeps_fast_path(self):
        stt=self.stt(transcript('открой PowerShell'),transcript('not used'))
        result=recognize_final(stt,np.zeros(1))
        self.assertEqual(result.chosen_stt_pass,'fast'); stt.second_pass.transcribe.assert_not_called()

    def test_disagreement_asks_specific_number(self):
        stt=self.stt(transcript('громкость пятьдесят',False,.3),transcript('громкость сто'))
        result=recognize_final(stt,np.zeros(1))
        self.assertFalse(result.accepted); self.assertEqual(result.clarification,'100 процентов?')

    def test_unknown_app_retries_before_question(self):
        stt=self.stt(transcript('открой неизвестное'),transcript('открой неизвестное'))
        stt.resolver=SimpleNamespace(inspect=lambda _: {'found':False})
        result=recognize_final(stt,np.zeros(1))
        stt.second_pass.transcribe.assert_called_once()
        self.assertFalse(result.accepted)
        self.assertEqual(result.text,'открой неизвестное')
        self.assertEqual(result.clarification,
            'Не уверена, что расслышала команду целиком. Правильно: «открой неизвестное»?')

    def test_failed_second_never_guesses(self):
        stt=self.stt(transcript('громкость стол'),None)
        stt.second_pass.transcribe.side_effect=RuntimeError('offline')
        result=recognize_final(stt,np.zeros(1))
        self.assertFalse(result.accepted); self.assertIn('стол',result.raw_transcript)

    def test_low_confidence_and_unparsed(self):
        for text in ['открой', 'громкость 900', 'спасибо за просмотр']:
            self.assertTrue(suspicion(transcript(text)))
        self.assertIn('low_confidence',suspicion(transcript('открой PowerShell',True,.2)))
        self.assertIn('invalid_monitor',suspicion(transcript('открой PowerShell на 0 экране')))

    def test_confirmation_and_replacement(self):
        pending=transcript('громкость сто',False); pending.clarification='100 процентов?'
        clarification=Clarification(); clarification.ask(pending)
        answer=clarification.consume(transcript('да'))
        self.assertEqual(answer.text,'громкость сто'); self.assertEqual(answer.raw_transcript,'да')
        clarification.ask(transcript('громкость стол',False))
        self.assertEqual(clarification.consume(transcript('пятьдесят процентов')).text,'громкость 50')

    def test_equivalent_aliases_do_not_create_disagreement(self):
        stt=self.stt(transcript('открой пауэршелл',False,.2),transcript('открой PowerShell'))
        result=recognize_final(stt,np.zeros(1))
        self.assertTrue(result.accepted)

    def test_wake_spelling_confidence_separate_from_entity(self):
        result=transcript('KK, открой PowerShell',True,.5)
        result.intent_confidence=.98
        self.assertEqual(suspicion(result),[])
        result.intent_confidence=.2
        self.assertEqual(suspicion(result),['low_confidence'])


class SearchTests(TestCase):
    def test_fallback_reaches_ocr_before_vision(self):
        from kk.tools.windows.search import AppSearch
        control=Mock(); item={'value':'Ojobudo','control_type':'EditControl','name':'Поиск'}
        launcher=Mock(); launcher.open.return_value={'hwnd':7}
        search=AppSearch(launcher,Mock(),Mock()); search.guard=Mock()
        search.uia_focus=Mock(return_value=None)
        found=[False]
        search.focused_edit=lambda _: (control,item) if found[0] else None
        def pixels(hwnd,vision):
            found[0]=True
            return True
        search.locate_pixels=Mock(side_effect=pixels)
        with patch('kk.tools.windows.search.focus_window_handle'), patch('kk.tools.windows.search.press_hotkey'):
            result=search.run('App','Ojobudo')
        self.assertEqual([a['method'] for a in result['attempts']],['uia','shortcut','keyboard','ocr'])
        search.locate_pixels.assert_called_once_with(7,False)

    def test_placeholder_and_clear_button(self):
        from kk.tools.windows.search import is_search
        self.assertTrue(is_search({'name':'Что вы чувствуете или ищете?'}))
        self.assertFalse(is_search({'name':'Очистить поиск'}))
        self.assertFalse(is_search({'name':'Найти на странице'}))

    def test_no_enter_for_write_exact_case(self):
        from kk.tools.windows.search import AppSearch
        control=Mock(); item={'value':'Ojobudo','control_type':'EditControl','name':'Поиск'}
        launcher=Mock(); launcher.open.return_value={'hwnd':7}
        search=AppSearch(launcher,Mock(),Mock()); search.guard=Mock()
        search.uia_focus=Mock(return_value=(control,item)); search.focused_edit=Mock(return_value=(control,item))
        with patch('kk.tools.windows.search.focus_window_handle'), patch('kk.tools.windows.search.press_hotkey') as hotkey:
            result=search.run('Yandex Music','Ojobudo')
            control.GetValuePattern().SetValue.assert_called_with('Ojobudo')
            self.assertTrue(result['verified']); hotkey.assert_not_called()
            search.run('Yandex Music','Ojobudo',True)
            hotkey.assert_called_once_with(['enter'])

    def test_failed_readback_never_submits(self):
        from kk.tools.windows.search import AppSearch
        control=Mock(); item={'value':'wrong','control_type':'EditControl','name':'Поиск'}
        launcher=Mock(); launcher.open.return_value={'hwnd':7}
        search=AppSearch(launcher,Mock(),Mock()); search.guard=Mock()
        search.uia_focus=Mock(return_value=(control,item)); search.focused_edit=Mock(return_value=(control,item))
        with patch('kk.tools.windows.search.focus_window_handle'), patch('kk.tools.windows.search.press_hotkey') as hotkey:
            with self.assertRaises(RuntimeError): search.run('Yandex Music','Ojobudo',True)
            hotkey.assert_not_called()
