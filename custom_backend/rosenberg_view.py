"""Source-linked Rosenberg sensitivity adapter; no effect records are written."""
from typing import Literal

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field


class RosenbergIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    comparison: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    timepoint: str = Field(min_length=1)
    measure: str
    test: Literal['z', 't']
    target_alpha: float = Field(strict=True, gt=0, lt=1, allow_inf_nan=False)


def register_rosenberg_routes(app, require_user, task_or_404, screener_db, bad):
    @app.post('/api/tasks/{task_id}/analysis/publication-bias/rosenberg')
    def rosenberg(task_id: str, body: RosenbergIn, user: dict = Depends(require_user),
                  format: Literal['docx','pptx','csv','tex'] | None = None):
        from coscreen.review_analysis import selected_effects
        from coscreen.publication_bias import _MEASURES, _RATIO_MEASURES, _validated_rows
        from coscreen.rosenberg_fail_safe import rosenberg_fail_safe

        path = screener_db(task_or_404(task_id), user['username'])
        try:
            if body.measure not in _MEASURES:
                raise ValueError('Choose a supported comparative effect or Fisher-z correlation for this diagnostic.')
            rows = selected_effects(path, body.comparison, body.outcome, body.timepoint, body.measure)
            effects, ses = _validated_rows(rows, body.measure)
            inputs = [{**row, 'effect': effect, 'se': se} for row, effect, se in zip(rows, effects, ses)]
            result = rosenberg_fail_safe(inputs, test=body.test, target_alpha=body.target_alpha,
                                        level=1-body.target_alpha, source_provenance=body.model_dump())
            result.update(measure=body.measure, effect_scale=(
                'log' if body.measure in _RATIO_MEASURES else 'Fisher z' if body.measure == 'FISHER_Z' else 'natural'))
            result['method_sources'] = [
                'Rosenberg (2005), Evolution 59:464–468, equations (3)–(4) and (7)–(10); verify the original paper.',
            ]
            if format:
                from custom_backend.analysis_audit_report import audit_response
                from custom_backend.source_provenance import source_articles
                table = dict(title='Rosenberg weighted fail-safe N',
                    columns=['Studies','Test','Target α','Observed p','Fail-safe N'],
                    rows=[[result['n_studies'],body.test,body.target_alpha,
                           result['observed_p_value'],result['fail_safe_n']]],
                    note='A sensitivity count under an assumed missing-study effect of zero; it does not estimate the number of unpublished studies. Full source effects, equations, test parameters and citations follow in the audit appendix.')
                return audit_response({**result,'source_articles':source_articles(path, rows)},
                                      format,'Rosenberg sensitivity audit',tables=[table])
            return result
        except ValueError as exc:
            raise bad(exc) from exc
