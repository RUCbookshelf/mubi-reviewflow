"""Optional presentation of an existing DTA fit; no refitting or persistence."""


def hsroc_view(fit):
    try:
        from coscreen.dta_hsroc import hsroc_from_bivariate

        parameters = {key: fit[key] for key in (
            'logit_sensitivity_mean', 'logit_specificity_mean', 'tau_sensitivity',
            'tau_specificity', 'random_effect_correlation')}
        context = {key: fit.get(key) for key in (
            'index_test', 'target_condition', 'threshold', 'reference_standard',
            'method', 'study_ids', 'studies', 'warnings')}
        result = hsroc_from_bivariate(parameters, source_provenance=context)
        return {'available': True, **result, 'sources': [
            'Cochrane DTA Handbook, version 1.0 (2010), ch. 10, §10.5.2.3, pp. 26–27.',
            'Harbord et al. (2007), Biostatistics 8:239–251. DOI: 10.1093/biostatistics/kxl004.',
            'Harbord & Whiting (2009), Stata Journal 9:211–229. DOI: 10.1177/1536867X0900900203.',
        ]}
    except (ImportError, ValueError, RuntimeError, ArithmeticError, KeyError) as exc:
        return {'available': False, 'reason': f'HSROC presentation unavailable: {exc}'}
