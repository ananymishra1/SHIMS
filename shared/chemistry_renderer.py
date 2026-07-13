"""Chemistry structure rendering using RDKit for SHIMS Enterprise.

Generates SVG/PNG depictions of molecules from SMILES, InChI, or common name
(where a curated SMILES is known). Falls back gracefully if RDKit is missing.
"""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

try:
    from rdkit import Chem
    from rdkit.Chem import Draw, rdDepictor
    RDKIT_AVAILABLE = True
except Exception:  # pragma: no cover
    RDKIT_AVAILABLE = False
    Chem = None  # type: ignore
    Draw = None  # type: ignore
    rdDepictor = None  # type: ignore

from .config import GENERATED_DIR


# Minimal curated SMILES map for common APIs referenced in SHIMS corpus.
# These are illustrative and should be validated/expanded by the chemistry team.
COMMON_API_SMILES: dict[str, str] = {
    'fluconazole': 'OC(Cn1cncn1)(Cn2cncn2)c3ccc(F)cc3F',
    'itraconazole': 'CCCC(=O)N1CCN(C(=O)OC2CC3CCC(C2)N3CC)CC1',
    'losartan': 'CCCCc1nc(Cl)c(CO)n1Cc2ccc(-c3ccccc3-c3nn[nH]n3)cc2',
    'duloxetine': 'CNCCC(c1cccs1)Oc2ccccc2F',
    'risperidone': 'Cc1nc2n(c(=O)c1CCN1CCC(CC1)c3c(Cl)ccc(Cl)c3)CCCC2',
    'gabapentin': 'O=C(O)CC1(CCN)CCCCC1',
    'telmisartan': 'COc1ccc2nc(N3CCN(C)CC3)nc(C)c2c1-c4ccc(OC(C)=O)cc4C',
    'dapagliflozin': 'OC[C@H]1O[C@@H](c2ccc(cc2)c3ccccc3)[C@H](O)[C@@H](O)[C@@H]1O',
    'ambroxol hcl': 'Cc1cc(N)c(C)c(OCC(=O)N)c1Br.Cl',
    'ambroxol': 'Cc1cc(N)c(C)c(OCC(=O)N)c1Br',
    'ketoconazole': 'CC(=O)N1CCN(CC1)c2ccc(OCc3cncn3C)cc2',
    'rosuvastatin': 'CC(C)c1nc(N(C)S(=O)(=O)C)nc(C(C)C)c1/C=C/C(=O)NC2CCCCC2',
    'olmesartan': 'Cc1c(c2ccc(cc2n1Cc3ccc(cc3)C(=O)O)C(=O)O)CCC(=O)O',
    'montelukast': 'C[C@H](O[C@@H]1OCC[C@@H]1C)c2cc(C(C)(C)C)cc(-c3ccc(Cl)cc3)c2OCC(=O)O',
    'dfta': 'CC(=O)Oc1ccccc1C(=O)O',  # illustrative placeholder
}


def _safe_name(text: str) -> str:
    h = hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]
    return h


def resolve_smiles(name_or_smiles: str) -> str | None:
    candidate = str(name_or_smiles or '').strip()
    if not candidate:
        return None
    if RDKIT_AVAILABLE and Chem is not None:
        mol = Chem.MolFromSmiles(candidate)
        if mol is not None:
            return candidate
    # Try curated names
    key = candidate.lower()
    if key in COMMON_API_SMILES:
        return COMMON_API_SMILES[key]
    # Try partial match
    for k, smi in COMMON_API_SMILES.items():
        if k in key or key in k:
            return smi
    return None


def render_svg(name_or_smiles: str, width: int = 300, height: int = 200) -> str:
    """Return SVG string for the molecule, or a fallback error SVG."""
    if not RDKIT_AVAILABLE or Chem is None or Draw is None or rdDepictor is None:
        return _fallback_svg('RDKit not available')
    smi = resolve_smiles(name_or_smiles)
    if not smi:
        return _fallback_svg('Structure not found')
    mol = Chem.MolFromSmiles(smi)
    if mol is None:
        return _fallback_svg('Invalid SMILES')
    try:
        rdDepictor.Compute2DCoords(mol)
    except Exception:
        pass
    try:
        svg = Draw.rdMolDraw2D.MolDraw2DSVG(width, height)
        svg.DrawMolecule(mol)
        svg.FinishDrawing()
        return svg.GetDrawingText()
    except Exception as exc:
        return _fallback_svg(f'Render error: {exc}')


def render_png(name_or_smiles: str, width: int = 300, height: int = 200) -> bytes:
    if not RDKIT_AVAILABLE or Chem is None or Draw is None or rdDepictor is None:
        return _fallback_png(width, height)
    smi = resolve_smiles(name_or_smiles)
    if not smi:
        return _fallback_png(width, height)
    mol = Chem.MolFromSmiles(smi)
    if mol is None:
        return _fallback_png(width, height)
    try:
        rdDepictor.Compute2DCoords(mol)
    except Exception:
        pass
    try:
        img = Draw.MolToImage(mol, size=(width, height))
        from io import BytesIO
        buf = BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()
    except Exception:
        return _fallback_png(width, height)


def render_to_file(name_or_smiles: str, width: int = 300, height: int = 200, format: str = 'svg') -> Path:
    safe = _safe_name(f'{name_or_smiles}_{width}_{height}_{format}')
    path = GENERATED_DIR / f'chem_{safe}.{format}'
    if format.lower() == 'svg':
        path.write_text(render_svg(name_or_smiles, width, height), encoding='utf-8')
    else:
        path.write_bytes(render_png(name_or_smiles, width, height))
    return path


def _fallback_svg(message: str) -> str:
    return f"""<svg xmlns='http://www.w3.org/2000/svg' width='300' height='120'>
  <rect width='100%' height='100%' fill='#f8fafc' stroke='#cbd5e1'/>
  <text x='50%' y='50%' dominant-baseline='middle' text-anchor='middle' fill='#64748b' font-family='sans-serif' font-size='14'>{message}</text>
</svg>"""


def _fallback_png(width: int, height: int) -> bytes:
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new('RGB', (width, height), '#f8fafc')
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype('arial.ttf', 14)
        except Exception:
            font = ImageFont.load_default()
        text = 'Structure unavailable'
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((width - tw) / 2, (height - th) / 2), text, fill='#64748b', font=font)
        from io import BytesIO
        buf = BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()
    except Exception:
        return b''


def list_known_structures() -> list[dict[str, Any]]:
    return [{'name': k, 'smiles': v} for k, v in COMMON_API_SMILES.items()]


def structure_data_url(name_or_smiles: str, width: int = 300, height: int = 200) -> str:
    svg = render_svg(name_or_smiles, width, height)
    encoded = base64.b64encode(svg.encode('utf-8')).decode('ascii')
    return f'data:image/svg+xml;base64,{encoded}'
