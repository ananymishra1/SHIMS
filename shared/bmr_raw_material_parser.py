"""Parse raw-material and solvent tables from BMR extracted summaries.

Handles numbered lists and pipe-delimited tables found in PDF/docx BMRs.
"""
from __future__ import annotations

import re
from typing import Any


_UNIT_PATTERNS = [
    r'kg\.?',
    r'kgs\.?',
    r'g\.?',
    r'gm\.?',
    r'grams?\.?',
    r'litres?\.?',
    r'liters?\.?',
    r'ltr\.?',
    r'ltrs\.?',
    r'lit\.?',
    r'lt\.?',
    r'l\.?',
    r'ml\.?',
    r'mol\.?',
    r'moles?\.?',
    r'mmol\.?',
]
UNIT_RE = re.compile(r'\b(' + '|'.join(_UNIT_PATTERNS) + r')\b', re.I)
# Stand-alone numeric token (not embedded in chemical formulas such as H2O2 or Na2SO4).
QTY_RE = re.compile(r'(?<![A-Za-z])[\d,]+(?:\.\d+)?(?![A-Za-z])')


def _clean_name(name: str) -> str:
    name = re.sub(r'[\u2010\u2011\u2012\u2013\u2014\u2015]', '-', name)
    name = re.sub(r'\s+', ' ', name)
    name = name.strip(' |:-•◦▪')
    return name


def _is_header(name: str) -> bool:
    low = name.lower()
    headers = {
        'raw material', 'raw materials', 'name of chemicals', 'name of chemical', 's. no.', 's.no', 's/no', 'sr no',
        'uom', 'qty', 'quantity', 'rate', 'amount', 'total', 'output', 'out put', 'description', 'item',
        'packing material', 'packing materials', 'appendix', 'material indent', 'drum', 'bag', 'transport',
        'conversion charge', 'cost per kg', 'test report', 'per kg', 'completion date', 'theoretical yield',
        'actual yield', 'output quantity', 'manufacturing date', 'holding period', '% yield', 'ensure that',
        'checks', 'done by', 'checked by', 'bpcr checked', 'bpcr reviewed', 'decision by', 'batch is',
    }
    for h in headers:
        if h in low:
            return True
    # Reject stage labels used as inputs (Stage-1, Stage-I, etc.)
    if re.match(r'^stage\s*[-–]?\s*[ivx\d]+', low):
        return True
    if len(name) < 3:
        return True
    return False


def _extract_unit_qty(text: str) -> tuple[str | None, float | None]:
    # Find first unit token
    m = UNIT_RE.search(text)
    if not m:
        return None, None
    unit = m.group(1).lower()
    # Normalize
    unit_map = {
        'kgs': 'kg', 'kilograms': 'kg', 'kilogram': 'kg',
        'gms': 'g', 'gm': 'g', 'grams': 'g', 'gram': 'g',
        'litres': 'L', 'liters': 'L', 'litre': 'L', 'liter': 'L',
        'ltrs': 'L', 'ltr': 'L', 'lit': 'L', 'lt': 'L', 'l': 'L',
        'mls': 'mL', 'ml': 'mL',
        'moles': 'mol', 'mole': 'mol', 'mmol': 'mmol',
    }
    unit = unit_map.get(unit, unit)
    # Pick the numeric token nearest to the unit span
    best: tuple[float, re.Match[str]] | None = None
    for qm in QTY_RE.finditer(text):
        dist = min(abs(qm.start() - m.start()), abs(qm.end() - m.end()))
        if best is None or dist < best[0]:
            best = (dist, qm)
    if best is not None:
        qty_str = best[1].group(0).replace(',', '')
        try:
            return unit, float(qty_str)
        except ValueError:
            pass
    return unit, None


def _parse_numbered_line(line: str) -> dict[str, Any] | None:
    # Remove leading bullet/number like "1." or "1)" or "1"
    stripped = re.sub(r'^\s*(?:\d+[\.\)\:\-]?\s+)', '', line)
    if stripped == line:
        return None
    unit, qty = _extract_unit_qty(stripped)
    if not unit:
        return None
    m = UNIT_RE.search(stripped)
    name = _clean_name(stripped[:m.start()] if m else UNIT_RE.sub('', stripped))
    if _is_header(name):
        return None
    return {'name': name, 'unit': unit, 'quantity': qty,
            'unit_type': 'mass' if unit in ('kg','g','mg','mol','mmol') else ('volume' if unit in ('L','mL') else 'mass')}


def _parse_pipe_table_row(line: str) -> dict[str, Any] | None:
    if '|' not in line:
        return None
    cells = [c.strip() for c in line.split('|')]
    # Keep empties so positional layout is preserved, then work with indexed list.
    if len(cells) < 3:
        return None

    for i, cell in enumerate(cells):
        unit, qty = _extract_unit_qty(cell)
        if not unit:
            continue
        # Choose nearest non-empty, non-numeric cell as name
        name: str | None = None
        name_dist = None
        for j, c in enumerate(cells):
            if j == i or not c or QTY_RE.fullmatch(c.replace(',', '')):
                continue
            if UNIT_RE.search(c):
                continue
            dist = abs(j - i)
            if name_dist is None or dist < name_dist:
                name_dist = dist
                name = c
        # Choose nearest numeric cell as quantity (allow same cell if qty already found)
        qty_value = qty
        if qty_value is None:
            qty_dist = None
            for j, c in enumerate(cells):
                if not c:
                    continue
                qm = QTY_RE.fullmatch(c.replace(',', ''))
                if not qm:
                    continue
                dist = abs(j - i)
                if qty_dist is None or dist < qty_dist:
                    qty_dist = dist
                    try:
                        qty_value = float(qm.group(0))
                    except ValueError:
                        pass
        if name:
            name = _clean_name(name)
            if not _is_header(name):
                return {'name': name, 'unit': unit, 'quantity': qty_value,
                        'unit_type': 'mass' if unit in ('kg','g','mg','mol','mmol') else ('volume' if unit in ('L','mL') else 'mass')}
    return None


def _normalize_table_text(text: str) -> str:
    """Collapse line-breaks inside PDF table cells (e.g. 'Rec.\nMethanol')."""
    # Join a qualifier line with the next material line.
    text = re.sub(r'\b(Rec\.?|Fresh|R\.\s*)\s*\n\s*([A-Za-z])', r'\1 \2', text, flags=re.I)
    # Join a serial-number line that has no unit with the following material line.
    text = re.sub(r'(^|\n)\s*(\d+)\s*\n\s*([A-Za-z])', r'\1\2 \3', text)
    return text


def _extract_table_materials(text: str) -> list[dict[str, Any]]:
    """Extract raw-material rows from space-delimited BMR tables.

    Matches patterns like:
        1 Rec. Methanol 05047 L Ax5 2
        2 DMwater 01035 L Ax9 17
    """
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    text = _normalize_table_text(text)
    # Pattern: optional serial number, optional Rec./Fresh, name (2-40 chars), 3-6 digit item code, unit
    pattern = re.compile(
        r'(?:^|\n)\s*(\d+)?\s*\b(Rec\.?|Fresh|R\.\s*)?\s*([A-Za-z][A-Za-z0-9\.\-\(\)\s]{2,40}?)\s+(\d{3,8})\s+(kg|g|gm|mg|l|ltr|lit|ml|mol|mmol)\b',
        re.I,
    )
    for m in pattern.finditer(text):
        name = _clean_name(m.group(3))
        if _is_header(name):
            continue
        if any(k in name.lower() for k in ('abbreviation', 'utility', 'operation', 'parameter', 'details', 'check')):
            continue
        unit = m.group(5).lower()
        unit_map = {'ltr': 'L', 'lit': 'L', 'lt': 'L', 'l': 'L', 'gm': 'g'}
        unit = unit_map.get(unit, unit)
        qty = None
        # Look for a nearby standalone number after the unit
        tail = text[m.end():m.end()+30]
        qm = re.search(r'(?<![A-Za-z])\d+(?:\.\d+)?(?![A-Za-z])', tail)
        if qm:
            try:
                qty = float(qm.group(0))
            except ValueError:
                pass
        key = f"{name.lower()}|{unit}"
        if key in seen:
            continue
        seen.add(key)
        results.append({
            'name': name,
            'unit': unit,
            'quantity': qty,
            'unit_type': 'mass' if unit in ('kg','g','mg','mol','mmol') else ('volume' if unit in ('L','mL') else 'mass'),
        })
    return results


def parse_raw_materials_from_summary(summary: str) -> list[dict[str, Any]]:
    """Return list of {name, unit, quantity, unit_type} from a BMR summary or process text."""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not summary:
        return results

    # Locate raw-material section
    lower = summary.lower()
    start_idx = -1
    for marker in ['raw material:', 'raw materials:', 'raw material uom', 'raw materials uom', 'raw material details']:
        idx = lower.find(marker)
        if idx >= 0:
            start_idx = idx
            break
    if start_idx < 0:
        # No explicit section: scan whole text for numbered material lines
        section = summary
    else:
        # Heuristic: section ends before next major header
        section = summary[start_idx:]
        end_markers = [
            '\nflow chart', '\nprocedure:', '\nstage-', '\nstage ',
            '\npacking material', '\nappendix', '\nmaterial indent', '\nsafety:',
            '\nequipment:', '\nreaction complies',
        ]
        for em in end_markers:
            eidx = section.lower().find(em)
            if eidx > 50:
                section = section[:eidx]
                break

    for line in section.splitlines():
        line = line.strip()
        if len(line) < 8:
            continue
        if '|' in line:
            candidate = _parse_pipe_table_row(line) or _parse_numbered_line(line)
        else:
            candidate = _parse_numbered_line(line)
        if not candidate:
            continue
        key = candidate['name'].lower()
        if key in seen:
            continue
        seen.add(key)
        results.append(candidate)

    # Also try space-delimited tables common in BMR PDFs (e.g. '1 Rec.\nMethanol 05047 L').
    for tm in _extract_table_materials(section):
        key = tm['name'].lower()
        if key in seen:
            continue
        seen.add(key)
        results.append(tm)

    # If the document has few/no structured tables, try to mine the narrative process.
    if len(results) < 5:
        # Avoid impurity sections that also contain chemical names.
        process_text = summary
        for cutoff in ['\nImpurity', '\nImpurities', '\nQuality Control', '\nList of Instruments', '\nSpecification']:
            idx = process_text.find(cutoff)
            if idx > 0:
                process_text = process_text[:idx]
        for proc in _extract_process_materials(process_text):
            key = proc['name'].lower()
            if key in seen or _is_header(proc['name']):
                continue
            seen.add(key)
            results.append({
                'name': proc['name'],
                'unit': proc['unit'],
                'quantity': proc['quantity'],
                'unit_type': proc['unit_type'],
            })
    return results


_COMMON_SOLVENTS = {
    'methanol', 'ethanol', 'acetone', 'toluene', 'ipa', 'isopropyl alcohol', 'isopropanol',
    'ethyl acetate', 'mdc', 'methylene dichloride', 'dichloromethane', 'dmf', 'dmso',
    'acetonitrile', 'water', 'thf', 'tetrahydrofuran', 'mtbe', 'methyl tertary butyl ether',
    'methyl tertiary butyl ether', 'tert-butyl methyl ether', 'petroleum ether', 'hexane',
    'heptane', 'cyclohexane', 'benzene', 'chloroform', 'carbon tetrachloride',
}


def format_material(m: dict[str, Any] | str) -> str:
    """Return 'Name (qty unit)' for a material dict, or the string itself."""
    if isinstance(m, dict):
        name = str(m.get("name") or "").strip()
        qty = m.get("quantity")
        unit = str(m.get("unit") or "").strip()
        if name and qty is not None and unit:
            return f"{name} ({qty} {unit})"
        return name
    return str(m or "").strip()


def _is_common_solvent_name(name: str) -> bool:
    low = name.lower()
    return any(re.search(r'(?:^|\W)' + re.escape(s) + r'(?:$|\W)', low) for s in _COMMON_SOLVENTS)


def _is_non_material(name: str) -> bool:
    low = name.lower()
    if len(name) < 3:
        return True
    non_materials = {
        'product', 'yield', 'vacuum', 'filter', 'filtration', 'nitrogen', 'air', 'medium',
        'solvent media', 'the', 'and', 'then', 'followed', 'stage', 'procedure', 'process',
        'apparatus', 'equipment', 'reactor', 'oven', 'muffle', 'hplc', 'gc', 'ir', 'uv',
        'polarimeter', 'karl fisher',
    }
    for nm in non_materials:
        if low == nm or low.startswith(nm + ' '):
            return True
    if re.match(r'^stage\s*[-–]?\s*[ivx\d]+', low):
        return True
    # Must contain at least one letter; roman numerals etc.
    if not re.search(r'[a-z]{2,}', low):
        return True
    return False


def _looks_chemical(name: str) -> bool:
    low = name.lower()
    # Known chemical-like fragments
    chem_fragments = [
        'yl', 'ol', 'ide', 'ate', 'ite', 'ine', 'one', 'ane', 'ene', 'yne', ' acid', ' chloride',
        ' bromide', ' iodide', ' fluoride', ' amine', ' amide', ' ester', ' ether', ' ketone',
        ' alcohol', ' phenol', 'thiazole', 'pyrimidine', 'pyrrol', 'morpholine', 'adamant',
        'carbonate', 'hydroxide', 'sulfate', 'sulphate', 'phosphate', 'nitrate', 'nitrite',
        'cyanide', 'isocyanate', 'carboxylate', 'sulfon', 'sulphon', 'acyl',
    ]
    if _is_common_solvent_name(name):
        return True
    if any(frag in low for frag in chem_fragments):
        return True
    # Multi-word names are more likely chemicals than junk
    if len(name.split()) >= 2:
        return True
    return False


def _clean_process_clause(part: str) -> str:
    # Remove leading colons/dashes left over from stage labels
    part = re.sub(r'^[\s:–\-]+', '', part)
    # Remove trailing solvent/media qualifiers
    part = re.sub(r'\s+(?:as\s+(?:a\s+)?solvent(?:\s+media)?|as\s+a\s+medium|as\s+medium|sol\.|solution)\s*$', '', part, flags=re.I)
    # Remove trailing process-verb phrases (keep the material that comes before the verb)
    part = re.sub(r'\s+(?:filter|filtrat|evaporat|evapor|dry|wash|stir|heat|cool|dissolv|add|charge|mix|blend|neutral|quench)\s+.*$', '', part, flags=re.I)
    # Remove trailing purpose/condition fragments
    part = re.sub(r'\s+(?:under\s+vacuum|at\s+room\s+temperature|for\s+\d+.*|overnight|to\s+give|to\s+get|to\s+yield|to\s+obtain)\s*$', '', part, flags=re.I)
    # Remove leading stage labels
    part = re.sub(r'^Stage\s*[-–]?\s*[IVX\d]+[:\.]?\s*', '', part, flags=re.I)
    return part.strip()


def _extract_process_materials(text: str) -> list[dict[str, Any]]:
    """Extract reagents/solvents from narrative process descriptions."""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not text:
        return results

    patterns = [
        # Raw / starting / input material: X (qty)
        (r'(?:raw|starting|input)\s+material[s]?\s*[:\-]\s*(.+?)(?=\n|$)', 'reagent'),
        # X reacts with Y ...
        (r'(?:^|\.\s*|\n\s*)(?:Stage[^a-zA-Z0-9]*[IVX\d]+\s*[:\.]?\s*)?([A-Za-z0-9][A-Za-z0-9\s\-\(\),]{2,60}?)\s+react(?:s|ed|ing)?\s+with', 'reagent'),
        # reacts with X (and Y) in presence of Z
        (r'react(?:s|ed|ing)?\s+with\s+(.+?)(?=\s+(?:in\s+(?:the\s+)?presence|to\s+(?:give|get|yield|obtain)|followed|and\s+then|\.|$))', 'reagent'),
        # in (the) presence of X
        (r'in\s+(?:the\s+)?presence\s+of\s+(.+?)(?=\s+(?:to\s+(?:give|get|yield|obtain)|followed|and\s+then|\.|$))', 'medium'),
        # purification using X
        (r'purification\s+(?:using|with)\s+(.+?)(?=\s+(?:to\s+(?:give|get|yield|obtain)|followed|and\s+then|\.|$))', 'solvent'),
        # dissolved in X
        (r'dissolved\s+(?:in|into)\s+(.+?)(?=\s+(?:to\s+(?:give|get|yield|obtain)|and\s+then|\.|$))', 'solvent'),
        # treated with X
        (r'treat(?:ed|ing)?\s+with\s+(.+?)(?=\s+(?:to\s+(?:give|get|yield|obtain)|followed|and\s+then|\.|$))', 'reagent'),
        # charged with X
        (r'charged\s+(?:with|and)\s+(.+?)(?=\s+(?:to\s+(?:give|get|yield|obtain)|followed|and\s+then|\.|$))', 'reagent'),
    ]

    for pat, default_type in patterns:
        for m in re.finditer(pat, text, re.I):
            clause = m.group(1)
            for part in re.split(r',|;|\band\b', clause):
                part = part.strip()
                part = _clean_process_clause(part)
                if not part or _is_header(part) or _is_non_material(part) or not _looks_chemical(part):
                    continue
                key = part.lower()
                if key in seen:
                    continue
                seen.add(key)
                is_solvent = default_type == 'solvent' or _is_common_solvent_name(part)
                unit = 'L' if is_solvent else 'kg'
                unit_type = 'volume' if is_solvent else 'mass'
                results.append({'name': _clean_name(part), 'unit': unit, 'quantity': None,
                                'unit_type': unit_type, 'is_solvent': is_solvent})
    return results


def parse_solvents_from_summary(summary: str, existing: list[str] | None = None) -> list[dict[str, Any]]:
    """Return solvents found in raw-material section that are common solvents."""
    rms = parse_raw_materials_from_summary(summary)
    solvents: list[dict[str, Any]] = []
    seen = {s.lower() for s in (existing or [])}
    for rm in rms:
        low = rm['name'].lower()
        if _is_common_solvent_name(low):
            if low not in seen:
                solvents.append(rm)
                seen.add(low)
    return solvents


def _extract_conditions(text: str) -> dict[str, Any]:
    """Parse numeric process conditions from a stage/procedure block."""
    if not text:
        return {}
    conditions: dict[str, Any] = {}

    # Temperature: "25-30°C", "maintain 25 °C", "at 60° C"
    temps: list[float] = []
    for m in re.finditer(r'(\d+(?:\.\d+)?)\s*(?:[-–]\s*(\d+(?:\.\d+)?))?\s*(?:°|deg(?:ree)?s?)?\s*C\b', text, re.I):
        if m.group(2):
            temps.extend([float(m.group(1)), float(m.group(2))])
        else:
            temps.append(float(m.group(1)))
    if temps:
        conditions['temperature_c_min'] = min(temps)
        conditions['temperature_c_max'] = max(temps)

    # pH: "pH 6.5-7.5", "pH : 7.0"
    phs: list[float] = []
    for m in re.finditer(r'pH\s*[:=\-]?\s*(\d+\.?\d*)\s*(?:[-–]\s*(\d+\.?\d*))?', text, re.I):
        phs.append(float(m.group(1)))
        if m.group(2):
            phs.append(float(m.group(2)))
    if phs:
        conditions['ph_min'] = min(phs)
        conditions['ph_max'] = max(phs)

    # Pressure: "5 bar", "-0.08 bar", "100 mbar"
    pressures: list[tuple[float, str]] = []
    for m in re.finditer(r'(-?\d+\.?\d*)\s*(bar|mbar|psi|kPa|mmHg|kg/cm2?)\b', text, re.I):
        val = float(m.group(1))
        unit = m.group(2).lower()
        if unit == 'mbar':
            val = val / 1000.0
            unit = 'bar'
        elif unit == 'psi':
            val = val * 0.0689476
            unit = 'bar'
        elif unit == 'kpa':
            val = val / 100.0
            unit = 'bar'
        elif unit in ('mmhg', 'kg/cm2', 'kg/cm'):
            continue
        pressures.append((val, unit))
    if pressures:
        conditions['pressure_bar'] = pressures[0][0]

    # RPM / mixing speed
    rpm_match = re.search(r'(\d+)\s*rpm\b', text, re.I)
    if rpm_match:
        conditions['mixing_speed_rpm'] = int(rpm_match.group(1))

    # Hold/reaction time: "hold for 2 h", "reflux for 30 min", "stir 1 hour"
    times_min: list[float] = []
    for m in re.finditer(r'(?:hold|stir|reflux|digest|react|maintain).*?(\d+\.?\d*)\s*(min|minutes|hr|hrs|hour|hours|h)\b', text, re.I):
        val = float(m.group(1))
        unit = m.group(2).lower()
        if unit.startswith('h'):
            val *= 60.0
        times_min.append(val)
    if times_min:
        conditions['hold_time_min'] = times_min[0]

    # Atmosphere
    atm_match = re.search(r'under\s+(nitrogen|argon|vacuum|air|N2)\b', text, re.I) or \
                re.search(r'\b(nitrogen|argon)\s+atmosphere\b', text, re.I)
    if atm_match:
        conditions['atmosphere'] = atm_match.group(1).capitalize()

    return conditions


def _extract_equipment(text: str) -> list[str]:
    """Extract equipment mentions from a stage/procedure block."""
    if not text:
        return []
    equipment: list[str] = []
    seen: set[str] = set()

    # Multi-word equipment phrases and typed equipment (longest first so adjectives stay attached).
    keywords = [
        'rotary vacuum dryer', 'vacuum tray dryer', 'tray dryer',
        'glass lined reactor', 'stainless steel reactor', 'ss reactor', 'ms reactor',
        'sparkler filter', 'leaf filter', 'candle filter', 'nutsche filter',
        'multi mill', 'vibro sifter', 'double cone blender', 'ribbon blender',
        'reactor', 'centrifuge', 'dryer', 'filter', 'blender', 'tank', 'vessel',
        'condenser', 'sifter', 'nutsche', 'vacuum pump', 'heat exchanger',
    ]

    for kw in keywords:
        pattern = r'(?:^|[^A-Za-z0-9])' + re.escape(kw) + r'(?:$|[^A-Za-z0-9])'
        for m in re.finditer(pattern, text, re.I):
            # Reject if embedded in a checklist/instruction sentence (Ensure, Check, Verify, Clean).
            line_start = text.rfind('\n', 0, m.start())
            if line_start < 0:
                line_start = 0
            prefix = text[line_start:m.start()]
            if re.search(r'\b(ensure|check|verify|cleaning of)\b', prefix, re.I):
                continue
            item = kw.title()
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            equipment.append(item)

    # Sized equipment: "1000 L reactor", "500 kg dryer", "SS reactor" — add size/unit info when available.
    for m in re.finditer(r'(\d+\.?\d*)\s*(L|kL|kg|g)\s+((?:SS|MS|CS|GLR|SSR|Glass Lined|Stainless Steel)\s+)?(reactor|centrifuge|dryer|filter|blender|tank|vessel|condenser|sifter|nutsche)', text, re.I):
        size = m.group(1)
        unit = m.group(2)
        typed = (m.group(3) or '').strip()
        name = m.group(4).capitalize()
        item = f"{size}{unit} {typed} {name}".strip()
        item = re.sub(r'\s+\d+[-–]\d+.*$', '', item)
        item = re.sub(r'\s+°?C\b.*$', '', item, flags=re.I)
        key = re.sub(r'\s+', ' ', item.lower())
        if len(item) < 6 or key in seen:
            continue
        seen.add(key)
        equipment.append(item)

    # Drop generic items that are already covered by a more specific entry (e.g. "Reactor" when "Glass Lined Reactor" exists).
    filtered: list[str] = []
    lower_items = [e.lower() for e in equipment]
    for item in equipment:
        item_lower = item.lower()
        if any(item != other and item_lower in other.lower() for other in equipment):
            continue
        filtered.append(item)
    return filtered


def _extract_stage_materials(block: str) -> list[dict[str, Any]]:
    """Combine narrative and table-derived materials for a stage block."""
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for m in _extract_process_materials(block):
        key = m['name'].lower()
        if key in seen:
            continue
        seen.add(key)
        results.append(m)
    for m in parse_raw_materials_from_summary(block):
        key = m['name'].lower()
        if key in seen:
            continue
        seen.add(key)
        is_solvent = _is_common_solvent_name(m['name'])
        results.append({**m, 'is_solvent': is_solvent})
    for m in _extract_table_materials(block):
        key = m['name'].lower()
        if key in seen:
            continue
        seen.add(key)
        is_solvent = _is_common_solvent_name(m['name'])
        results.append({**m, 'is_solvent': is_solvent})
    return results


def extract_route_stages_from_text(text: str) -> list[dict[str, Any]]:
    """Extract stage-level process blocks from narrative BMR process descriptions."""
    if not text:
        return []
    # Split by Stage headers like Stage I, Stage-II, Stage 1, Stage-Final
    parts = re.split(r'(?:^|\n)\s*Stage\s*[-–]?\s*([IVX\d]+|Final)\s*[:\.]?\s*', text, flags=re.I)
    stages: list[dict[str, Any]] = []
    if len(parts) < 3:
        # No clear stage headers; treat whole text as one stage
        materials = _extract_stage_materials(text)
        rms = [m for m in materials if not m.get('is_solvent')]
        sols = [m for m in materials if m.get('is_solvent')]
        return [{
            'stage_no': 1,
            'stage_name': 'Process',
            'raw_materials': rms,
            'solvents': sols,
            'conditions': _extract_conditions(text),
            'equipment': _extract_equipment(text),
            'notes': text[:500],
        }]
    # parts[0] is preamble, then pairs (stage_label, block)
    for i in range(1, len(parts), 2):
        label = parts[i].strip()
        block = parts[i + 1] if i + 1 < len(parts) else ''
        sentence = re.split(r'[\.\n]', block, maxsplit=1)[0].strip()
        materials = _extract_stage_materials(block)
        rms = [m for m in materials if not m.get('is_solvent')]
        sols = [m for m in materials if m.get('is_solvent')]
        stages.append({
            'stage_no': _roman_or_int(label),
            'stage_name': f"Stage {label}" + (f" - {sentence[:60]}" if sentence else ''),
            'raw_materials': rms,
            'solvents': sols,
            'conditions': _extract_conditions(block),
            'equipment': _extract_equipment(block),
            'notes': block[:500],
        })
    return stages


def _roman_or_int(label: str) -> int:
    roman = {'i': 1, 'ii': 2, 'iii': 3, 'iv': 4, 'v': 5, 'vi': 6, 'vii': 7, 'viii': 8, 'ix': 9, 'x': 10}
    low = label.lower()
    if low in roman:
        return roman[low]
    try:
        return int(low)
    except ValueError:
        return len(roman) + 1


def fetch_best_bmr_text(product_name: str) -> str:
    """Return the best available BMR text (summary + full extracted text) for a product."""
    from .database import db

    like = f"%{product_name}%"
    rows = db.query(
        """
        SELECT d.original_name, d.document_type,
               COALESCE(d.extracted_summary, '') AS extracted_summary,
               COALESCE(f.extracted_text, '') AS extracted_text
        FROM enterprise_bmr_documents d
        LEFT JOIN ingest_files f ON f.id = d.ingest_file_id
        WHERE (COALESCE(d.extracted_summary, '') != '' OR COALESCE(f.extracted_text, '') != '')
          AND (lower(d.product_name) = lower(?) OR lower(d.original_name) LIKE lower(?))
        ORDER BY
            CASE WHEN lower(d.document_type) IN ('bmr', 'bpcr') THEN 0 ELSE 1 END,
            d.created_at DESC
        LIMIT 15
        """,
        (product_name, like),
    )
    if not rows:
        return ""

    scored: list[tuple[int, int, str]] = []
    for row in rows:
        name = (row.get("original_name") or "").lower()
        summary = row.get("extracted_summary", "") or ""
        full_text = row.get("extracted_text", "") or ""
        # Avoid duplicating the summary if it is already the start of the full text.
        if full_text.strip().startswith(summary.strip()):
            text = full_text
        elif summary.strip().startswith(full_text.strip()):
            text = summary
        else:
            text = f"{summary}\n{full_text}"
        text = text[:60000]
        count = len(parse_raw_materials_from_summary(text))
        # Prefer consumption/costing documents over flow charts or TLC docs.
        priority = 0
        if "consumption" in name:
            priority = 3
        elif "cost" in name or "costing" in name:
            priority = 2
        elif "raw material" in text.lower():
            priority = 1
        # Boost docs that contain narrative process signals (reacts, dissolved, etc.)
        low_text = text.lower()
        if any(k in low_text for k in ("reacts with", "react with", "dissolved in", "in presence of", "purification using")):
            priority += 1
        scored.append((priority, count, text))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    if not scored:
        return ""
    # If the best doc has a rich table, use it alone. Otherwise merge the top two docs
    # so a sparse BMR can still pick up process-description solvents/reagents.
    if scored[0][1] >= 5:
        return scored[0][2]
    top_texts = [s[2] for s in scored[:2]]
    return "\n".join(top_texts)


def fetch_best_bmr_summary(product_name: str) -> str:
    """Return the extracted BMR summary most likely to contain a usable raw-material table."""
    return fetch_best_bmr_text(product_name)
