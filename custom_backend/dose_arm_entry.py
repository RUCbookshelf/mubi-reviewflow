"""Persist arm-derived dose contrasts with their original inputs and sources."""
from typing import Literal

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field


class DoseArmsIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    study_id: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    timepoint: str = Field(min_length=1)
    measure: Literal['RR','OR','MD','RATE_RATIO']
    reference_dose: float = Field(ge=0, allow_inf_nan=False)
    dose_unit: str = Field(min_length=1)
    independent_arms: Literal['confirmed']
    arms: list[dict] = Field(min_length=3,max_length=101)
    source_key: str = Field(min_length=1)
    source_locator: str = Field(min_length=1)
    person_time_unit: str | None = None


def register_dose_arm_routes(app, require_user, task_or_404, screener_db, bad):
    @app.post('/api/tasks/{task_id}/analysis/dose/arm-curves')
    def save(task_id: str, body: DoseArmsIn, user: dict = Depends(require_user)):
        from coscreen.dose_arm_covariance import arm_summaries_to_dose_curve
        from coscreen.dose_analysis import save_curve, list_curves
        path = screener_db(task_or_404(task_id), user['username'])
        try:
            rate = body.measure == 'RATE_RATIO'
            if rate:
                if not body.person_time_unit or not body.person_time_unit.strip():
                    raise ValueError('Rate ratios require a declared common person-time unit.')
                if any('person_time' not in arm or 'total' in arm for arm in body.arms):
                    raise ValueError('Rate ratios require person_time rather than total in every arm.')
            elif body.person_time_unit is not None or any('person_time' in arm for arm in body.arms):
                raise ValueError('Select RATE_RATIO explicitly for person-time data.')
            result = arm_summaries_to_dose_curve(body.study_id,body.arms,
                'RR' if rate else body.measure,body.dose_unit,body.reference_dose)
            # The pure builder uses RR for both risk and rate inputs; persist distinct estimands.
            result['measure'] = body.measure
            sources = ['Greenland & Longnecker (1992); Orsini et al. (2012), corrected covariance formulas. Verify the original papers and cite the study reports.',
                       'For MD: Crippa & Orsini (2016), equations 1–3, pooled-SD diagonal and shared-reference off-diagonal.']
            audit = {'arms':result['input_data'],'source_locator':body.source_locator,
                     'reference_dose':body.reference_dose,'dose_unit':body.dose_unit,
                     'measure':body.measure,'person_time_unit':body.person_time_unit,
                     'independent_arms':body.independent_arms,'covariance_scale':result['covariance_scale'],
                     'entry_method':'arm_summaries_to_dose_curve','method_sources':sources}
            save_curve(path,body.study_id,body.outcome,body.timepoint,body.measure,
                result['reference_dose'],result['dose_unit'],result['contrasts'],result['covariance'],
                body.source_key,input_data=audit)
            return {'calculation':result,'input_data':audit,'curves':list_curves(path)}
        except ValueError as exc:
            raise bad(exc) from exc
