"""Vertical limits of restriction zones from free text and from our compact notation.

Free text as published in the customer's zone file, e.g. «От земли до 500 м (1700 фут) AMSL»,
«От 400 м (1400 фут) AMSL до FL200», «На всех высотах». The data mixes Cyrillic and Latin
look-alike letters («Oт», «фyт»), so matching accepts both.
"""
import re
from dataclasses import dataclass, field

from bas.domain.models import VerticalLimit

INVALID_MARK = re.compile(r'^\s*некорректная\s+геометрия!\s*', re.I)
RANGE = re.compile(r'^[оo]т\s+(?P<lower>.+?)\s+до\s+(?P<upper>.+)$', re.I)
ALL_ALTITUDES = re.compile(r'на\s+всех\s+высотах', re.I)
FLIGHT_LEVEL = re.compile(r'\bfl\s*(\d{1,3})\b', re.I)
METRES = re.compile(r'(\d+(?:[.,]\d+)?)\s*[мm](?![a-zа-я])', re.I)
FEET_ONLY = re.compile(r'(\d+(?:[.,]\d+)?)\s*(?:ф[уy]т|ft)\b', re.I)
COMPACT = re.compile(r'^\s*(?:(GND|UNL)|FL\s*(\d{1,3})|(-?\d+(?:\.\d+)?)\s*(AGL|AMSL))\s*$', re.I)


@dataclass
class ParsedAltitudes:
    lower: VerticalLimit
    upper: VerticalLimit
    notes: str = ''
    parsed: bool = True
    flags: list[str] = field(default_factory=list)


def _limit(part: str, default_reference: str) -> VerticalLimit | None:
    text = part.strip().lower()
    if re.search(r'\bземли\b|\bgnd\b', text):
        return VerticalLimit(reference='GND')
    if re.search(r'\bunl\b', text):
        return VerticalLimit(reference='UNL')
    level = FLIGHT_LEVEL.search(text)
    if level:
        return VerticalLimit(reference='FL', value=int(level.group(1)))
    reference = 'AMSL' if 'amsl' in text else 'AGL' if 'agl' in text else default_reference
    metres = METRES.search(text)
    if metres:
        return VerticalLimit(reference=reference, value=float(metres.group(1).replace(',', '.')))
    feet = FEET_ONLY.search(text)
    if feet:
        return VerticalLimit(reference=reference, value=round(float(feet.group(1).replace(',', '.')) * 0.3048, 1))
    return None


def parse_altitude_text(text: str) -> ParsedAltitudes:
    flags = []
    body = text or ''
    if INVALID_MARK.match(body):
        flags.append('source_marked_invalid_geometry')
        body = INVALID_MARK.sub('', body, count=1)
    lines = [line.strip() for line in body.strip().splitlines() if line.strip()]
    head = lines[0] if lines else ''
    notes = ' '.join(lines[1:])
    if ALL_ALTITUDES.search(head):
        return ParsedAltitudes(VerticalLimit(reference='GND'), VerticalLimit(reference='UNL'), notes, True, flags)
    match = RANGE.match(head)
    if match:
        # Missing references resolve to the safe side: a lower limit taken as AMSL sits lower
        # above high ground, an upper limit taken as AGL reaches higher.
        lower = _limit(match.group('lower'), 'AMSL')
        upper = _limit(match.group('upper'), 'AGL')
        if lower and upper:
            return ParsedAltitudes(lower, upper, notes, True, flags)
    # Unknown wording: the zone applies at every altitude until someone reads it.
    return ParsedAltitudes(VerticalLimit(reference='GND'), VerticalLimit(reference='UNL'),
                           ' '.join(lines) if lines else '', False, flags + ['altitude_text_not_recognised'])


def parse_compact_limit(text: str) -> VerticalLimit | None:
    """Our KML notation: GND, UNL, FL100, "120 AGL", "400 AMSL"."""
    match = COMPACT.match(text or '')
    if not match:
        return None
    word, level, value, reference = match.groups()
    if word:
        return VerticalLimit(reference=word.upper())
    if level:
        return VerticalLimit(reference='FL', value=int(level))
    return VerticalLimit(reference=reference.upper(), value=float(value))


def format_compact_limit(limit: VerticalLimit) -> str:
    if limit.reference in ('GND', 'UNL'):
        return limit.reference
    if limit.reference == 'FL':
        return f'FL{int(limit.value):03d}'
    return f'{limit.value:g} {limit.reference}'
