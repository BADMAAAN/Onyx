"""Semantic latest-added playback; a click alone is never completion evidence."""
import re
from time import monotonic, sleep


def _fold(text):
    return ' '.join(re.findall(r'\w+', str(text).casefold()))


class YandexMusicActions:
    def __init__(self, registry, uia, media, screen=None):
        self.registry, self.uia, self.media, self.screen = registry, uia, media, screen
        self.hwnd = None

    def _check_focus(self):
        if self.hwnd:
            from .native import foreground_window
            if foreground_window().get('hwnd') != self.hwnd:
                raise InterruptedError('Яндекс Музыка потеряла фокус')

    def _visible(self):
        self._check_focus()
        return [e for e in self.uia.tree(False) if not e.get('offscreen') and e.get('rect')]

    def _ocr(self):
        if not self.screen: raise LookupError('OCR недоступен')
        self._check_focus()
        image,meta=self.screen.capture.capture_image('active_window',max_dimension=1600)
        try: result=self.screen.ocr.read(image)
        finally: image.close()
        source=next((r for r in result['results'] if r.get('language')=='ru'),result['results'][0])
        rows=[]
        for line in source.get('lines',[]):
            words=line.get('words') or []
            if not words or not line.get('text','').strip(): continue
            bounds=[w['rect'] for w in words]
            left=min(r[0] for r in bounds); top=min(r[1] for r in bounds)
            right=max(r[0]+r[2] for r in bounds); bottom=max(r[1]+r[3] for r in bounds)
            rows.append(dict(text=line['text'],x=meta['bounds'][0]+(left+right)/2/meta['scale'][0],
                y=meta['bounds'][1]+(top+bottom)/2/meta['scale'][1],
                left=meta['bounds'][0]+left/meta['scale'][0],
                top=meta['bounds'][1]+top/meta['scale'][1]))
        return rows

    def _ocr_click(self, rows, phrases, leftmost=False):
        from .mouse import click
        matches=[r for r in rows if any(_fold(p) in _fold(r['text']) for p in phrases)]
        if not matches: raise LookupError('OCR не нашёл нужный раздел')
        if leftmost: matches.sort(key=lambda r:r['left'])
        elif len(matches)!=1: raise LookupError('OCR нашёл неоднозначный раздел')
        target=matches[0]
        self._check_focus(); self.registry.check_cancel()
        click(round(target['x']),round(target['y']))
        sleep(.35)
        return target

    def _wait_ocr(self, predicate, message):
        deadline=monotonic()+3
        while monotonic()<deadline:
            self.registry.check_cancel()
            rows=self._ocr()
            if predicate(rows): return rows
            sleep(.2)
        raise LookupError(message)

    def _verify_playback(self, title):
        deadline=monotonic()+5
        while monotonic()<deadline:
            self.registry.check_cancel()
            sessions=self.media.call('list')
            matches=[s for s in sessions if 'yandex' in s.get('session_id','').casefold()
                     or 'яндекс' in s.get('session_id','').casefold()]
            if _fold(title) and any(s.get('playing') and _fold(title) == _fold(s.get('title')) for s in matches):
                return {'verified':True,'title':title,'ordering':'newest_first',
                        'media_session':next(s for s in matches if s.get('playing') and _fold(title) == _fold(s.get('title')))}
            sleep(.2)
        return {'verified':False,'title':title,'reason':'Не подтверждено воспроизведение выбранного трека'}

    def _play_ocr(self):
        from .mouse import double_click
        rows=self._ocr()
        self._ocr_click(rows,('Коллекция','Collection'),leftmost=True)
        rows=self._wait_ocr(lambda rows: bool(rows) and
            any(_fold(r['text']) in {'коллекция','collection'} and
                r['left']>min(x['left'] for x in rows) for r in rows) and
            any('мне нравится' in _fold(r['text']) or 'liked tracks' in _fold(r['text']) for r in rows),
            'Переход в коллекцию не подтвердился')
        section=self._ocr_click(rows,('Мне нравится','Треки','Liked tracks','Tracks'))
        rows=self._wait_ocr(lambda rows:any('дате добавления' in _fold(r['text']) or
            'date added' in _fold(r['text']) for r in rows),
            'Сортировка по дате добавления не появилась')
        sort=[r for r in rows if any(p in _fold(r['text']) for p in ('дате добавления','date added','недавно добавлен'))]
        if len(sort)!=1: raise LookupError('OCR не определил сортировку по дате добавления')
        newest=('сначала новые','новые сначала','newest first')
        if not any(p in _fold(sort[0]['text']) for p in newest):
            self._ocr_click(rows,('По дате добавления','Date added'))
            rows=self._wait_ocr(lambda rows:any(any(p in _fold(r['text']) for p in newest)
                for r in rows),'Вариант «сначала новые» не появился')
            self._ocr_click(rows,('Сначала новые','Новые сначала','Newest first'))
            rows=self._wait_ocr(lambda rows:any(any(p in _fold(r['text']) for p in newest)
                and any(d in _fold(r['text']) for d in ('дате добавления','date added')) for r in rows),
                'Порядок «сначала новые» не подтвердился через OCR')
        if not any(any(p in _fold(r['text']) for p in newest) and
                   any(d in _fold(r['text']) for d in ('дате добавления','date added')) for r in rows):
            raise LookupError('Порядок «сначала новые» не подтвердился через OCR')
        # Coordinates from the menu-opening frame are stale after selection.
        sort=[r for r in rows if any(p in _fold(r['text']) for p in ('дате добавления','date added'))]
        if len(sort) != 1: raise LookupError('Текущая строка сортировки неоднозначна')
        candidates=[r for r in rows if r['left']>=section['left']-30 and r['top']>sort[0]['top']+20
                    and len(_fold(r['text']))>=3 and not re.search(r'(?i)сортир|добавлен|воспроизв|play|shuffle',r['text'])]
        if not candidates: raise LookupError('Первый трек не определяется через OCR')
        first=min(candidates,key=lambda r:(r['top'],r['left']))
        title=first['text'].split(' — ')[0].strip()
        self._check_focus(); self.registry.check_cancel()
        double_click(round(first['x']),round(first['y']))
        return self._verify_playback(title)

    def _click_named(self, elements, names):
        candidates = [e for e in elements if e.get('control_type') in
            {'ButtonControl','TabItemControl','ListItemControl','MenuItemControl','HyperlinkControl'}
            and any(_fold(name) in _fold(e.get('name')) for name in names)]
        if len(candidates) != 1:
            raise LookupError('Раздел или сортировка не определяются однозначно')
        self.registry.check_cancel()
        self.uia.click_observed(candidates[0])
        sleep(.25)
        return candidates[0]

    def play_latest_added(self):
        opened = self.registry.execute('open_application', {'name':'Яндекс Музыка'})
        if not opened.ok or not isinstance(opened.data, dict) or not opened.data.get('verified'):
            raise RuntimeError('Яндекс Музыка не открылась')
        self.hwnd=opened.data.get('hwnd')
        elements = self._visible()
        if not any(_fold(e.get('name')) in {'коллекция','моя музыка','библиотека','collection','library'} for e in elements):
            return self._play_ocr()
        self._click_named(elements, ('Коллекция','Моя музыка','Библиотека','Collection','Library'))
        elements = self._visible()
        # Prefer the track list over albums and playlists.
        self._click_named(elements, ('Треки','Tracks'))
        elements = self._visible()
        sort = [e for e in elements if re.search(r'(?i)дате добавления|date added|недавно добавлен|recently added', e.get('name',''))]
        if len(sort) != 1:
            raise LookupError('Сортировка по дате добавления не определяется')
        label = _fold(sort[0].get('name'))
        newest=('сначала новые','новые сначала','descending','newest first')
        ordering_verified=any(word in label for word in newest)
        if not ordering_verified:
            self.uia.click_observed(sort[0])
            elements = self._visible()
            self._click_named(elements, ('Сначала новые','Новые сначала','Newest first'))
        elements = self._visible()
        ordering_verified=any(any(word in _fold(e.get('name')) for word in newest)
            and re.search(r'(?i)дате добавления|date added|недавно добавлен|recently added',e.get('name',''))
            for e in elements)
        if not ordering_verified:
            raise LookupError('Порядок «сначала новые» не подтвердился в интерфейсе')
        sort = [e for e in elements if re.search(r'(?i)дате добавления|date added|недавно добавлен|recently added', e.get('name',''))]
        if len(sort) != 1: raise LookupError('Текущая строка сортировки неоднозначна')
        rows = [e for e in elements if e.get('control_type') in {'ListItemControl','DataItemControl'}
                and e['rect']['top'] >= sort[0]['rect']['bottom']
                and e.get('name') and not re.search(r'(?i)сортиров|sort|плейлист|playlist|альбом|album',e['name'])]
        if not rows:
            raise LookupError('Список добавленных треков не определяется')
        first = min(rows, key=lambda e: (e['rect']['top'],e['rect']['left']))
        title = first['name'].split(' — ')[0].split(' - ')[0].strip()
        if len(_fold(title)) < 2:
            raise LookupError('Название первого трека не определяется')
        self.registry.check_cancel()
        self.uia.click_observed(first)
        return self._verify_playback(title)
