"""Read-only study intervals; no effect-store or synthesis dependency."""

import math
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field


class StudyIntervalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["proportion", "rate"]
    events: int = Field(strict=True, ge=0, le=2**53 - 1)
    method: str = Field(min_length=1, max_length=40)
    level: float = Field(strict=True, gt=0, lt=1, allow_inf_nan=False)
    total: int | None = Field(default=None, strict=True, ge=1, le=2**53 - 1)
    person_time: float | None = Field(default=None, strict=True, gt=0, allow_inf_nan=False)
    time_unit: str = Field(default="", max_length=100)
    source_note: str = Field(default="", max_length=2000)


SOURCES = {
    "clopper_pearson": "Clopper & Pearson (1934), Biometrika 26:404–413. DOI: 10.1093/biomet/26.4.404",
    "wilson": "Wilson (1927), JASA 22:209–212. DOI: 10.1080/01621459.1927.10502953",
    "mid_p": "Berry & Armitage (1995), The Statistician 44:417–431. DOI: 10.2307/2988638",
    "jeffreys": "Jeffreys (1946), DOI: 10.1098/rspa.1946.0056; Brown, Cai & DasGupta (2001), DOI: 10.1214/aos/1013203451. Unmodified equal-tailed posterior interval.",
    "exact": "Garwood (1936), Biometrika 28:437–442. DOI: 10.1093/biomet/28.3-4.437",
    "byar": "Breslow & Day (1987), Statistical Methods in Cancer Research, Volume II, IARC Scientific Publications No. 82. Byar approximation; original Byar derivation was not formally published.",
}


class RiskDifferenceIntervalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events_t: int = Field(strict=True, ge=0, le=2**53 - 1)
    n_t: int = Field(strict=True, ge=1, le=2**53 - 1)
    events_c: int = Field(strict=True, ge=0, le=2**53 - 1)
    n_c: int = Field(strict=True, ge=1, le=2**53 - 1)
    method: Literal["wald", "newcombe_hybrid_score"]
    level: float = Field(strict=True, gt=0, lt=1, allow_inf_nan=False)
    source_note: str = Field(default="", max_length=2000)


class PairedIntervalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a: int = Field(strict=True, ge=0, le=2**53 - 1)
    b: int = Field(strict=True, ge=0, le=2**53 - 1)
    c: int = Field(strict=True, ge=0, le=2**53 - 1)
    d: int = Field(strict=True, ge=0, le=2**53 - 1)
    method: Literal["wald_bonett_price", "newcombe_square_and_add", "wald"]
    level: float = Field(strict=True, gt=0, lt=1, allow_inf_nan=False)
    source_note: str = Field(default="", max_length=2000)


class RateRatioIntervalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events_t: int = Field(strict=True, ge=0, le=2**53 - 1)
    person_time_t: float = Field(strict=True, gt=0, allow_inf_nan=False)
    events_c: int = Field(strict=True, ge=0, le=2**53 - 1)
    person_time_c: float = Field(strict=True, gt=0, allow_inf_nan=False)
    time_unit: str = Field(min_length=1, max_length=100)
    method: Literal["exact", "mid_p"]
    level: float = Field(strict=True, gt=0, lt=1, allow_inf_nan=False)
    source_note: str = Field(default="", max_length=2000)


def register_study_interval_routes(app, require_user, task_or_404):
    @app.post("/api/tasks/{task_id}/analysis/study-interval/rate-ratio")
    def rate_ratio(task_id: str, body: RateRatioIntervalIn, user: dict = Depends(require_user)):
        task_or_404(task_id)
        from coscreen.rate_ratio_exact_interval import rate_ratio_interval

        try:
            if not body.time_unit.strip():
                raise ValueError("Specify the common person-time unit for both arms.")
            ratio = body.person_time_c / body.person_time_t
            if not math.isfinite(ratio) or ratio <= 0:
                raise ValueError("Exposure ratio exceeds the finite numeric range.")
            for events, exposure in ((body.events_t, body.person_time_t), (body.events_c, body.person_time_c)):
                if events and (not math.isfinite(events / exposure) or events / exposure <= 0):
                    raise ValueError("An event rate exceeds the finite numeric range.")
            result = rate_ratio_interval(body.events_t, body.person_time_t, body.events_c, body.person_time_c,
                                         method=body.method, level=body.level)
            if body.events_c and (not math.isfinite(result['ci'][1]) or not math.isfinite(result['irr'])):
                raise ValueError("Upper limit exceeds the numeric range; this is not a zero-event boundary.")
            if body.events_t and (result['ci'][0] <= 0 or result['irr'] <= 0):
                raise ValueError("Lower limit exceeds the numeric range; this is not a zero-event boundary.")
            # JSON has no infinity literal: preserve the mathematical boundary as an explicit string.
            def bound(value):
                if math.isnan(value):
                    raise ValueError("Interval is not numerically defined for these inputs.")
                return ("Infinity" if value > 0 else "-Infinity") if math.isinf(value) else value
            for key in ('irr', 'log_irr'):
                result[key] = bound(result[key])
            for key in ('ci', 'log_ci'):
                result[key] = [bound(value) for value in result[key]]
            references = ["Conditional binomial interval transformed to a Poisson rate ratio: Stata [R] epitab, Methods and formulas (https://www.stata.com/manuals/repitab.pdf); Clopper & Pearson (1934), DOI: 10.1093/biomet/26.4.404."]
            if body.method == 'mid_p':
                references.append(SOURCES['mid_p'])
            result.update(time_unit=body.time_unit.strip(), source_note=body.source_note.strip(),
                          use='study_description_only', method_sources=references,
                          infinity_encoding='Infinity and -Infinity strings denote mathematical unbounded limits, not missing values.')
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/tasks/{task_id}/analysis/study-interval/paired-risk-difference")
    def paired_difference(task_id: str, body: PairedIntervalIn, user: dict = Depends(require_user)):
        task_or_404(task_id)
        from coscreen.paired_rd_interval import paired_rd_interval

        try:
            result = paired_rd_interval(body.a, body.b, body.c, body.d, method=body.method, level=body.level)
            references = ["Fagerland, Lydersen & Laake (2017), Statistical Analysis of Contingency Tables, ch. 8, §§8.6.2–8.6.3; recommendations in Table 8.15, pp. 384–385. Check the original book."]
            if body.method == "wald_bonett_price":
                references.append("Bonett & Price (2012), Adjusted Wald confidence interval for a difference of binomial proportions based on paired data. DOI: 10.3102/1076998611411915.")
            elif body.method == "newcombe_square_and_add":
                references.append("Newcombe (1998), Improved confidence intervals for the difference between binomial proportions based on paired data. Statistics in Medicine 17(22):2635–2650; PMID 9839354.")
            result.update(source_note=body.source_note.strip(), method_sources=references,
                          use="study_description_only")
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/tasks/{task_id}/analysis/study-interval/risk-difference")
    def risk_difference(task_id: str, body: RiskDifferenceIntervalIn, user: dict = Depends(require_user)):
        task_or_404(task_id)
        from coscreen.two_group_rd_interval import risk_difference_interval

        try:
            result = risk_difference_interval(body.events_t, body.n_t, body.events_c, body.n_c,
                                              method=body.method, level=body.level)
            result.update(source_note=body.source_note.strip(), use="study_description_only",
                          method_sources=["Newcombe (1998), Interval estimation for the difference between independent proportions: comparison of eleven methods. Statistics in Medicine 17(8):873–890; PMID 9595617. Wald unpooled or method 10 hybrid score without continuity correction, as selected."])
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/tasks/{task_id}/analysis/study-interval")
    def study_interval(task_id: str, body: StudyIntervalIn, user: dict = Depends(require_user)):
        task_or_404(task_id)
        # Lazy import keeps an unavailable numerical module out of application startup.
        from coscreen.single_group_exact_ci import proportion_ci, rate_ci

        try:
            if body.kind == "proportion":
                if body.total is None or body.person_time is not None or body.time_unit.strip():
                    raise ValueError("A proportion requires total people and no person-time fields.")
                result = proportion_ci(body.events, body.total, body.method, body.level)
            else:
                if body.person_time is None or body.total is not None or not body.time_unit.strip():
                    raise ValueError("A rate requires person-time and its unit, with no total-people field.")
                result = rate_ci(body.events, body.person_time, body.method, body.level)
            if not all(math.isfinite(result[key]) for key in ("estimate", "lower", "upper")):
                raise ValueError("Interval exceeds the finite numeric range; check the exposure and confidence level.")
            result.update(time_unit=body.time_unit.strip(), source_note=body.source_note.strip(),
                          method_sources=[SOURCES[body.method]], use="study_description_only")
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
