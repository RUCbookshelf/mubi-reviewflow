"""Peters single-proportion diagnostic using selected, source-linked raw counts."""
from typing import Literal

from fastapi import Depends


def register_peters_routes(app, require_user, task_or_404, screener_db, bad):
    @app.get('/api/tasks/{task_id}/analysis/single-group/peters')
    def peters(task_id: str, comparison: str, outcome: str, timepoint: str,
               user: dict = Depends(require_user),
               format: Literal['docx', 'pptx', 'csv', 'tex'] | None = None):
        from coscreen.single_group_data import selected_single_group_counts
        from coscreen.peters_test import peters_test

        path = screener_db(task_or_404(task_id), user['username'])
        try:
            rows = selected_single_group_counts(path, comparison, outcome, timepoint, 'proportion')
            result = peters_test(rows, source_provenance={
                'comparison': comparison, 'outcome': outcome, 'timepoint': timepoint,
                'kind': 'proportion', 'count_selection': 'one selected raw count per independent study'})
            if format:
                from custom_backend.analysis_audit_report import audit_response
                from custom_backend.source_provenance import source_articles
                slope = result['coefficients']['slope_on_inverse_total']
                table = {'title':'Peters regression of single proportions',
                         'columns':['Included studies', 'Excluded studies', 'Slope on 1/total', 'SE', 't', 'p'],
                         'rows':[[result['n_included'], len(result['excluded_studies']), slope['estimate'],
                                  slope['se'], result['statistic'], result['p_value']]],
                         'note':result['source'] + '. ' + result['limitation']}
                return audit_response({**result, 'source_articles':source_articles(path, rows)},
                                      format, 'Peters proportion asymmetry audit', tables=[table])
            return result
        except ValueError as exc:
            raise bad(exc) from exc
