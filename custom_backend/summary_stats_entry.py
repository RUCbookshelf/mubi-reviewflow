"""Convert explicitly chosen arm summaries, preserving imputation provenance."""
from typing import Annotated, Literal

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field


class _Arm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    n: int = Field(strict=True, ge=2)


class ReportedArm(_Arm):
    format: Literal['reported']
    mean: float = Field(allow_inf_nan=False)
    sd: float = Field(ge=0, allow_inf_nan=False)


class RangeArm(_Arm):
    format: Literal['range']
    method: Literal['hozo_2005', 'wan_2014', 'luo_2018']
    median: float = Field(allow_inf_nan=False)
    min_val: float = Field(allow_inf_nan=False)
    max_val: float = Field(allow_inf_nan=False)


class IqrArm(_Arm):
    format: Literal['iqr']
    method: Literal['wan_2014', 'luo_2018']
    median: float = Field(allow_inf_nan=False)
    q1: float = Field(allow_inf_nan=False)
    q3: float = Field(allow_inf_nan=False)


Arm = Annotated[ReportedArm | RangeArm | IqrArm, Field(discriminator='format')]


class SummaryStatsIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    study_id: str
    comparison: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    timepoint: str = Field(min_length=1)
    source_key: str
    source_locator: str = Field(min_length=1)
    append: bool = False
    measure: Literal['MD', 'SMD']
    direction: Literal['treatment_minus_control']
    effect_direction: Literal['first_vs_second', 'second_vs_first']
    treatment: Arm
    control: Arm


def register_summary_stats_routes(app, require_user, task_or_404, screener_db, bad):
    @app.post('/api/tasks/{task_id}/analysis/estimated-summary/effects')
    def save_summary(task_id: str, body: SummaryStatsIn, user: dict = Depends(require_user)):
        from coscreen.summary_stats_estimation import estimate_from_median_range, estimate_from_median_iqr
        from coscreen.review_analysis import continuous_effect, save_effect, list_effects
        path = screener_db(task_or_404(task_id), user['username'])
        try:
            if body.treatment.format == body.control.format == 'reported':
                raise ValueError('Use ordinary arm entry when both arms report observed means and SDs.')
            arms, audit = {}, {}
            for name, arm in [('treatment', body.treatment), ('control', body.control)]:
                values = arm.model_dump(exclude={'format'})
                if arm.format == 'reported':
                    result = {'mean_estimate':arm.mean, 'sd_estimate':arm.sd, 'n':arm.n,
                              'input_data':values, 'warnings':[], 'method':'reported'}
                else:
                    calculate = estimate_from_median_range if arm.format == 'range' else estimate_from_median_iqr
                    result = calculate(**values, source_provenance={'source_key':body.source_key, 'source_locator':body.source_locator, 'arm':name})
                audit[name] = {**result, 'estimated':arm.format != 'reported', 'format':arm.format}
                suffix = 't' if name == 'treatment' else 'c'
                arms.update({f'n_{suffix}':arm.n, f'mean_{suffix}':result['mean_estimate'], f'sd_{suffix}':result['sd_estimate']})
            effect = continuous_effect(**arms, measure=body.measure)
            warnings = ['Formula-estimated means/SDs are not observed data. The usual effect SE omits reconstruction uncertainty; compare analyses with and without these studies.']
            warnings.extend(w for arm in audit.values() for w in arm['warnings'])
            references = {
                'hozo_2005':'Hozo, Djulbegovic & Hozo (2005), DOI 10.1186/1471-2288-5-13, formulas 5/16 and Table 3.',
                'wan_2014':'Wan et al. (2014), DOI 10.1186/1471-2288-14-135, range/IQR mean and SD formulas and Tables 1–2.',
                'luo_2018':'Luo et al. (2018), DOI 10.1177/0962280216669183, mean estimator; SD uses Wan et al. (2014).',
            }
            methods = {arm.method for arm in (body.treatment, body.control) if arm.format != 'reported'}
            if 'luo_2018' in methods:
                methods.add('wan_2014')
            sources = [references[method] for method in sorted(methods)]
            inputs = {**arms, 'estimated_summary':True, 'arm_estimation':audit,
                      'direction':body.direction, 'effect_direction':body.effect_direction,
                      'warnings':warnings, 'method_sources':sources,
                      'standardizer':'pooled_within_group_sd' if body.measure == 'SMD' else 'none'}
            result_id = save_effect(path, body.study_id, body.comparison, body.outcome, body.timepoint,
                body.measure, effect['estimate'], effect['se'], body.source_key, inputs,
                'estimated_summary_arms', append=body.append, source_locator=body.source_locator)
            return {'result_id':result_id, 'effect':effect, 'input_data':inputs, 'warnings':warnings,
                    'effects':list_effects(path)}
        except ValueError as exc:
            raise bad(exc) from exc
