"""Strict RU/EN cardinal numbers. Unknown words are never repaired."""
import re

RU = dict(zip('ноль один два три четыре пять шесть семь восемь девять десять одиннадцать двенадцать тринадцать четырнадцать пятнадцать шестнадцать семнадцать восемнадцать девятнадцать'.split(), range(20)))
EN = dict(zip('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(), range(20)))
RU.update(одна=1, одно=1, две=2)
TENS = dict(zip('двадцать тридцать сорок пятьдесят шестьдесят семьдесят восемьдесят девяносто'.split(), range(20, 100, 10)))
TENS.update(zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(), range(20, 100, 10)))
HUNDREDS = dict(zip('сто двести триста четыреста пятьсот шестьсот семьсот восемьсот девятьсот'.split(), range(100, 1000, 100)))


def spoken_number(text):
    text = re.sub(r'\s*(?:процент(?:ов|а)?|percent|%)\s*$', '', text.casefold().strip()).strip()
    if re.fullmatch(r'\d{1,6}', text):
        return int(text)
    words = text.replace('-', ' ').split()
    if not words:
        return None
    total = 0
    if len(words) >= 2 and words[0] in EN and 1 <= EN[words[0]] <= 9 and words[1] == 'hundred':
        total = EN[words.pop(0)] * 100
        words.pop(0)
        if words and words[0] == 'and':
            words.pop(0)
            if not words:
                return None
    elif words[0] in HUNDREDS:
        total = HUNDREDS[words.pop(0)]
    if not words:
        return total
    units = {**RU, **EN}
    if len(words) == 1 and words[0] in units:
        return total + units[words[0]]
    if words[0] in TENS and (len(words) == 1 or (len(words) == 2 and 1 <= units.get(words[1], -1) <= 9)):
        return total + TENS[words[0]] + (units[words[1]] if len(words) == 2 else 0)
    return None
