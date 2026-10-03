"""Read-only robust-prior Bayesian synthesis and explicit prior-grid comparison."""
from typing import Literal

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field


class PriorSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mu_prior_family: Literal['normal','student_t']
    mu_prior_mean: float = Field(allow_inf_nan=False)
    mu_prior_scale: float = Field(gt=0,allow_inf_nan=False)
    mu_prior_df: float | None = Field(gt=0,allow_inf_nan=False)
    tau_prior_family: Literal['half_normal','half_student_t']
    tau_prior_scale: float = Field(gt=0,allow_inf_nan=False)
    tau_prior_df: float | None = Field(gt=0,allow_inf_nan=False)


class RobustScope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    comparison: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    timepoint: str = Field(min_length=1)
    measure: Literal['RR','OR','HR','RATE_RATIO','RD','MD','SMD','FISHER_Z']


class RobustFitIn(RobustScope):
    prior: PriorSpec


class RobustGridIn(RobustScope):
    priors: list[PriorSpec] = Field(min_length=1)


METHOD_SOURCES = [
    'Grant & Di Tanna (2025), Bayesian Meta-Analysis, sections 4.2.1 and 4.4.5 (half-distribution priors on tau), 12.2.2 (t scale differs from SD), 3.7.6 and 4.8.5 (descriptive sensitivity). Check the original book before citing.',
    'An explicit Student-t prior on the pooled effect was not verified in that book; this implementation uses the normal scale-mixture identity and independent numerical calibration. It does not use a Student-t study likelihood or perform publication-bias model averaging.',
]


def register_bayesian_robust_routes(app, require_user, task_or_404, screener_db, bad):
    def calculate(task_id, body, user, grid, format):
        from coscreen.review_analysis import selected_effects
        from coscreen.bayesian_robust_meta import fit_bayesian_robust_meta, prior_sensitivity_grid
        from custom_backend.source_provenance import source_articles
        path = screener_db(task_or_404(task_id),user['username'])
        try:
            rows = selected_effects(path,body.comparison,body.outcome,body.timepoint,body.measure)
            result = (prior_sensitivity_grid(rows,priors=[prior.model_dump() for prior in body.priors])
                      if grid else fit_bayesian_robust_meta(rows,**body.prior.model_dump()))
            if body.measure == 'FISHER_Z':
                result['analysis_scale'] = result['standard_error_scale'] = 'fisher_z'
                for cell in result['cells'] if grid else [result]:
                    cell['mu']['scale'] = 'fisher_z'
                    cell['tau']['scale'] = 'fisher_z_sd'
                if grid:
                    result['influence_overview'] = result['influence_overview'].replace('on the natural scale', 'on the Fisher z scale')
            report = {**result,'request':body.model_dump(),'method_sources':METHOD_SOURCES,
                    'sources':[{key:row[key] for key in ('study_id','source_key','source_locator','result_id','estimate','se')} for row in rows],
                    'source_articles':source_articles(path, rows)}
            if format:
                from custom_backend.analysis_audit_report import audit_response
                return audit_response(report, format, 'Bayesian prior comparison audit')
            return report
        except ValueError as exc:
            raise bad(exc) from exc

    @app.post('/api/tasks/{task_id}/analysis/bayesian/robust-meta')
    def fit(task_id: str, body: RobustFitIn, user: dict = Depends(require_user), format: Literal['docx','pptx','csv','tex'] | None = None):
        return calculate(task_id,body,user,False,format)

    @app.post('/api/tasks/{task_id}/analysis/bayesian/prior-grid')
    def grid(task_id: str, body: RobustGridIn, user: dict = Depends(require_user), format: Literal['docx','pptx','csv','tex'] | None = None):
        return calculate(task_id,body,user,True,format)
