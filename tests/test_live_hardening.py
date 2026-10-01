from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Event, RLock
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
import json


class LiveHardeningTests(TestCase):
    def test_real_sample_is_ram_only_until_explicit_save(self):
        from kk.voice.diagnostics import LastVoiceSample
        from kk.voice.stt_faster_whisper import Transcription
        with TemporaryDirectory() as folder:
            store = LastVoiceSample(Path(folder)/'voice_diagnostics')
            result = Transcription('Открой TG','ru',1,.3,.1,
                raw_transcript='Открой ТГ',confidence=.72)
            result.passes = [{'raw_transcript':'Открой ТГ'}]
            store.remember(bytes(960), result, 'open_telegram')
            self.assertEqual(list(Path(folder).rglob('*')), [])
            saved = store.save()
            self.assertTrue(saved.exists())
            self.assertTrue(saved.with_suffix('.wav').exists())
            meta = json.loads(saved.read_text(encoding='utf-8'))
            self.assertEqual(meta['raw_transcript'],'Открой ТГ')
            self.assertEqual(meta['final_transcript'],'Открой TG')
            self.assertEqual(meta['intent'],'open_telegram')
            self.assertEqual(meta['confidence'],.72)
            self.assertIsNone(store.save())

    def test_expired_sample_is_never_written(self):
        from kk.voice.diagnostics import LastVoiceSample
        from time import sleep
        with TemporaryDirectory() as folder:
            store=LastVoiceSample(Path(folder)/'diagnostics',ttl_seconds=.01)
            store.remember(bytes(960),SimpleNamespace(text='x',raw_transcript='x',confidence=.5,passes=[]))
            sleep(.04)
            self.assertIsNone(store.sample)
            self.assertIsNone(store.save())
            self.assertFalse(store.folder.exists())

    def test_barge_in_cancels_generation_and_both_speech_queues(self):
        from kk.core.task import CancellationToken
        from kk.voice.speech_queue import SpeechQueue
        from kk.voice.streaming import BargeInController
        speech=SpeechQueue.__new__(SpeechQueue)
        speech.lock=RLock(); speech.token=CancellationToken()
        speech.text_queue=Queue(); speech.audio_queue=Queue(); speech.done=Event()
        speech.playing=Event(); speech.playing.set(); speech.reference=b'a'
        speech.text_queue.put('old'); speech.audio_queue.put('old')
        with patch('kk.voice.speech_queue.sd.stop'):
            barge = BargeInController(speech.cancel)
            barge.speech_started()
            self.assertFalse(speech.token.event.is_set())
            barge.confirm('Подожди, другой вопрос', final=True)
        self.assertTrue(speech.token.event.is_set())
        self.assertTrue(speech.text_queue.empty())
        self.assertTrue(speech.audio_queue.empty())
        self.assertFalse(speech.playing.is_set())

    def test_latest_music_cannot_complete_from_clicks_alone(self):
        from kk.core.completion import review
        task=SimpleNamespace(user_request='Включи последний добавленный трек',actions=[
            {'tool':'click_ui_element','arguments':{'name':'Последний трек'},
             'result':{'ok':True,'data':{'clicked':True}}}],observations=[])
        provider=Mock()
        complete,reason=review(provider,'x',task,'Включила.')
        self.assertFalse(complete)
        self.assertIn('Не подтверждены',reason)
        provider.assert_not_called()
        task.actions.append({'tool':'get_current_media','arguments':{},
            'result':{'ok':True,'data':{'playing':True,'title':'Другой трек'}}})
        task.observations=[{'data':{'ui_elements':[{'name':'По дате добавления — сначала новые'}]}}]
        complete,_=review(provider,'x',task,'Включила.')
        self.assertFalse(complete)
        provider.assert_not_called()

    def test_first_chat_uses_observed_row_and_checks_header(self):
        from kk.tools.windows.telegram import TelegramActions
        rect=lambda top:dict(left=0,top=top,right=200,bottom=top+30,center_x=100,center_y=top+15)
        container={'class_name':'class Dialogs::InnerWidget','rect':rect(0)}
        container['rect']['bottom']=200
        topbar={'class_name':'class HistoryView::TopBarWidget','rect':dict(left=200,top=0,right=600,bottom=40)}
        row={'name':'Алиса','control_type':'ListItemControl','rect':rect(50),
             'automation_id':'chat_1','offscreen':False}
        folder={'name':'Все чаты','control_type':'ListItemControl','rect':rect(0),
                'automation_id':'folder_all','offscreen':False}
        header={'name':'Алиса','rect':dict(center_x=300,center_y=20),'class_name':'Text'}
        uia=SimpleNamespace(click_observed=Mock())
        action=TelegramActions(SimpleNamespace(check_cancel=Mock()),uia)
        action.focus=Mock(return_value={'hwnd':42,'executable':'AyuGram.exe'})
        action.observe=Mock(side_effect=[[container,folder,row,topbar],[container,folder,row,topbar,header]])
        with (patch('kk.tools.windows.native.foreground_window',return_value={'hwnd':42}),
              patch('kk.tools.windows.telegram.sleep')):
            result=action.first_chat()
        self.assertTrue(result['verified'])
        uia.click_observed.assert_called_once_with(row)

    def test_voice_record_cancel_is_separate_from_explicit_send(self):
        from kk.tools.windows.telegram import TelegramActions
        for send in (False,True):
            request='Отправь' if send else 'Отмени'
            registry=SimpleNamespace(check_cancel=Mock(),
                cancellation=SimpleNamespace(get=lambda:SimpleNamespace(user_request=request)))
            action=TelegramActions(registry,SimpleNamespace(click=Mock()))
            action.recording_hwnd=42
            before=[{'control_type':'ButtonControl','name':'Отправить' if send else 'Отменить',
                     'automation_id':'VoiceRecordBar.Send' if send else 'VoiceRecordBar.Cancel'},
                    {'automation_id':'VoiceRecordBar.RecordLock'}]
            after=[{'automation_id':'History.VoiceMessage','name':'Голосовое сообщение'}] if send else []
            action.observe=Mock(side_effect=[before,after])
            with (patch('kk.tools.windows.windows.focus_window_handle'),
                  patch('kk.tools.windows.native.foreground_window',return_value={'hwnd':42})):
                result=action.finish(send=send)
            self.assertTrue(result['verified'])
            self.assertEqual(result['sent'],send)

    def test_yandex_requires_matching_playing_media_session(self):
        from kk.tools.windows.yandex_music import YandexMusicActions
        rect=lambda top:dict(left=300,top=top,right=800,bottom=top+20)
        library={'name':'Коллекция','control_type':'ButtonControl','rect':rect(0)}
        tracks={'name':'Треки','control_type':'TabItemControl','rect':rect(0)}
        sort={'name':'По дате добавления — сначала новые','control_type':'ButtonControl','rect':rect(20)}
        song={'name':'Мой трек — Артист','control_type':'ListItemControl','rect':rect(60)}
        uia=SimpleNamespace(tree=Mock(side_effect=[[library],[tracks],[sort],[sort,song]]),click_observed=Mock())
        registry=SimpleNamespace(execute=Mock(return_value=SimpleNamespace(ok=True,data={'verified':True})),check_cancel=Mock())
        media=SimpleNamespace(call=Mock(return_value=[{'session_id':'YandexMusic','title':'Мой трек','playing':True}]))
        action=YandexMusicActions(registry,uia,media)
        with patch('kk.tools.windows.yandex_music.sleep'):
            result=action.play_latest_added()
        self.assertTrue(result['verified'])
        self.assertEqual(result['ordering'],'newest_first')
        self.assertEqual(uia.click_observed.call_args_list[-1].args[0],song)
        uia.tree.side_effect=[[library],[tracks],[sort],[sort,song]]
        media.call.return_value=[{'session_id':'YandexMusic','title':'Другой трек','playing':True}]
        with (patch('kk.tools.windows.yandex_music.sleep'),
              patch('kk.tools.windows.yandex_music.monotonic',side_effect=[0,1,6])):
            result=action.play_latest_added()
        self.assertFalse(result['verified'])

    def test_yandex_ocr_fallback_uses_observed_text_and_playback(self):
        from kk.tools.windows.yandex_music import YandexMusicActions
        def line(text,x,y):
            return {'text':text,'words':[{'text':text,'rect':[x,y,100,15]}]}
        frames=[
            [line('Коллекция',50,50)],
            [line('Коллекция',50,50),line('Коллекция',250,50),line('Мне нравится',300,90)],
            [line('По дате добавления — сначала новые',300,100),line('Мой трек',300,150)],
        ]
        ocr=SimpleNamespace(read=Mock(side_effect=[{'results':[{'language':'ru','lines':f}]} for f in frames]))
        image=SimpleNamespace(close=Mock())
        capture=SimpleNamespace(capture_image=Mock(return_value=(image,{'bounds':(100,200,1100,900),'scale':(1,1)})))
        screen=SimpleNamespace(capture=capture,ocr=ocr)
        registry=SimpleNamespace(execute=Mock(return_value=SimpleNamespace(ok=True,data={'verified':True})),check_cancel=Mock())
        media=SimpleNamespace(call=Mock(return_value=[{'session_id':'YandexMusic','title':'Мой трек','playing':True}]))
        action=YandexMusicActions(registry,SimpleNamespace(tree=Mock(return_value=[])),media,screen)
        with (patch('kk.tools.windows.yandex_music.sleep'),
              patch('kk.tools.windows.mouse.click') as click,
              patch('kk.tools.windows.mouse.double_click') as double_click):
            result=action.play_latest_added()
        self.assertTrue(result['verified'])
        self.assertEqual(click.call_count,2)
        double_click.assert_called_once()

    def test_look_before_ask_reads_context_without_actions(self):
        from kk.core.orchestrator import _look_before_ask
        from kk.tools.registry import ToolResult
        tools=SimpleNamespace(decision_policy=SimpleNamespace(preferences={'browser':'Firefox'}),
            execute=Mock(return_value=ToolResult(True,{})))
        context=_look_before_ask(tools,'Открой браузер')
        self.assertEqual(context['preferences']['browser'],'Firefox')
        self.assertEqual([call.args[0] for call in tools.execute.call_args_list],
            ['get_desktop_context','list_windows','get_monitors','get_ui_tree','see_screen','recall_memories'])
