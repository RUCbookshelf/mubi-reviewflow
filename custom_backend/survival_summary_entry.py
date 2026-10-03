"""Explicit median-survival conversion and guarded effect persistence."""
import math
from typing import Literal

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field


class SurvivalSummaryIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    conversion: Literal['hr', 'rate']
    exponential_assumption: Literal['acknowledged']
    event_sources: dict[str, Literal['reported', 'half_n', 'unavailable']]
    values: dict


class SurvivalSummarySaveIn(SurvivalSummaryIn):
    study_id: str
    comparison: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    timepoint: str = Field(min_length=1)
    source_key: str
    source_locator: str = Field(min_length=1)
    append: bool = False
    effect_direction: Literal['first_vs_second', 'second_vs_first'] | None = None


def _convert(body):
    from coscreen.survival_summary_conversion import calculate_hr_from_median_survival, calculate_rate_from_median
    values = dict(body.values)
    arms = ['treatment', 'control'] if body.conversion == 'hr' else ['single']
    if set(body.event_sources) != set(arms):
        raise ValueError('Choose an event-count source for every arm.')
    for arm in arms:
        suffix = '_' + arm if body.conversion == 'hr' else ''
        events, n = 'events'+suffix, 'n'+suffix
        policy = body.event_sources[arm]
        if policy == 'reported' and events not in values:
            raise ValueError('Reported event-count policy requires the event count.')
        if policy == 'half_n' and (n not in values or events in values):
            raise ValueError('Half-n approximation requires n and no reported event count.')
        if policy == 'unavailable' and (body.conversion == 'hr' or events in values or n in values):
            raise ValueError('Unavailable uncertainty is only valid for a rate with neither events nor n.')
        for key in (events, n):
            if key in values and (type(values[key]) is not int or values[key] <= 0):
                raise ValueError(f'{key} must be a positive integer count.')
    if not isinstance(values.get('time_unit'), str) or not values['time_unit'].strip():
        raise ValueError('Record a common time unit for the reported median(s).')
    calculate = calculate_hr_from_median_survival if body.conversion == 'hr' else calculate_rate_from_median
    result = calculate(values)
    result['event_sources'] = body.event_sources
    result['exponential_assumption'] = body.exponential_assumption
    result['inverse_variance_ready'] = result['se'] is not None
    result['method_sources'] = [
        'Spruance et al. (2004), DOI 10.1128/AAC.48.8.2787-2792.2004: interpretation of hazard and median ratios.',
        'Exponential model derivation: hazard = ln(2)/median; log-hazard variance uses the Poisson approximation 1/events. Verify these assumptions and cite the study reports.',
        'Tierney et al. (2007), DOI 10.1186/1745-6215-8-16 supports the O−E/V conversion, not conversion from median survival. Implied O−E/V here is reconstructed, not reported.',
    ]
    return result


def register_survival_summary_routes(app, require_user, task_or_404, screener_db, bad):
    @app.post('/api/tasks/{task_id}/analysis/survival-summary/convert')
    def convert(task_id: str, body: SurvivalSummaryIn, user: dict = Depends(require_user)):
        screener_db(task_or_404(task_id), user['username'])
        try:
            return _convert(body)
        except ValueError as exc:
            raise bad(exc) from exc

    @app.post('/api/tasks/{task_id}/analysis/survival-summary/effects')
    def save(task_id: str, body: SurvivalSummarySaveIn, user: dict = Depends(require_user)):
        from coscreen.review_analysis import save_effect, list_effects
        path = screener_db(task_or_404(task_id), user['username'])
        try:
            result = _convert(body)
            if not result['inverse_variance_ready']:
                raise ValueError('This conversion has no SE and is read-only; it cannot enter inverse-variance synthesis.')
            measure = 'HR' if body.conversion == 'hr' else 'LOG_RATE'
            if measure == 'HR' and body.effect_direction is None:
                raise ValueError('Choose how the effect direction maps to Comparison')
            if measure == 'LOG_RATE' and body.effect_direction is not None:
                raise ValueError('Effect direction is not applicable to LOG_RATE')
            estimate = result['estimate'] if measure == 'HR' else math.log(result['estimate'])
            inputs = {**result['input_data'], 'survival_summary_conversion':True,
                      'event_sources':body.event_sources, 'exponential_assumption':body.exponential_assumption,
                      'warnings':result['warnings'], 'assumptions':result['assumptions'],
                      'method_sources':result['method_sources']}
            if body.effect_direction:
                inputs['effect_direction'] = body.effect_direction
            result_id = save_effect(path, body.study_id, body.comparison, body.outcome, body.timepoint,
                measure, estimate, result['se'], body.source_key, inputs, result['entry_method'],
                append=body.append, source_locator=body.source_locator)
            return {'result_id':result_id, 'measure':measure, 'estimate':estimate, 'se':result['se'],
                    'calculation':result, 'effects':list_effects(path)}
        except ValueError as exc:
            raise bad(exc) from exc
