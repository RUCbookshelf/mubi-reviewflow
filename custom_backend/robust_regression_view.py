"""Adapt saved effects and coded moderators to study-level sandwich inference."""
import math
from collections.abc import Mapping

_MISSING = object()


def _is_mapping(value) -> bool:
    """键值容器判定：``dict``/``Mapping`` 之外也接受 ``sqlite3.Row`` 之类的
    行对象（有 ``keys`` 与下标取值的 DB-API 行，但**不是** ``Mapping``
    子类——基线对它们可用，不得因守卫而新增拒绝：R2）。"""
    return isinstance(value, Mapping) or callable(getattr(value, "keys", None))


def _mapping_get(row, key, default=_MISSING):
    if key in row.keys():
        return row[key]
    return default


def _row_value(row, key: str, what: str):
    """读外部行的一个字段；**缺失**（键不存在）时抛带说明的 ValueError。

    MM10-19：本视图的四个外部键值容器（rows/covariates/spec/references）此前
    无守卫下标，键缺失即 KeyError/TypeError 逃逸，而唯一调用方端点的外层 try
    只捕 ValueError（→ 400）。统一改为缺字段抛 ValueError，让缺口按 4xx 报
    告；键存在时值**原样透传**（含 None）。接受 dict/Mapping 与 sqlite3.Row
    之类的行对象（基线对后者可用，守卫不得新增拒绝：R2）。
    """
    if not _is_mapping(row):
        raise ValueError(f"{what} must be a mapping with a '{key}' field")
    value = _mapping_get(row, key)
    if value is _MISSING:
        raise ValueError(f"{what} is missing the '{key}' field")
    return value


def fit_robust_view(rows, covariates, spec, references, interactions, *, measure, model, vcov_type, level):
    from coscreen.meta_regression_robust import robust_meta_regression
    from coscreen.review_analysis import RATIOS
    if not _is_mapping(spec):
        raise ValueError("moderator specification must be a mapping")
    if not _is_mapping(references):
        raise ValueError("categorical moderator references must be a mapping")
    if not _is_mapping(covariates):
        raise ValueError("coded moderator values must be a mapping")
    moderators = {}
    for key, kind in spec.items():
        if kind == 'categorical':
            reference = _mapping_get(references, key)
            if reference is _MISSING:
                raise ValueError(f"categorical moderator {key} is missing its reference level")
            moderators[key] = {'type': 'categorical', 'reference': reference}
        else:
            moderators[key] = kind
    moderators.update({f'{a}:{b}':[a,b] for a,b in interactions})
    inputs = []
    for row in rows:
        study_id = _row_value(row, 'study_id', 'selected effect')
        covariate_values = _mapping_get(covariates, study_id)
        if covariate_values is _MISSING:
            raise ValueError(f"no coded moderator values for study {study_id}")
        estimate = _row_value(row, 'estimate', f"selected effect of study {study_id}")
        se = _row_value(row, 'se', f"selected effect of study {study_id}")
        if not _is_mapping(covariate_values):
            # 值存在但不是键值容器（None/list 等）：基线在 ** 展开处抛 TypeError
            # 逃逸 → 500。收敛为带说明的 ValueError，与其余缺字段缺口同为 4xx。
            raise ValueError(
                f"coded moderator values for study {study_id} must be a mapping")
        inputs.append({'study_id':study_id,
                       'effect':math.log(estimate) if measure in RATIOS else estimate,
                       'se':se, **covariate_values})
    result = robust_meta_regression(inputs,moderators,model=model,vcov_type=vcov_type,level=level)
    sources = [{key:_row_value(row, key, 'selected effect')
                for key in ('study_id','source_key','source_locator','result_id')} for row in rows]
    return {**result,'measure':measure,
            'analysis_scale':'log' if measure in RATIOS else 'fisher_z' if measure == 'FISHER_Z' else 'natural',
            'sources':sources,
            'method_sources':[
                'Tipton (2015), DOI 10.1037/met0000011: small-sample robust variance inference.',
                'Pustejovsky & Tipton (2018), DOI 10.1080/07350015.2016.1247004: CR0/CR1/CR2 and coefficient-specific Satterthwaite degrees of freedom.',
                'Each study is one independent cluster with one selected effect; this does not fit multiple dependent effects per study. Verify and cite the chosen method and study reports.',
            ]}
