"""Read-only random subgroup adapter over selected, source-linked study effects."""
import math
from typing import Literal

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field


class SubgroupIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    comparison: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    timepoint: str = Field(min_length=1)
    measure: str
    dimension_id: int = Field(gt=0)
    tau2_policy: Literal['pooled', 'separate']


def register_subgroup_routes(app, require_user, task_or_404, screener_db, bad):
    @app.post('/api/tasks/{task_id}/analysis/subgroup-random')
    def subgroup(task_id: str, body: SubgroupIn, user: dict = Depends(require_user),
                 format: Literal['docx', 'pptx', 'csv', 'tex'] | None = None):
        from coscreen.review_analysis import selected_effects, RATIOS
        from coscreen.advanced_analysis import _characteristics
        from coscreen.measure_registry import get_measure
        from coscreen.subgroup_random import subgroup_random_analysis

        path = screener_db(task_or_404(task_id), user['username'])
        try:
            rows = selected_effects(path, body.comparison, body.outcome, body.timepoint, body.measure)
            labels = _characteristics(path, rows, body.dimension_id)
            inputs = [{**row, 'effect': math.log(row['estimate']) if body.measure in RATIOS else row['estimate'],
                       'subgroup': labels[row['study_id']]} for row in rows]
            result = subgroup_random_analysis(inputs, tau2_policy=body.tau2_policy, model='random', level=.95)
            result.update(request=body.model_dump(), measure=body.measure,
                          analysis_scale=get_measure(body.measure)['scale'],
                          method_sources=['Borenstein et al. (2021), Introduction to Meta-Analysis, 2nd ed., ch. 21, pp. 172–189; pooled tau-squared eq. 21.38, pairwise tests eqs. 21.45–21.49.',
                                          'Borenstein & Higgins (2013), Research Synthesis Methods 4:333–352, doi:10.1002/jrsm.1077'])
            if format:
                from custom_backend.analysis_audit_report import audit_response
                from custom_backend.source_provenance import source_articles
                table = {'title':'Random-effects subgroup estimates',
                         'columns':['Subgroup', 'Studies', 'Estimate', 'SE', '95% CI', 'Tau-squared'],
                         'rows':[[name, group['k'], group['pooled'], group['se'],
                                  f"{group['ci'][0]} to {group['ci'][1]}", group['tau2']]
                                 for name, group in result['subgroups'].items()],
                         'note':result['method'] + ' Verify source records and cite the methods used.'}
                return audit_response({**result, 'source_articles':source_articles(path, rows)},
                                      format, 'Random-effects subgroup audit', tables=[table])
            return result
        except ValueError as exc:
            raise bad(exc) from exc
