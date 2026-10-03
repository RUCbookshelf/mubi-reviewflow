"""Read-only exploratory pooling of saved diagnostic tables."""
from typing import Literal

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field


class ExploratoryLrIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    index_test: str = Field(min_length=1)
    target_condition: str = Field(min_length=1)
    threshold: str = Field(min_length=1)
    reference_standard: str = Field(min_length=1)
    model: Literal['fixed', 'dl']
    correction: Literal['none', 'half']
    confidence: float = Field(gt=0, lt=1, allow_inf_nan=False)


def register_dta_exploratory_lr_routes(app, require_user, task_or_404, screener_db, bad):
    @app.post('/api/tasks/{task_id}/analysis/dta/exploratory-likelihood-ratios')
    def calculate(task_id: str, body: ExploratoryLrIn, user: dict = Depends(require_user),
                  format: Literal['docx','pptx','csv','tex'] | None = None):
        from coscreen.dta_analysis import list_results
        from coscreen.dta_likelihood_ratios import pool_likelihood_ratios
        path = screener_db(task_or_404(task_id), user['username'])
        try:
            rows = [row for row in list_results(path) if all(row[key] == getattr(body, key)
                    for key in ('index_test','target_condition','threshold','reference_standard'))]
            if len({row['study_id'] for row in rows}) != len(rows):
                raise ValueError('Select one independent diagnostic table per study.')
            tables = [{key:row[key] for key in ('study_id','tp','fp','fn','tn')} for row in rows]
            sources = [{key:row[key] for key in ('study_id','source_key','source_locator','threshold','reference_standard')} for row in rows]
            result = pool_likelihood_ratios(tables, correction='none' if body.correction == 'none' else .5,
                random_effects=body.model == 'dl', confidence=body.confidence, source_provenance=sources)
            result = {**result, 'request':body.model_dump(), 'method_sources':[
                'Irwig et al. (1995), Meta-analytic methods for diagnostic test accuracy, J Clin Epidemiol 48:119–130. Verify the original methods before citation.',
                'Zwinderman & Bossuyt (2008), We should not pool diagnostic likelihood ratios in systematic reviews, Stat Med 27:687–697. Separate pooling ignores LR correlation; use only for exploratory comparison.',
                'Cochrane Handbook for Systematic Reviews of Diagnostic Test Accuracy, chapter 10, section 10.4.2: prefer likelihood ratios derived at the bivariate summary point.',
            ]}
            if format:
                from custom_backend.analysis_audit_report import audit_response
                summary = dict(title='Exploratory likelihood ratios',
                    columns=['Ratio','Estimate',f'{body.confidence * 100:g}% interval',
                             'Q (fixed-IV; df)','I² (%)','DL τ² (log LR variance scale)'],
                    rows=[[label,result['lr_'+key],result[key]['ci_lr'],
                           f"{result[key]['heterogeneity']['q']} ({result[key]['heterogeneity']['q_df']})",
                           result[key]['heterogeneity']['i2_percent'],result[key]['heterogeneity']['tau2']]
                          for key,label in [('positive','LR+'),('negative','LR−')]],
                    note=f"Model={body.model}; zero-cell policy={body.correction}; confidence={body.confidence}. Separate LR pooling ignores their correlation. Full 2×2 tables, exclusions, parameters and citations follow in the audit appendix.")
                from custom_backend.source_provenance import source_articles
                return audit_response({**result,'source_tables':rows,
                                       'source_articles':source_articles(path, rows)},format,
                                      'Exploratory diagnostic LR audit',tables=[summary])
            return result
        except ValueError as exc:
            raise bad(exc) from exc
