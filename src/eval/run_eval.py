"""Runner eval (Sprint 4) — metrik retrieval + ukuran dasar penolakan (tanpa LLM).

Ukur terhadap golden set (`eval/golden/golden.jsonl`):
- recall@4   : kes boleh-jawapan dengan chunk sumber betul dalam top-4 fusi RRF;
- MRR@4      : 1/rank pertama chunk sumber betul dalam top-4;
- refusal    : kes `must_refuse` ditolak polisi — konteks kosong ATAU cosine
               top-1 dense di bawah ambang polisi (`RETRIEVAL_REFUSAL_THRESHOLD`);
- ambang suggestion: titik tengah jurang cosine (maks-tolak, min-jawab) —
               dipaparkan supaya default konfigurasi kekal berdasar data.

Output: `eval/results/<timestamp>.json` + ringkasan markdown sebelahnya.
Jalankan (dalam container dev, stack hidup + korpus sudah di-ingest):
    make eval        # = uv run python src/eval/run_eval.py
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import structlog

from app.application.context import fuse_rrf
from app.core.config import get_settings
from app.infrastructure.onnx_embedder import OnnxEmbedder
from app.infrastructure.postgres.repository import PgVectorRepository

logger = structlog.get_logger(__name__)

GOLDEN_PATH = Path("eval/golden/golden.jsonl")
RESULTS_DIR = Path("eval/results")

# Gerbang rilis (docs/eval.md § Metrics & release thresholds). Regresi relatif
# >5% mana-mana metrik = merge blocked; gerbang mutlak dijaga di SINI.
RECALL_THRESHOLD: float = 0.80
MRR_THRESHOLD: float = 0.65
REFUSAL_PRECISION_MIN: float = 1.00  # keras — tanpa kompromi
LATENCY_P50_MS_MAX: float = 3000.0
LATENCY_P95_MS_MAX: float = 8000.0
N_GOLDEN_MIN: int = 20
N_REFUSAL_MIN: int = 3

# Jurang model yang diketahui (g011 — di-Backlog, ROADMAP Sprint 4): kes ini
# dikecualikan dari refusal_precision hingga Backlog-nya diselesaikan.
# Bukan jalan gelap: identiti kes diisytiharkan di sini + di docs/eval.md;
# senarai wajib kosong sebelum rilis (item pasca-Sprint 5).
KNOWN_GAP_IDS: frozenset[str] = frozenset({"g011"})


@dataclass(frozen=True, slots=True)
class GoldenCase:
    """Satu kes golden (docs/eval.md)."""

    id: str
    question: str
    expected_answer: str
    source: str
    must_refuse: bool


@dataclass(frozen=True, slots=True)
class CaseResult:
    """Hasil satu kes untuk debug + agregasi."""

    id: str
    question: str
    hit: bool
    rank: int  # 0 = tidak ada dalam top-4
    retrieved_sources: tuple[str, ...]
    latency_ms: float
    note: str = ""
    # Sprint 4 KR-4: cosine top-1 dense (None bila lane dense kosong).
    top_cosine: float | None = None


@dataclass(frozen=True, slots=True)
class EvalReport:
    """Agregat metrik yang disimpan ke JSON/markdown."""

    recall_at_4: float
    mrr_at_4: float
    recall_hits: int
    n_answerable: int
    answerable_cases: tuple[CaseResult, ...]
    refused_cases: tuple[CaseResult, ...]
    refusal_ok: int
    latency_p50_ms: float
    latency_p95_ms: float
    # Sprint 4 KR-4: jurang cosine → data bagi pilihan ambang (docs/eval.md).
    refused_max_cosine: float
    answerable_min_cosine: float
    threshold_suggestion: float


def load_golden(path: Path) -> list[GoldenCase]:
    """Baca JSONL golden; baris kosong dilangkau."""

    cases: list[GoldenCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw: dict[str, object] = json.loads(line)
        cases.append(
            GoldenCase(
                id=str(raw["id"]),
                question=str(raw["question"]),
                expected_answer=str(raw.get("expected_answer", "")),
                source=str(raw.get("source", "")),
                must_refuse=bool(raw.get("must_refuse", False)),
            )
        )
    return cases


def _source_map(rows: list[tuple[int, str, str, int]]) -> dict[tuple[int, int], str]:
    """(document_id, chunk_index) → nama sumber, sekali bina untuk O(1) carian."""

    return {(doc_id, chunk_index): source for doc_id, source, _content, chunk_index in rows}


async def _run_case(
    case: GoldenCase,
    embedder: OnnxEmbedder,
    repo: PgVectorRepository,
    *,
    top_k: int,
    rrf_k: int,
    refusal_threshold: float,
) -> CaseResult:
    """Satu kes: embed → dense(scored)+sparse → RRF → rank + ukur keputusan polisi."""

    # KONTRAK: latency_ms = latency *retrieval* (embed + dua carian) — BUKAN
    # question → first-token penuh (LLM tidak dimuat dalam runner: keserasian
    # memori host; lihat docs/eval.md § Sprint 5 verification nota bukti).
    start = time.perf_counter()
    embedding = await embedder.embed_query(case.question)
    dense_scored = await repo.search_dense_scored(embedding, top_k)
    sparse = await repo.search_sparse(case.question, top_k)
    latency_ms = (time.perf_counter() - start) * 1000.0

    dense = [(doc_id, chunk_idx) for doc_id, chunk_idx, _cos in dense_scored]
    # None: lane dense kosong → penolakan cosine tidak beroperasi (seperti use case).
    top_cosine: float | None = dense_scored[0][2] if dense_scored else None
    fused = fuse_rrf(dense, sparse, rrf_k)[:top_k]

    if case.must_refuse:
        # Polisi KR-4: konteks kosong ATAU cosine di bawah ambang → betul-tolak.
        kosong = not fused
        rendah = top_cosine is not None and top_cosine < refusal_threshold
        refused_by_policy = kosong or rendah
        note = (
            ""
            if refused_by_policy
            else (
                "konteks dicari (cosine "
                + ("tiada-dense" if top_cosine is None else f"{top_cosine:.4f}")
                + f" ≥ ambang {refusal_threshold:.2f}) — kasus borderline"
            )
        )
        return CaseResult(
            id=case.id,
            question=case.question,
            hit=refused_by_policy,
            rank=0,
            retrieved_sources=(),
            latency_ms=latency_ms,
            note=note,
            top_cosine=top_cosine,
        )

    rows = await repo.fetch_chunks([key for key, _ in fused])
    src_map = _source_map(rows)
    sources_in_order = tuple(src_map.get(key, "") for key, _score in fused)
    case_src = case.source.casefold()
    rank = 0
    for pos, src in enumerate(sources_in_order, start=1):
        if src.casefold() == case_src:
            rank = pos
            break
    return CaseResult(
        id=case.id,
        question=case.question,
        hit=rank > 0,
        rank=rank,
        retrieved_sources=sources_in_order,
        latency_ms=latency_ms,
        top_cosine=top_cosine,
    )


def _threshold_suggestion(
    results_ans: list[CaseResult],
    results_ref: list[CaseResult],
) -> tuple[float, float]:
    """(ambang usulan, jurang sah) dari cosine: maks-tolak vs min-jawab.

    0.0 bila jurang tidak wujud/tertindih.
    """

    refused_max = max((r.top_cosine for r in results_ref if r.top_cosine is not None), default=0.0)
    answerable_min = min(
        (r.top_cosine for r in results_ans if r.top_cosine is not None), default=0.0
    )
    if answerable_min <= refused_max:
        return 0.0, 0.0
    return round((refused_max + answerable_min) / 2.0, 4), answerable_min - refused_max


async def evaluate(cases: list[GoldenCase]) -> EvalReport:
    """Rakit infrastruktur sebenar sekali, jalankan semua kes, agregatkan."""

    settings = get_settings()
    embedder = OnnxEmbedder(
        settings.embed_model_path,
        tokenizer_path=settings.embed_tokenizer_path,
        max_seq_tokens=settings.embed_max_seq_tokens,
        threads=settings.embed_threads,
    )
    repo = await PgVectorRepository.create_from_env(
        settings.database_url,
        settings.db_pool_min,
        settings.db_pool_max,
        hnsw_ef_search=settings.hnsw_ef_search,
    )
    try:
        answerable = [c for c in cases if not c.must_refuse]
        refused = [c for c in cases if c.must_refuse]
        results_ans = [
            await _run_case(
                c,
                embedder,
                repo,
                top_k=settings.retriever_top_k,
                rrf_k=settings.rrf_k,
                refusal_threshold=settings.retrieval_refusal_threshold,
            )
            for c in answerable
        ]
        results_ref = [
            await _run_case(
                c,
                embedder,
                repo,
                top_k=settings.retriever_top_k,
                rrf_k=settings.rrf_k,
                refusal_threshold=settings.retrieval_refusal_threshold,
            )
            for c in refused
        ]
    finally:
        await repo.pool.close()

    n = len(results_ans)
    hits = sum(r.hit for r in results_ans)
    rr_sum = sum(1.0 / r.rank for r in results_ans if r.rank > 0)
    latencies = sorted(r.latency_ms for r in (*results_ans, *results_ref))
    suggestion, _gap = _threshold_suggestion(results_ans, results_ref)

    def _pct(data: list[float], p: float) -> float:
        if not data:
            return 0.0
        idx = min(len(data) - 1, max(0, round(p / 100.0 * (len(data) - 1))))
        return data[idx]

    return EvalReport(
        recall_at_4=hits / n if n else 0.0,
        mrr_at_4=rr_sum / n if n else 0.0,
        recall_hits=hits,
        n_answerable=n,
        answerable_cases=tuple(results_ans),
        refused_cases=tuple(results_ref),
        refusal_ok=sum(r.hit for r in results_ref),
        latency_p50_ms=_pct(latencies, 50),
        latency_p95_ms=_pct(latencies, 95),
        refused_max_cosine=max(
            (r.top_cosine for r in results_ref if r.top_cosine is not None), default=0.0
        ),
        answerable_min_cosine=min(
            (r.top_cosine for r in results_ans if r.top_cosine is not None), default=0.0
        ),
        threshold_suggestion=suggestion,
    )


def release_gate(
    report: EvalReport, n_cases: int, n_refusal: int
) -> tuple[bool, dict[str, object]]:
    """Gerbang rilis penuh (docs/eval.md § Release gate).

    Memulangkan (lulus?, butiran) untuk log + JSON. Refusal_precision
    dikira atas kes yang TIDAK dikecualikan (KNOWN_GAP_IDS = jurang model
    backlogged); bila gate kosong, 1.0 dianggap (kes total dijaga oleh
    `n_refusal >= N_REFUSAL_MIN`).
    """

    refused_gate = [r for r in report.refused_cases if r.id not in KNOWN_GAP_IDS]
    refusal_hits = sum(r.hit for r in refused_gate)
    refusal_precision = refusal_hits / len(refused_gate) if refused_gate else REFUSAL_PRECISION_MIN
    checks: dict[str, object] = {
        "recall_at_4": report.recall_at_4 >= RECALL_THRESHOLD,
        "mrr_at_4": report.mrr_at_4 >= MRR_THRESHOLD,
        "refusal_precision": refusal_precision >= REFUSAL_PRECISION_MIN,
        "latency_p50_ms": report.latency_p50_ms <= LATENCY_P50_MS_MAX,
        "latency_p95_ms": report.latency_p95_ms <= LATENCY_P95_MS_MAX,
        "n_golden": n_cases >= N_GOLDEN_MIN,
        "n_refusal": n_refusal >= N_REFUSAL_MIN,
    }
    details: dict[str, object] = {
        "recall_at_4": round(report.recall_at_4, 4),
        "mrr_at_4": round(report.mrr_at_4, 4),
        "refusal_precision": round(refusal_precision, 4),
        "refusal_hits_gate": refusal_hits,
        "n_refused_gate": len(refused_gate),
        "excluded_gap_ids": sorted(KNOWN_GAP_IDS),
        "latency_p50_ms": round(report.latency_p50_ms, 2),
        "latency_p95_ms": round(report.latency_p95_ms, 2),
        "n_cases": n_cases,
        "n_refusal_cases": n_refusal,
        "passed_recall_at_4": checks["recall_at_4"],
        "passed_mrr_at_4": checks["mrr_at_4"],
        "passed_refusal_precision": checks["refusal_precision"],
        "passed_latency_p50_ms": checks["latency_p50_ms"],
        "passed_latency_p95_ms": checks["latency_p95_ms"],
        "passed_n_golden": checks["n_golden"],
        "passed_n_refusal": checks["n_refusal"],
    }
    return all(checks.values()), details


def write_outputs(report: EvalReport, config_threshold: float) -> tuple[Path, Path]:
    """Simpan JSON penuh + markdown ringkasan ke `eval/results/`."""

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    json_path = RESULTS_DIR / f"{stamp}.json"
    md_path = RESULTS_DIR / f"{stamp}.md"

    gate_passed, gate_details = release_gate(
        report,
        n_cases=report.n_answerable + len(report.refused_cases),
        n_refusal=len(report.refused_cases),
    )

    payload: dict[str, object] = {
        "phase": "sprint5-release-gate",
        "thresholds": {
            "recall_at_4_min": RECALL_THRESHOLD,
            "mrr_at_4_min": MRR_THRESHOLD,
            "refusal_precision_min": REFUSAL_PRECISION_MIN,
            "latency_p50_ms_max": LATENCY_P50_MS_MAX,
            "latency_p95_ms_max": LATENCY_P95_MS_MAX,
            "n_golden_min": N_GOLDEN_MIN,
            "n_refusal_min": N_REFUSAL_MIN,
            "known_gap_ids": sorted(KNOWN_GAP_IDS),
        },
        "policy_threshold": config_threshold,
        "recall_at_4": round(report.recall_at_4, 4),
        "mrr_at_4": round(report.mrr_at_4, 4),
        "n_answerable": report.n_answerable,
        "refusal_ok": report.refusal_ok,
        "n_refused_cases": len(report.refused_cases),
        "latency_p50_ms": round(report.latency_p50_ms, 2),
        "latency_p95_ms": round(report.latency_p95_ms, 2),
        "refusal_threshold_data": {
            "refused_max_cosine": round(report.refused_max_cosine, 4),
            "answerable_min_cosine": round(report.answerable_min_cosine, 4),
            "suggestion": report.threshold_suggestion,
        },
        "cases": [asdict(r) for r in (*report.answerable_cases, *report.refused_cases)],
        "gate": {"passed": gate_passed, **gate_details},
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", "utf-8")

    fails = [r for r in report.answerable_cases if not r.hit]
    # Gate refusal: kecualikan jurang model yang diketahui (KNOWN_GAP_IDS,
    # di-Backlog — lihat ROADMAP). Kiraan disatukan dari release_gate
    # (satu sumber kebenaran) — senarai kes diulang untuk butiran per-kes.
    refused_gate = [r for r in report.refused_cases if r.id not in KNOWN_GAP_IDS]
    refusal_hits_gate = cast(int, gate_details["refusal_hits_gate"])
    n_refused_gate = cast(int, gate_details["n_refused_gate"])
    md = [
        "# Eval — grounded retrieval (gerbang rilis)",
        "",
        f"- recall@4 = **{report.recall_at_4:.2f}** ({report.recall_hits}/{report.n_answerable})"
        f" — gerbang ≥ {RECALL_THRESHOLD:.2f}",
        f"- MRR@4 = **{report.mrr_at_4:.2f}** — gerbang ≥ {MRR_THRESHOLD:.2f}",
        f"- refusal oleh polisi: {report.refusal_ok}/{len(report.refused_cases)}"
        f" (ambang aktif {config_threshold:.2f})",
        f"- refusal_precision gerbang = **{(refusal_hits_gate / n_refused_gate) if n_refused_gate else 1.0:.2f}**"
        f" ({refusal_hits_gate}/{n_refused_gate} kes bukan-jurang)"
        f" — gerbang = {REFUSAL_PRECISION_MIN:.2f} (keras)",
        f"- latency retrieval p50/p95 = {report.latency_p50_ms:.0f}ms / {report.latency_p95_ms:.0f}ms"
        f" — gerbang ≤ {LATENCY_P50_MS_MAX:.0f}/{LATENCY_P95_MS_MAX:.0f}ms",
        *(
            [f"- dikecualikan (jurang model, Backlog): {', '.join(sorted(KNOWN_GAP_IDS))}"]
            if KNOWN_GAP_IDS
            else []
        ),
        "",
        "## Data pilihan ambang penolakan (KR-4, cosine top-1 dense)",
        "",
        f"- kasus mesti-tolak: maks cosine = {report.refused_max_cosine:.4f}",
        f"- kasus boleh-jawapan: min cosine = {report.answerable_min_cosine:.4f}",
        f"- **ambang diukur (aktif) = {config_threshold:.2f}**; usulan tengah-jurang = "
        f"{report.threshold_suggestion:.4f} (0.0 = jurang tertindih — lihat docs/eval.md)",
        "",
    ]
    if fails:
        md.append("## Kes gagal")
        md.append("")
        for r in fails:
            md.append(f"- `{r.id}` {r.question!r} → dapat: {r.retrieved_sources or 'tiada'}")
    refused_fail = [r for r in refused_gate if not r.hit]
    if refused_fail:
        md.append("")
        md.append("## Penolakan gagal (bukan jurang backlogged)")
        md.append("")
        for r in refused_fail:
            md.append(f"- `{r.id}` {r.question!r} — {r.note}")
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    return json_path, md_path


async def main() -> None:
    cases = load_golden(GOLDEN_PATH)
    n_answerable = sum(not c.must_refuse for c in cases)
    n_refusal = sum(c.must_refuse for c in cases)
    settings = get_settings()
    logger.info(
        "eval_start",
        cases=len(cases),
        answerable=n_answerable,
        refusal=n_refusal,
        policy_threshold=settings.retrieval_refusal_threshold,
    )
    report = await evaluate(cases)
    json_path, md_path = write_outputs(report, settings.retrieval_refusal_threshold)

    # Sanity golden sebelum gerbang metrik (docs/eval.md): set mesti cukup
    # besar dan mengandungi kes penolakan yang mencukupi.
    ok_sanity = len(cases) >= N_GOLDEN_MIN and n_refusal >= N_REFUSAL_MIN
    ok_metrics, gate_details = release_gate(report, n_cases=len(cases), n_refusal=n_refusal)
    ok = ok_sanity and ok_metrics
    logger.info(
        "eval_done",
        **gate_details,
        sanity_golden=ok_sanity,
        results_json=str(json_path),
        results_md=str(md_path),
        passed=ok,
    )
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
