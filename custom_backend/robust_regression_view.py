"""Adapt saved effects and coded moderators to study-level sandwich inference."""
import math


def fit_robust_view(rows, covariates, spec, references, interactions, *, measure, model, vcov_type, level):
    from coscreen.meta_regression_robust import robust_meta_regression
    from coscreen.review_analysis import RATIOS
    moderators = {key:({'type':'categorical','reference':references[key]} if kind == 'categorical' else kind)
                  for key,kind in spec.items()}
    moderators.update({f'{a}:{b}':[a,b] for a,b in interactions})
    inputs = [{'study_id':row['study_id'],
               'effect':math.log(row['estimate']) if measure in RATIOS else row['estimate'],
               'se':row['se'], **covariates[row['study_id']]} for row in rows]
    result = robust_meta_regression(inputs,moderators,model=model,vcov_type=vcov_type,level=level)
    return {**result,'measure':measure,
            'analysis_scale':'log' if measure in RATIOS else 'fisher_z' if measure == 'FISHER_Z' else 'natural',
            'sources':[{key:row[key] for key in ('study_id','source_key','source_locator','result_id')} for row in rows],
            'method_sources':[
                'Tipton (2015), DOI 10.1037/met0000011: small-sample robust variance inference.',
                'Pustejovsky & Tipton (2018), DOI 10.1080/07350015.2016.1247004: CR0/CR1/CR2 and coefficient-specific Satterthwaite degrees of freedom.',
                'Each study is one independent cluster with one selected effect; this does not fit multiple dependent effects per study. Verify and cite the chosen method and study reports.',
            ]}
