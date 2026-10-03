"""Network ranking API adapter; inference stays in the validated algorithms."""
from typing import Literal

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field


class NetworkRankingIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    outcome: str = Field(min_length=1)
    timepoint: str = Field(min_length=1)
    measure: str
    reference: str = Field(min_length=1)
    model: Literal['random_reml_common_tau']
    benefit: Literal['higher', 'lower']
    n_sim: int = Field(strict=True, ge=2, le=100000)
    seed: int = Field(strict=True, ge=0, le=2**32-1)


def register_network_ranking_routes(app, require_user, task_or_404, screener_db, bad):
    @app.post('/api/tasks/{task_id}/analysis/network/ranks')
    def rank(task_id: str, body: NetworkRankingIn, user: dict = Depends(require_user),
             format: Literal['docx','pptx','csv','tex'] | None = None):
        from coscreen.network_study_data import list_network_studies
        from coscreen.network_random import synthesize_network_random
        from coscreen.network_ranks import network_ranks
        from custom_backend.source_provenance import source_articles

        path = screener_db(task_or_404(task_id), user['username'])
        try:
            studies = list_network_studies(path, body.outcome, body.timepoint, body.measure)
            fitted = synthesize_network_random(studies, body.measure, body.reference)
            # Bound desktop request memory; the algorithm retains sampled ranks.
            if body.n_sim * len(fitted['treatments']) > 2000000:
                raise ValueError('Reduce simulation count: this interface supports at most 2,000,000 treatment draws per request.')
            result = network_ranks(fitted, n_sim=body.n_sim, seed=body.seed, benefit=body.benefit)
            result.update(sources={row['study_id']: row['source_key'] for row in studies},
                          source_articles=source_articles(path, studies),
                          request=body.model_dump(), method_sources=[
                              'Salanti, Ades & Ioannidis (2011), J Clin Epidemiol 64:163–171. DOI: 10.1016/j.jclinepi.2010.03.016.',
                              'Rücker & Schwarzer (2015), BMC Med Res Methodol 15:58. DOI: 10.1186/s12874-015-0060-8.'])
            if format:
                from custom_backend.analysis_audit_report import audit_response
                ranking = {row['treatment']: row for row in result['ranking']}
                table = dict(title='Treatment ranking',
                    columns=['Treatment','SUCRA','P(rank 1)','Mean rank'],
                    rows=[[name, *(f"{ranking[name][field]:.6g}" for field in ('sucra','p_best','mean_rank'))]
                          for name in result['treatments']],
                    note=f"Benefit={body.benefit}; reference={body.reference}; simulations={body.n_sim}; seed={body.seed}. Rank uncertainty, full input contrasts/covariances and citations follow in the audit appendix.")
                return audit_response({**result, 'source_studies':studies},format,
                                      'Network ranking audit',tables=[table])
            return result
        except ValueError as exc:
            raise bad(exc) from exc
