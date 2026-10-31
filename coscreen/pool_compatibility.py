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
    signature_keys = ('estimator', 'standardizer', 'small_sample_correction', 'variance_method')
    signatures = {}         # 分类器现值口径（含 MM5-04 存量行读时升级）
    stored_signatures = {}  # 行内已存元数据原样口径（= 基线 758da65 语义；错误明细按此回报）
    # MM9-05：存量口径 → 现值口径的升级注记（仅记录两口径不同的行）：
    # 拒绝明细按存量原样回报时，真实冲突（现值口径下 approx-J vs exact-gamma
    # 等）不可见——用本表在 methods 条目上补挂当前分类器视图，不改变 methods
    # 既有键与 detail 文案。
    upgrades = {}  # stored 签名元组 -> {current 签名元组: [study_id, ...]}
    for row in smd_rows:
        data = row.get('input_data') if isinstance(row.get('input_data'), dict) else {}
        stored = data.get('estimator_metadata') or row.get('estimator_metadata') or {}
        entry_method = row.get('entry_method') or 'manual'
        baseline_metadata = stored if stored else legacy_smd_metadata(entry_method, data)
        current_metadata = baseline_metadata
        if stored.get('estimator') == 'unclassified_smd':
            # MM5-04 存量行升级：2.1.3–2.1.4 早期经提取模板入库的 SMD 行，在
            # "extraction:rct_continuous" 进入 _LEGACY_APPROXIMATE_SUMMARY_FORMATS
            # 之前被 save_effect 按旧分类固化为 unclassified_smd（存 input_json）。
            # entry_method 的语义权威是分类器现值——对已能识别的格式在读时升级
            # 签名，使其与臂级 hedges_g 行 / 修复后新写入的显式元数据行同池可合成；
            # 不重写任何存量数据。分类器仍判 unclassified 的行（如 living_review、
            # manual 未知 standardizer）不受影响。
            rederived = legacy_smd_metadata(entry_method, data)
            if rederived.get('estimator') != 'unclassified_smd':
                current_metadata = rederived
        stored_tuple = tuple(baseline_metadata.get(key) for key in signature_keys)
        current_tuple = tuple(current_metadata.get(key) for key in signature_keys)
        stored_signatures.setdefault(stored_tuple, []).append(row.get('study_id', 'unknown'))
        signatures.setdefault(current_tuple, []).append(row.get('study_id', 'unknown'))
        if current_tuple != stored_tuple:
            upgrades.setdefault(stored_tuple, {}).setdefault(
                current_tuple, []).append(row.get('study_id', 'unknown'))

    def _signature_conflict(sigmap: dict) -> bool:
        all_unclassified = bool(sigmap) and all(
            signature[0] == 'unclassified_smd' for signature in sigmap)
        return len(sigmap) > 1 or (
            not all_unclassified and any(None in signature for signature in sigmap))

    # MM5-04（评审第 2 轮）：读时升级只能让池更兼容（被识别的存量行并入
    # hedges_g 组），不得让基线可池的组合新增误拒（旧 extraction 行 +
    # living_review 行在基线同为单一 unclassified 签名、可池），也不得让仍
    # 拒绝的组合多出 estimator_conflict 明细。因此冲突必须在「存量原样」与
    # 「分类器现值」两个口径下同时成立才上报；methods 明细按存量原样回报，
    # 与基线逐字一致。MM9-05：对两口径不一致的存量组**加注**当前分类器视图
    # （additive 键），使真实冲突可见——判定门控与 reason 集合不变。
    if _signature_conflict(stored_signatures) and _signature_conflict(signatures):
        methods = []
        for signature, studies in stored_signatures.items():
            method = {'estimator':signature[0], 'standardizer':signature[1],
                      'small_sample_correction':signature[2],
                      'variance_method':signature[3], 'studies':studies}
            if signature in upgrades:
                method['legacy_upgrade_note'] = ('stored metadata kept verbatim; the current '
                                                 'classifier reads the marked studies as follows')
                method['current_classifier_views'] = [
                    {'estimator':current[0], 'standardizer':current[1],
                     'small_sample_correction':current[2], 'variance_method':current[3],
                     'studies':upgraded_studies}
                    for current, upgraded_studies in sorted(upgrades[signature].items())]
            methods.append(method)
        reasons.append({'type':'estimator_conflict', 'codes':['SMD'],
                        'methods':methods,
                        'detail':'Selected SMD results use different or undocumented estimators, corrections or variance methods. Choose compatible results or recalculate with a stated method.'})
    if reasons:
        raise PoolCompatibilityError(reasons, expected_measure)
