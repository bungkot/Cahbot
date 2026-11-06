"""Runner eval Sprint 3 — metrik pengambilan semula (tanpa LLM).

Ukur terhadap golden set (`eval/golden/golden.jsonl`):
- recall@4 : kes boleh-jawapan dengan chunk dari sumber betul dalam top-4 fusi RRF;
- MRR@4    : 1/rank pertama chunk dari sumber betul dalam top-4;
- refusal  : kes `must_refuse` yang *fused* kosong (tiada konteks) — dasar
             ambang skor RRF tiba pada Sprint 4 (ROADMAP), jadi diharapkan
             sebagai nota, bukan ambang lepas-utama di sini.

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

import structlog

from app.application.context import fuse_rrf
from app.core.config import get_settings
from app.infrastructure.onnx_embedder import OnnxEmbedder
from app.infrastructure.postgres.repository import PgVectorRepository

logger = structlog.get_logger(__name__)

GOLDEN_PATH = Path("eval/golden/golden.jsonl")
RESULTS_DIR = Path("eval/results")
RECALL_THRESHOLD: float = 0.70  # DoD Sprint 3; naik ke 0.80 pada Sprint 5


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


@dataclass(frozen=True, slots=True)
class EvalReport:
    """Agregat metrik yang disimpan ke JSON/markdown."""

    recall_at_4: float
    mrr_at_4: float
    recall_hits: int
    n_answerable: int
    answerable_cases: tuple[CaseResult, ...]
    refused_cases: tuple[CaseResult, ...]
    refusal_no_context: int
    latency_p50_ms: float
    latency_p95_ms: float


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
) -> CaseResult:
    """Satu kes: embed → dense+sparse → jumlah RRF → ukur kedudukan top-4."""

    start = time.perf_counter()
    embedding = await embedder.embed_query(case.question)
    dense = await repo.search_dense(embedding, top_k)
    sparse = await repo.search_sparse(case.question, top_k)
    latency_ms = (time.perf_counter() - start) * 1000.0

    fused = fuse_rrf(dense, sparse, rrf_k)[:top_k]

    if case.must_refuse:
        hit = not fused
        note = "" if hit else "fused tidak kosong — dasar ambang skor bermula Sprint 4"
        return CaseResult(
            id=case.id,
            question=case.question,
            hit=hit,
            rank=0,
            retrieved_sources=(),
            latency_ms=latency_ms,
            note=note,
        )

    # Urut sources mengikut skor fusi (turun) untuk mengira rank MRR.
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
    )


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
            await _run_case(c, embedder, repo, top_k=settings.retriever_top_k, rrf_k=settings.rrf_k)
            for c in answerable
        ]
        results_ref = [
            await _run_case(c, embedder, repo, top_k=settings.retriever_top_k, rrf_k=settings.rrf_k)
            for c in refused
        ]
    finally:
        await repo.pool.close()

    n = len(results_ans)
    hits = sum(r.hit for r in results_ans)
    rr_sum = sum(1.0 / r.rank for r in results_ans if r.rank > 0)
    latencies = sorted(r.latency_ms for r in (*results_ans, *results_ref))

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
        refusal_no_context=sum(r.hit for r in results_ref),
        latency_p50_ms=_pct(latencies, 50),
        latency_p95_ms=_pct(latencies, 95),
    )


def write_outputs(report: EvalReport) -> tuple[Path, Path]:
    """Simpan JSON penuh + markdown ringkasan ke `eval/results/`."""

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    json_path = RESULTS_DIR / f"{stamp}.json"
    md_path = RESULTS_DIR / f"{stamp}.md"

    payload: dict[str, object] = {
        "phase": "sprint3-retrieval",
        "thresholds": {"recall_at_4": RECALL_THRESHOLD, "note": "Sprint 5: 0.80 / MRR 0.65"},
        "recall_at_4": round(report.recall_at_4, 4),
        "mrr_at_4": round(report.mrr_at_4, 4),
        "n_answerable": report.n_answerable,
        "refusal_no_context": report.refusal_no_context,
        "n_refused_cases": len(report.refused_cases),
        "latency_p50_ms": round(report.latency_p50_ms, 2),
        "latency_p95_ms": round(report.latency_p95_ms, 2),
        "cases": [asdict(r) for r in (*report.answerable_cases, *report.refused_cases)],
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", "utf-8")

    fails = [r for r in report.answerable_cases if not r.hit]
    md = [
        "# Eval Sprint 3 — retrieval-only",
        "",
        f"- recall@4 = **{report.recall_at_4:.2f}** ({report.recall_hits}/{report.n_answerable})"
        f" — sasaran sprint ≥ {RECALL_THRESHOLD:.2f}",
        f"- MRR@4 = **{report.mrr_at_4:.2f}** (sasaran penuh Sprint 5: ≥ 0.65)",
        f"- refusal tanpa konteks: {report.refusal_no_context}/{len(report.refused_cases)}"
        " (dasar ambang skor RRF tiba Sprint 4)",
        f"- latency retrieval p50/p95 = {report.latency_p50_ms:.0f}ms / {report.latency_p95_ms:.0f}ms",
        "",
    ]
    if fails:
        md.append("## Kes gagal")
        md.append("")
        for r in fails:
            md.append(f"- `{r.id}` {r.question!r} → dapat: {r.retrieved_sources or 'tiada'}")
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    return json_path, md_path


async def main() -> None:
    cases = load_golden(GOLDEN_PATH)
    n_answerable = sum(not c.must_refuse for c in cases)
    logger.info("eval_start", cases=len(cases), answerable=n_answerable)
    report = await evaluate(cases)
    json_path, md_path = write_outputs(report)

    ok = report.recall_at_4 >= RECALL_THRESHOLD
    logger.info(
        "eval_done",
        recall_at_4=round(report.recall_at_4, 3),
        mrr_at_4=round(report.mrr_at_4, 3),
        latency_p50_ms=round(report.latency_p50_ms, 1),
        latency_p95_ms=round(report.latency_p95_ms, 1),
        results=str(json_path),
        passed=ok,
    )
    logger.info(
        "eval_result",
        recall_at_4=round(report.recall_at_4, 3),
        mrr_at_4=round(report.mrr_at_4, 3),
        latency_p50_ms=round(report.latency_p50_ms, 1),
        latency_p95_ms=round(report.latency_p95_ms, 1),
        results_json=str(json_path),
        results_md=str(md_path),
        passed=ok,
    )
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
