from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


TRUST_VERIFIED = "verified"
TRUST_SOURCED = "sourced"
TRUST_MEMORY = "memory-backed"
TRUST_DRAFT = "draft"
TRUST_UNVERIFIED = "unverified"
TRUST_LEVELS = {TRUST_VERIFIED, TRUST_SOURCED, TRUST_MEMORY, TRUST_DRAFT, TRUST_UNVERIFIED}


@dataclass(frozen=True)
class EvidenceItem:
    kind: str
    title: str
    source_uri: str = ""
    excerpt: str = ""
    score: float = 0.0
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["metadata"] = data["metadata"] or {}
        return data


def _clean(value: Any, limit: int = 1200) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _dedupe(items: list[EvidenceItem], limit: int = 12) -> list[EvidenceItem]:
    out: list[EvidenceItem] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        key = (item.kind, item.title.lower(), item.source_uri.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def evidence_from_search(result: dict[str, Any] | None, *, limit: int = 8) -> list[dict[str, Any]]:
    result = result or {}
    items: list[EvidenceItem] = []
    for row in (result.get("results") or [])[:limit]:
        items.append(
            EvidenceItem(
                kind="web",
                title=_clean(row.get("title") or row.get("url") or "Web result", 220),
                source_uri=_clean(row.get("url"), 1000),
                excerpt=_clean(row.get("snippet"), 650),
                score=0.85 if row.get("url") else 0.55,
                metadata={"provider": row.get("source") or result.get("provider"), "query": result.get("query")},
            )
        )
    return [x.to_dict() for x in _dedupe(items, limit)]


def evidence_from_brain_context(ctx: dict[str, Any] | None, *, limit: int = 8) -> list[dict[str, Any]]:
    ctx = ctx or {}
    items: list[EvidenceItem] = []
    for hit in (ctx.get("hits") or [])[:limit]:
        kind = _clean(hit.get("kind") or "memory", 80)
        score = float(hit.get("score") or 0.0)
        items.append(
            EvidenceItem(
                kind="memory" if kind == "memory" else ("research" if kind == "research" else "rag"),
                title=_clean(hit.get("title") or "Brain context", 220),
                source_uri=_clean(hit.get("url") or hit.get("source") or "", 1000),
                excerpt=_clean(hit.get("content"), 650),
                score=score,
                metadata={"tags": hit.get("tags") or [], "source": hit.get("source") or "", "created_at": hit.get("created_at")},
            )
        )
    return [x.to_dict() for x in _dedupe(items, limit)]


def evidence_from_artifact(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    result = result or {}
    if not result:
        return []
    ledger = result.get("ledger") or {}
    evidence = EvidenceItem(
        kind="artifact",
        title=_clean(result.get("title") or result.get("filename") or result.get("type") or "Generated artifact", 220),
        source_uri=_clean(result.get("file_url") or result.get("url") or ledger.get("path") or "", 1000),
        excerpt=_clean(result.get("note") or result.get("filename") or "", 500),
        score=0.98 if result.get("verified") and result.get("sha256") else 0.72,
        metadata={
            "sha256": result.get("sha256") or ledger.get("sha256"),
            "ledger": ledger,
            "verified": bool(result.get("verified")),
            "kind": result.get("kind") or result.get("type"),
        },
    )
    return [evidence.to_dict()]


def evidence_from_mailbox_digest(digest: dict[str, Any] | None, *, limit: int = 8) -> list[dict[str, Any]]:
    digest = digest or {}
    items: list[EvidenceItem] = []
    for msg in (digest.get("messages") or [])[: max(1, limit // 2)]:
        items.append(
            EvidenceItem(
                kind="mailbox",
                title=_clean(msg.get("subject") or "Mailbox item", 220),
                source_uri=_clean(msg.get("source_url") or msg.get("id") or "", 1000),
                excerpt=_clean(msg.get("snippet") or msg.get("body"), 650),
                score=0.82,
                metadata={"provider": msg.get("provider"), "sender": msg.get("sender"), "received_at": msg.get("received_at")},
            )
        )
    for cap in (digest.get("captures") or [])[: max(1, limit // 2)]:
        items.append(
            EvidenceItem(
                kind="capture",
                title=_clean(cap.get("title") or "Capture", 220),
                source_uri=_clean(cap.get("url") or cap.get("id") or "", 1000),
                excerpt=_clean(cap.get("text"), 650),
                score=0.78,
                metadata={"source": cap.get("source"), "kind": cap.get("kind"), "created_at": cap.get("created_at")},
            )
        )
    return [x.to_dict() for x in _dedupe(items, limit)]


def evidence_from_action(action: dict[str, Any] | None) -> list[dict[str, Any]]:
    action = action or {}
    if not action:
        return []
    return [
        EvidenceItem(
            kind="action",
            title=_clean(action.get("title") or action.get("action_type") or "Action", 220),
            source_uri=_clean(action.get("id") or action.get("action_id") or "", 220),
            excerpt=_clean(action.get("summary") or action.get("status") or "", 500),
            score=0.95 if action.get("status") == "completed" and action.get("verified", True) else 0.55,
            metadata={"status": action.get("status"), "ledger_hash": action.get("ledger_hash") or action.get("record_hash"), "autonomy": action.get("autonomy")},
        ).to_dict()
    ]


def merge_evidence(*groups: list[dict[str, Any]] | None, limit: int = 12) -> list[dict[str, Any]]:
    items: list[EvidenceItem] = []
    for group in groups:
        for raw in group or []:
            items.append(
                EvidenceItem(
                    kind=_clean(raw.get("kind") or "source", 80),
                    title=_clean(raw.get("title") or "Evidence", 220),
                    source_uri=_clean(raw.get("source_uri") or raw.get("url") or "", 1000),
                    excerpt=_clean(raw.get("excerpt") or raw.get("snippet") or "", 650),
                    score=float(raw.get("score") or 0.0),
                    metadata=raw.get("metadata") or {},
                )
            )
    return [x.to_dict() for x in _dedupe(items, limit)]


def build_trust(
    *,
    route: str = "",
    evidence: list[dict[str, Any]] | None = None,
    missing_evidence: list[str] | None = None,
    requested_level: str | None = None,
    action_id: str = "",
    ledger_hash: str = "",
    query_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = merge_evidence(evidence or [])
    missing = [_clean(x, 240) for x in (missing_evidence or []) if _clean(x, 240)]
    kinds = {str(e.get("kind") or "") for e in evidence}
    route_l = (route or "").lower()

    if action_id and ledger_hash and evidence:
        level = TRUST_VERIFIED
        score = 0.96
        reason = "Action or artifact has ledger-backed proof plus evidence."
    elif "artifact" in kinds and any((e.get("metadata") or {}).get("sha256") for e in evidence):
        level = TRUST_VERIFIED
        score = 0.94
        reason = "Generated artifact has a hash/ledger proof."
    elif "web" in kinds or "research" in kinds:
        level = TRUST_SOURCED
        score = 0.82
        reason = "Answer is grounded in web or stored research sources."
    elif {"rag", "memory", "mailbox", "capture"} & kinds:
        level = TRUST_MEMORY
        score = 0.72
        reason = "Answer is grounded in local memory, mailbox, capture, or RAG context."
    elif requested_level == TRUST_DRAFT or any(x in route_l for x in ("campaign", "calendar", "draft")):
        level = TRUST_DRAFT
        score = 0.58
        reason = "This is a generated draft or plan and needs user review before external use."
    else:
        level = TRUST_UNVERIFIED
        score = 0.35
        reason = "No source, tool result, or local evidence was attached."

    if missing:
        score = max(0.2, round(score - min(0.25, len(missing) * 0.05), 2))

    confidence = {"score": round(score, 2), "reason": reason}
    return {
        "trust_level": level,
        "confidence": confidence,
        "evidence_count": len(evidence),
        "evidence": evidence,
        "missing_evidence": missing,
        "action_id": action_id,
        "ledger_hash": ledger_hash,
        "query_plan": query_plan or None,
        "policy": "verification-first: cite evidence, mark uncertainty, and require approval for external or irreversible actions.",
    }


def compact_trust(trust: dict[str, Any] | None) -> dict[str, Any]:
    trust = trust or {}
    return {
        "trust_level": trust.get("trust_level") or TRUST_UNVERIFIED,
        "confidence": trust.get("confidence") or {"score": 0.0, "reason": "No trust envelope attached."},
        "evidence_count": int(trust.get("evidence_count") or len(trust.get("evidence") or [])),
        "missing_evidence": trust.get("missing_evidence") or [],
        "action_id": trust.get("action_id") or "",
        "ledger_hash": trust.get("ledger_hash") or "",
    }


def trust_to_text(trust: dict[str, Any] | None) -> str:
    compact = compact_trust(trust)
    return json.dumps(compact, ensure_ascii=False, sort_keys=True)
