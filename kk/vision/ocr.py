"""Local Windows OCR; no model downloads, images remain in memory."""
from __future__ import annotations

import asyncio
import io
from time import perf_counter


class WindowsOCR:
    def languages(self):
        import winrt.windows.foundation.collections  # WinRT vector projection
        from winrt.windows.media.ocr import OcrEngine
        return [x.language_tag for x in OcrEngine.available_recognizer_languages]

    async def _read(self, image, languages):
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapDecoder, BitmapPixelFormat, BitmapAlphaMode
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import InMemoryRandomAccessStream, DataWriter
        import winrt.windows.foundation
        import winrt.windows.foundation.collections
        buf = io.BytesIO()
        image.save(buf, format='PNG')
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        try:
            writer.write_bytes(buf.getvalue())
            await writer.store_async()
            stream.seek(0)
            decoder = await BitmapDecoder.create_async(stream)
            bitmap = await decoder.get_software_bitmap_converted_async(BitmapPixelFormat.BGRA8, BitmapAlphaMode.IGNORE)
            try:
                results = []
                for language in languages:
                    engine = OcrEngine.try_create_from_language(Language(language))
                    if engine is None:
                        continue
                    result = await engine.recognize_async(bitmap)
                    results.append({'language': language, 'text': result.text,
                        'lines': [{'text': line.text, 'words': [
                            {'text': w.text, 'rect': [w.bounding_rect.x, w.bounding_rect.y,
                             w.bounding_rect.width, w.bounding_rect.height]} for w in line.words
                        ]} for line in result.lines]})
                return results
            finally:
                bitmap.close()
        finally:
            writer.detach_stream()
            writer.close()
            stream.close()

    def read(self, image, languages=('ru', 'en-US')):
        started = perf_counter()
        results = asyncio.run(self._read(image, languages))
        if not results:
            raise RuntimeError('Windows OCR: requested language packs unavailable')
        return {'source': 'windows_ocr', 'results': results, 'seconds': perf_counter() - started}
