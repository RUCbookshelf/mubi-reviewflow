"""Optional prediction-region presentation for an existing DTA fit."""


def prediction_region_view(fit):
    try:
        # MM9-16：边界门控移入 try——fit 不是 dict（无 .get）或 warnings 元素
        # 不是 str（无 .lower）时同样按降级口径回报，而不是 AttributeError
        # 逃逸成 5xx（调用方 main.py 不捕该类异常）。
        if any('boundary' in warning.lower() and
               ('between-study covariance' in warning.lower() or 'pooled confidence' in warning.lower())
               for warning in fit.get('warnings', [])):
            return {'available': False, 'reason': 'Prediction region unavailable for a boundary fit.'}
        from coscreen.dta_prediction_region import prediction_region

        region = prediction_region(*(fit[key] for key in (
            'logit_sensitivity_mean', 'logit_specificity_mean', 'tau_sensitivity',
            'tau_specificity', 'random_effect_correlation')))
        return {'available': True, **region,
                'source_provenance': {key: fit.get(key) for key in (
                    'index_test', 'target_condition', 'threshold', 'reference_standard',
                    'study_ids', 'studies', 'warnings')},
                'method_sources': [
                    'Cochrane DTA Handbook, version 1.0, ch. 10, §10.5.2.1: bivariate model and prediction region; verify the current original text.',
                    'Reitsma et al. (2005), J Clin Epidemiol 58:982–990, doi:10.1016/j.jclinepi.2005.02.022.',
                ]}
    # MM9-16：fit 是外部传入的合成结果字典，补 TypeError/AttributeError 后
    # 边界形态（非 dict / warnings 非字符串列表 / 数值型别不符）一律降级为
    # available=False + 可读 reason，不再逃逸 5xx。
    except (ImportError, ValueError, RuntimeError, ArithmeticError, KeyError,
            TypeError, AttributeError) as exc:
        return {'available': False, 'reason': f'Prediction region unavailable: {exc}'}
