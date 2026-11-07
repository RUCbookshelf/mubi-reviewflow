"""Optional descriptive heterogeneity intervals, isolated from primary synthesis."""


def heterogeneity_intervals(result):
    try:
        from coscreen.heterogeneity_interval import i2_h_interval
        interval = i2_h_interval(result['q'], result['n_studies'], level=.95)
        return {'available': True, **interval,
                'method_source': 'Higgins & Thompson (2002), Statistics in Medicine 21:1539–1558',
                'q_basis': 'fixed inverse-variance Q'}
    # MM10-18：result 是外部传入的合成结果字典，缺 'q'/'n_studies' 键
    # （KeyError）或不是 dict（TypeError）时同样按降级口径回报，而不是让
    # 5xx 冒泡——调用方（main.py 各合成端点）不捕 KeyError/TypeError。
    except (ImportError, ValueError, ArithmeticError, KeyError, TypeError) as exc:
        return {'available': False, 'reason': str(exc)}
