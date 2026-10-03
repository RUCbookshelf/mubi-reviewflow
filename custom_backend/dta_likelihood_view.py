"""Optional likelihood-ratio presentation from the existing bivariate fit."""


def likelihood_ratio_view(fit):
    try:
        from coscreen.dta_likelihood_ratios import summary_likelihood_ratios, _EXTREME_PROBABILITY

        context = {key: fit.get(key) for key in (
            'index_test', 'target_condition', 'threshold', 'reference_standard',
            'method', 'study_ids', 'studies', 'warnings')}
        result = summary_likelihood_ratios(
            fit['logit_sensitivity_mean'], fit['logit_specificity_mean'], fit.get('mean_covariance'),
            confidence=.95, n_samples=0, source_provenance=context)
        point = result['summary_point']
        near_boundary = any(value < _EXTREME_PROBABILITY or value > 1-_EXTREME_PROBABILITY
                            for value in (point['sensitivity'], point['specificity']))
        return {'available': True, **result,
                'display_intervals': result['mean_covariance'] is not None and not near_boundary,
                'interval_display_note': ('Intervals withheld: near-boundary summary probabilities make the log-Wald approximation unreliable.'
                                          if near_boundary else 'Intervals unavailable: fitted-mean estimation covariance is missing.'
                                          if result['mean_covariance'] is None else 'Approximate 95% delta-method intervals.'),
                'method_sources': ['Cochrane DTA Handbook, version 1.0 (2010), ch. 10, §10.5.2 p. 23 (summary-point ratios) and §10.4.2 pp. 19–20 (separate-pooling caveat).',
                                   'Zwinderman & Bossuyt (2008), We should not pool diagnostic likelihood ratios in systematic reviews. Statistics in Medicine 27:687–697.']}
    except (ImportError, ValueError, ArithmeticError, KeyError) as exc:
        return {'available': False, 'reason': f'Likelihood-ratio presentation unavailable: {exc}'}
