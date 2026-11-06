"""Unit: release_gate() — gerbang rilis penuh run_eval (docs/eval.md).

Fungsi pure; tanpa Docker/DB — hanya dataclass report yang dibina manual.
"""

import pytest

from eval.run_eval import (
    N_REFUSAL_MIN,
    CaseResult,
    EvalReport,
    release_gate,
)

pytestmark = pytest.mark.unit

N_ANSWERABLE = 17  # padankan golden v1 (20 kes = 17 answerable + 3 refusal)


def _hit(rank: int = 1) -> CaseResult:
    """Kes boleh-jawapan berjaya (rank di dalam top-4)."""

    return CaseResult(
        id="g001", question="q", hit=True, rank=rank, retrieved_sources=("a.pdf",), latency_ms=0.0
    )


def _refuse(id_str: str, *, hit: bool) -> CaseResult:
    """Kes mesti-tolak; `hit` bermaksud ditolak oleh polisi (baik)."""

    return CaseResult(
        id=id_str, question="q", hit=hit, rank=0, retrieved_sources=(), latency_ms=0.0
    )


def _report(
    answerable: tuple[CaseResult, ...],
    refused: tuple[CaseResult, ...],
    *,
    recall: float | None = None,
    mrr: float | None = None,
    p50: float = 10.0,
    p95: float = 20.0,
) -> EvalReport:
    """Bina EvalReport minimal; recall/mrr eksplisit bila diberi nilai."""

    n = len(answerable)
    auto_recall = sum(r.hit for r in answerable) / n if n else 0.0
    auto_ranks = sorted(r.rank for r in answerable if r.hit)
    auto_mrr = sum(1.0 / rank for rank in auto_ranks) / n if n else 0.0
    return EvalReport(
        recall_at_4=auto_recall if recall is None else recall,
        mrr_at_4=auto_mrr if mrr is None else mrr,
        recall_hits=sum(r.hit for r in answerable),
        n_answerable=n,
        answerable_cases=answerable,
        refused_cases=refused,
        refusal_ok=sum(r.hit for r in refused),
        latency_p50_ms=p50,
        latency_p95_ms=p95,
        refused_max_cosine=0.0,
        answerable_min_cosine=0.0,
        threshold_suggestion=0.0,
    )


# Tiga kes mesti-tolak — SEMUA hit pasca-Sprint 6: g011 ditolak deterministik
# oleh guard keadaan-peribadi sebelum LLM; KNOWN_GAP_IDS kini kosong.
_REFUSED_GATE: tuple[CaseResult, ...] = (
    _refuse("g012", hit=True),
    _refuse("g013", hit=True),
    _refuse("g011", hit=True),
)
_CASES_MIN: int = len(_REFUSED_GATE)


def test_lulus_penuh() -> None:
    report = _report(tuple(_hit() for _ in range(N_ANSWERABLE)), _REFUSED_GATE)
    ok, details = release_gate(report, n_cases=20, n_refusal=3)
    assert ok is True
    assert all(
        details[f"passed_{k}"] is True
        for k in (
            "recall_at_4",
            "mrr_at_4",
            "refusal_precision",
            "latency_p50_ms",
            "latency_p95_ms",
            "n_golden",
            "n_refusal",
        )
    )
    assert details["n_cases"] == 20
    assert details["n_refused_gate"] == 3  # tiada lagi dikecualikan — jurang g011 selesai
    assert details["refusal_precision"] == 1.0


def test_gagal_recall_di_bawah_ambang() -> None:
    report = _report(tuple(_hit() for _ in range(N_ANSWERABLE)), _REFUSED_GATE, recall=0.79)
    ok, details = release_gate(report, n_cases=20, n_refusal=3)
    assert ok is False
    assert details["passed_recall_at_4"] is False


def test_gagal_mrr_di_bawah_ambang() -> None:
    report = _report(tuple(_hit(rank=4) for _ in range(N_ANSWERABLE)), _REFUSED_GATE, mrr=0.64)
    ok, details = release_gate(report, n_cases=20, n_refusal=3)
    assert ok is False
    assert details["passed_mrr_at_4"] is False


def test_refusal_precision_gagal_bila_kes_bukan_gap_tak_hit() -> None:
    # g020 BUKAN jurang diketahui dan tidak ditolak polisi → precision < 1.0.
    report = _report(
        tuple(_hit() for _ in range(N_ANSWERABLE)),
        (_refuse("g012", hit=True), _refuse("g013", hit=False), _refuse("g020", hit=True)),
    )
    ok, details = release_gate(report, n_cases=20, n_refusal=3)
    assert ok is False
    assert details["passed_refusal_precision"] is False
    assert details["refusal_precision"] == pytest.approx(round(2 / 3, 4))  # 0.6667 (4 d.p.)


def test_semua_refusal_gagal_precision_nol() -> None:
    report = _report(
        tuple(_hit() for _ in range(N_ANSWERABLE)),
        (_refuse("g012", hit=False), _refuse("g013", hit=False), _refuse("g020", hit=False)),
    )
    ok, details = release_gate(report, n_cases=20, n_refusal=3)
    assert ok is False
    assert details["passed_refusal_precision"] is False
    assert details["refusal_precision"] == 0.0


def test_gate_kosong_semua_kes_dikecualikan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Semua 3 kes mesti-tolak berada dalam KNOWN_GAP_IDS → gate kosong.

    Precision = 1.0 (vacuous) tetapi keadaan itu dijaga oleh cek
    n_refusal >= N_REFUSAL_MIN yang berasingan — bukan jalan gelap.
    """

    monkeypatch.setattr("eval.run_eval.KNOWN_GAP_IDS", frozenset({"g012", "g013", "g020"}))
    report = _report(
        tuple(_hit() for _ in range(N_ANSWERABLE)),
        (_refuse("g012", hit=False), _refuse("g013", hit=False), _refuse("g020", hit=False)),
    )
    ok, details = release_gate(report, n_cases=20, n_refusal=3)
    assert ok is True
    assert details["n_refused_gate"] == 0
    assert details["refusal_precision"] == 1.0


def test_latency_di_atas_ambang_gagal() -> None:
    report = _report(
        tuple(_hit() for _ in range(N_ANSWERABLE)), _REFUSED_GATE, p50=3001.0, p95=8000.0
    )
    ok, details = release_gate(report, n_cases=20, n_refusal=3)
    assert ok is False
    assert details["passed_latency_p50_ms"] is False


def test_latency_sempadanan_lulus() -> None:
    report = _report(
        tuple(_hit() for _ in range(N_ANSWERABLE)), _REFUSED_GATE, p50=3000.0, p95=8000.0
    )
    ok, details = release_gate(report, n_cases=20, n_refusal=3)
    assert ok is True
    assert details["passed_latency_p50_ms"] is True
    assert details["passed_latency_p95_ms"] is True


def test_sanity_n_refusal_kurang_dari_minimum_gagal() -> None:
    # Walaupun semua lain lulus, 2 kes mesti-tolak < N_REFUSAL_MIN → gagal.
    report = _report(
        tuple(_hit() for _ in range(N_ANSWERABLE)),
        (_refuse("g012", hit=True), _refuse("g013", hit=True)),
    )
    ok, details = release_gate(report, n_cases=N_ANSWERABLE + 2, n_refusal=2)
    assert ok is False
    assert details["passed_n_refusal"] is False


def test_sanity_n_kes_kurang_dari_minimum_gagal() -> None:
    short = (_hit() for _ in range(N_ANSWERABLE - 1))
    report = _report(tuple(short), _REFUSED_GATE)
    ok, details = release_gate(report, n_cases=N_ANSWERABLE - 1 + _CASES_MIN, n_refusal=_CASES_MIN)
    assert ok is False
    assert details["passed_n_golden"] is False
    # N_REFUSAL_MIN kekal 3 — dijaga oleh cek gerbang yang berasingan (diuji atas).
    assert N_REFUSAL_MIN == 3
