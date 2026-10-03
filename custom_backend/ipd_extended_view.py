"""Read-only adapters for extended Cox models; participant rows are never exported."""
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field


class PrivateCoxRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validate_without_echo(request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                # Validation errors otherwise echo invalid participant input values.
                return JSONResponse(status_code=422, content={'detail': [
                    {key: error[key] for key in ('loc', 'msg', 'type')} for error in exc.errors()
                ]})
        return validate_without_echo


class ExtendedCoxIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    participants: list[dict[str, object]] = Field(min_length=4, max_length=100000)
    study_sources: dict[str, str] = Field(min_length=1)
    ties: Literal['efron', 'breslow']


class MultiCoxIn(ExtendedCoxIn):
    covariate_centers: dict[str, float]
    covariate_scales: dict[str, float]


class TwoStageCoxIn(ExtendedCoxIn):
    tau2_method: Literal['DL', 'REML']


METHOD_SOURCES = [
    'Cox (1972), Regression Models and Life-Tables. DOI: 10.1111/j.2517-6161.1972.tb00899.x.',
    'Borenstein et al. (2021), Introduction to Meta-Analysis, 2nd ed., Chapter 39, pp. 351–355: conceptual IPD framing, not numerical formulas. Check the original book and cite the methods used.',
]


def register_ipd_extended_routes(app, require_user, task_or_404, screener_db, bad):
    router = APIRouter(route_class=PrivateCoxRoute)

    def calculate(task_id, body, user, two_stage, format):
        from coscreen.review_analysis import list_studies
        from coscreen.ipd_cox_extended import fit_adjusted_stratified_cox_multi, fit_two_stage_random_slopes
        path = screener_db(task_or_404(task_id), user['username'])
        linked = {row['id']: set(row['reports']) for row in list_studies(path)}
        if any(source not in linked.get(study, set()) for study, source in body.study_sources.items()):
            raise bad('each study source must be linked to its study in this task')
        try:
            parameters = body.model_dump(exclude={'participants', 'study_sources'})
            fit = fit_two_stage_random_slopes if two_stage else fit_adjusted_stratified_cox_multi
            result = fit(body.participants, body.study_sources, **parameters)
            sources = [*METHOD_SOURCES,
                       'Efron (1977), The Efficiency of Cox’s Likelihood Function for Censored Data.' if body.ties == 'efron'
                       else 'Breslow (1974), Covariance Analysis of Censored Survival Data.']
            if two_stage:
                sources += [
                    'DerSimonian & Laird (1986), Meta-analysis in clinical trials. DOI: 10.1016/0197-2456(86)90046-2.' if body.tau2_method == 'DL'
                    else 'Viechtbauer (2005), Bias and Efficiency of Meta-Analytic Variance Estimators in the Random-Effects Model.',
                    'Higgins, Thompson & Spiegelhalter (2009), A re-evaluation of random-effects meta-analysis. DOI: 10.1111/j.1467-985X.2008.00552.x.',
                ]
            report = {**result, 'parameters': parameters, 'method_sources': sources,
                    'participant_rows_saved': False,
                    'warnings': ['Proportional hazards and independent censoring are assumptions, not tested here. No delayed entry, competing risks or missing-data handling.'] +
                    (['Unadjusted two-stage estimates differ from adjusted common-effect Cox estimates. With few studies, normal intervals and heterogeneity estimates may be unreliable. Q and I² use fixed-effect weights, including under REML.'] if two_stage else [])}
            if format:
                from custom_backend.analysis_audit_report import audit_response
                from custom_backend.source_provenance import source_articles
                report['source_articles'] = source_articles(path, [
                    {'source_key': key} for key in body.study_sources.values()])
                return audit_response(report, format, 'IPD Cox analysis audit')
            return report
        except ValueError as exc:
            raise bad(exc) from exc

    @router.post('/api/tasks/{task_id}/analysis/ipd/survival/multi-adjusted-stratified-cox')
    def multi(task_id: str, body: MultiCoxIn, user: dict = Depends(require_user), format: Literal['docx','pptx','csv','tex'] | None = None):
        return calculate(task_id, body, user, False, format)

    @router.post('/api/tasks/{task_id}/analysis/ipd/survival/two-stage-random-slopes')
    def two_stage(task_id: str, body: TwoStageCoxIn, user: dict = Depends(require_user), format: Literal['docx','pptx','csv','tex'] | None = None):
        return calculate(task_id, body, user, True, format)

    app.include_router(router)
