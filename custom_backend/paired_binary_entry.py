"""Persist one explicitly selected paired binary effect and its audit inputs."""
from typing import Literal

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field


class PairedBinaryIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    study_id: str
    comparison: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    timepoint: str = Field(min_length=1)
    source_key: str
    source_locator: str = ''
    append: bool = False
    measure: Literal['PAIRED_OR', 'PAIRED_RD']
    a: int = Field(strict=True, ge=0)
    b: int = Field(strict=True, ge=0)
    c: int = Field(strict=True, ge=0)
    d: int = Field(strict=True, ge=0)
    direction: Literal['treatment_minus_control']
    effect_direction: Literal['first_vs_second', 'second_vs_first']
    level: float = Field(gt=0, lt=1, allow_inf_nan=False)
    or_correction: Literal['none', 'add_half_discordant']


def register_paired_binary_routes(app, require_user, task_or_404, screener_db, bad):
    @app.post('/api/tasks/{task_id}/analysis/paired-binary/effects')
    def save_paired(task_id: str, body: PairedBinaryIn, user: dict = Depends(require_user)):
        from coscreen.paired_binary_effect import paired_binary_effect
        from coscreen.review_analysis import save_effect, list_effects
        path = screener_db(task_or_404(task_id), user['username'])
        try:
            result = paired_binary_effect({key:getattr(body, key) for key in 'abcd'},
                direction=body.direction, level=body.level,
                or_correction=None if body.or_correction == 'none' else body.or_correction,
                source_provenance={'source_key':body.source_key, 'source_locator':body.source_locator})
            estimate, se = ((result['log_estimate'], result['se']) if body.measure == 'PAIRED_OR'
                            else (result['risk_difference'], result['rd_se']))
            inputs = {**result['input_data'], 'selected_measure':body.measure,
                      'effect_direction':body.effect_direction,
                      'or_correction_applied':result['or_correction_applied'],
                      'rd_correction_applied':False, 'warnings':result['warnings']}
            result_id = save_effect(path, body.study_id, body.comparison, body.outcome, body.timepoint,
                body.measure, estimate, se, body.source_key, inputs, 'paired_binary_2x2',
                append=body.append, source_locator=body.source_locator)
            return {'result_id':result_id, 'measure':body.measure, 'estimate':estimate, 'se':se,
                    'calculation':result, 'effects':list_effects(path),
                    'method_sources':['Curtin, Elbourne & Altman (2002), Binary outcomes. DOI 10.1002/sim.1206: paired versus parallel estimands.',
                                      'Fagerland et al. (2014). DOI 10.1002/sim.6148: limitations of Wald intervals for paired proportions.']}
        except ValueError as exc:
            raise bad(exc) from exc
