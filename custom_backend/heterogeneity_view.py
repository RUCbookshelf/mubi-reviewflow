"""Optional descriptive heterogeneity intervals, isolated from primary synthesis."""


def heterogeneity_intervals(result):
    try:
        from coscreen.heterogeneity_interval import i2_h_interval
        interval = i2_h_interval(result['q'], result['n_studies'], level=.95)
        return {'available': True, **interval,
                'method_source': 'Higgins & Thompson (2002), Statistics in Medicine 21:1539–1558',
                'q_basis': 'fixed inverse-variance Q'}
    except (ImportError, ValueError, ArithmeticError) as exc:
        return {'available': False, 'reason': str(exc)}
