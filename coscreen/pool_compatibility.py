"""Metadata guard shared by stored and direct synthesis entry points."""

from coscreen.measure_registry import check_pool_compatibility
from coscreen.smd_contract import legacy_smd_metadata

NON_DIRECTIONAL_MEASURES = frozenset({'FISHER_Z', 'PHI', 'R_EQUIV_APPROX', 'MEAN', 'LOGIT_PROP', 'LOG_RATE'})


class PoolCompatibilityError(ValueError):
    def __init__(self, reasons, expected_measure=None):
        message = 'incompatible selected effect measures, standardizers or directions for this comparison/outcome/timepoint'
        if expected_measure is not None:
            message += f'; expected {expected_measure}'
        super().__init__(message)
        self.reasons = reasons


def require_compatible_effects(rows, expected_measure=None):
    """Reject incompatible families, standardizers and reversed saved effects."""
    # Internal callers such as dose-response supply the scale once per pool.
    codes = [row.get('measure', expected_measure) for row in rows]
    if expected_measure is not None:
        codes.append(expected_measure)
    reasons = check_pool_compatibility(codes)['reasons']
    reversed_studies = [row.get('study_id', 'unknown') for row in rows
                        if isinstance(row.get('input_data'), dict)
                        and row['input_data'].get('effect_direction') == 'second_vs_first']
    if reversed_studies:
        reasons.append({'type':'direction_conflict', 'studies':reversed_studies,
                        'detail':'These reported effects run opposite to Comparison. Convert the estimate and uncertainty to first-group vs second-group direction before pooling.'})
    known_direction = any(isinstance(row.get('input_data'), dict) and
                          row['input_data'].get('effect_direction') in {'first_vs_second', 'second_vs_first'}
                          for row in rows)
    if known_direction:
        unknown_studies = [row.get('study_id', 'unknown') for row in rows
                           if row.get('measure', expected_measure) not in NON_DIRECTIONAL_MEASURES
                           and not (isinstance(row.get('input_data'), dict) and
                                    row['input_data'].get('effect_direction'))]
        if unknown_studies:
            reasons.append({'type':'direction_unknown', 'studies':unknown_studies,
                            'detail':'Confirm the effect direction of these older results before pooling them with direction-checked results.'})
    smd_rows = [row for row in rows if row.get('measure', expected_measure) == 'SMD']
    glass, other, unknown = [], [], []
    for row in smd_rows:
        data = row.get('input_data') or {}
        if not isinstance(data, dict):
            data = {}
        is_glass = (row.get('entry_method') == 'glass_delta_two_arm' or
                    row.get('standardizer') == 'control_arm_sd' or
                    data.get('standardizer') == 'control_arm_sd')
        if row.get('entry_method') == 'manual':
            if data.get('standardizer') == 'control_arm_sd' and data.get('bias_correction') == 'none':
                glass.append(row.get('study_id', 'unknown'))
            elif data.get('standardizer') == 'pooled_within_group_sd' and data.get('bias_correction') == 'hedges':
                other.append(row.get('study_id', 'unknown'))
            else:
                unknown.append(row.get('study_id', 'unknown'))
        elif is_glass:
            glass.append(row.get('study_id', 'unknown'))
        else:
            other.append(row.get('study_id', 'unknown'))
    if glass and other:
        reasons.append({'type':'standardizer_conflict', 'codes':['SMD'],
                        'glass_studies':glass, 'other_studies':other,
                        'detail':"Glass delta uses the control-arm SD; it cannot share a pool with Hedges g or SMDs whose standardizer is not confirmed as the control-arm SD. Use separate analysis strata."})
    if unknown:
        reasons.append({'type':'standardizer_conflict', 'codes':['SMD'],
                        'unknown_studies':unknown,
                        'detail':'Manual SMD standardizer or small-sample correction is unknown. Confirm and save it as Hedges g (pooled SD, corrected) or Glass delta (control SD, uncorrected) before synthesis.'})
    signatures = {}
    for row in smd_rows:
        data = row.get('input_data') if isinstance(row.get('input_data'), dict) else {}
        metadata = data.get('estimator_metadata') or row.get('estimator_metadata') or {}
        entry_method = row.get('entry_method') or 'manual'
        if not metadata:
            metadata = legacy_smd_metadata(entry_method, data)
        elif metadata.get('estimator') == 'unclassified_smd':
            # MM5-04 存量行升级：2.1.3–2.1.4 早期经提取模板入库的 SMD 行，在
            # "extraction:rct_continuous" 进入 _LEGACY_APPROXIMATE_SUMMARY_FORMATS
            # 之前被 save_effect 按旧分类固化为 unclassified_smd（存 input_json）。
            # entry_method 的语义权威是分类器现值——对已能识别的格式在读时升级
            # 签名，与臂级 hedges_g 行 / 修复后新写入的显式元数据行同池可合成；
            # 分类器仍判 unclassified 的行（如 manual 未知 standardizer）不受影响，
            # 不重写任何存量数据。
            rederived = legacy_smd_metadata(entry_method, data)
            if rederived.get('estimator') != 'unclassified_smd':
                metadata = rederived
        keys = ('estimator', 'standardizer', 'small_sample_correction', 'variance_method')
        signature = tuple(metadata.get(key) for key in keys)
        signatures.setdefault(signature, []).append(row.get('study_id', 'unknown'))
    all_unclassified = bool(signatures) and all(
        signature[0] == 'unclassified_smd' for signature in signatures
    )
    if (len(signatures) > 1 or
            (not all_unclassified and any(None in signature for signature in signatures))):
        reasons.append({'type':'estimator_conflict', 'codes':['SMD'],
                        'methods':[{'estimator':signature[0], 'standardizer':signature[1],
                                    'small_sample_correction':signature[2],
                                    'variance_method':signature[3], 'studies':studies}
                                   for signature, studies in signatures.items()],
                        'detail':'Selected SMD results use different or undocumented estimators, corrections or variance methods. Choose compatible results or recalculate with a stated method.'})
    if reasons:
        raise PoolCompatibilityError(reasons, expected_measure)
