"""Read-only NNT presentation adapter; statistical formulas remain in coscreen."""

import math
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field


class NntConversionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    measure: Literal['rd', 'or', 'rr']
    estimate: float = Field(strict=True, allow_inf_nan=False)
    lower: float = Field(strict=True, allow_inf_nan=False)
    upper: float = Field(strict=True, allow_inf_nan=False)
    level: float = Field(strict=True, gt=0, lt=1, allow_inf_nan=False)
    cer: float | None = Field(default=None, strict=True, gt=0, lt=1, allow_inf_nan=False)
    adverse_event_confirmed: bool = Field(strict=True)
    time_horizon: str = Field(min_length=1, max_length=200)
    source_note: str = Field(default='', max_length=2000)


def register_nnt_routes(app, require_user, task_or_404):
    @app.post('/api/tasks/{task_id}/analysis/nnt-conversion')
    def convert_nnt(task_id: str, body: NntConversionIn, user: dict = Depends(require_user)):
        task_or_404(task_id)
        from coscreen.nnt_conversion import nnt_from_rd, nnt_from_or, nnt_from_rr

        try:
            if not body.adverse_event_confirmed:
                raise ValueError('Confirm that the recorded event is adverse; NNTB/NNTH labels assume reducing events is beneficial.')
            if not body.time_horizon.strip():
                raise ValueError('Specify the follow-up duration for the effect and baseline risk.')
            limits = [body.lower, body.upper]
            if body.measure == 'rd':
                if body.cer is not None:
                    raise ValueError('RD is already absolute; do not supply CER for the RD conversion.')
                result = nnt_from_rd(body.estimate, limits, level=body.level)
            else:
                if body.cer is None:
                    raise ValueError('A control event risk (CER) is required for OR/RR conversion.')
                function = nnt_from_or if body.measure == 'or' else nnt_from_rr
                result = function(body.estimate, limits, body.cer, level=body.level)
            values = [result['nnt_point']['value'], result['nnt_ci']['lower'], result['nnt_ci']['upper']]
            if any(value is not None and not math.isfinite(value) for value in values):
                raise ValueError('Converted magnitudes exceed the finite numeric range.')
            result.update(time_horizon=body.time_horizon.strip(), source_note=body.source_note.strip(),
                          adverse_event_confirmed=True, use='presentation_only',
                          method_sources=['Cochrane Handbook v6.5, §15.4.4 (NNT conversion and interpretation).',
                                          'Altman (1998), Confidence intervals for the number needed to treat. BMJ 317:1309–1312. DOI: 10.1136/bmj.317.7168.1309.'])
            return result
        except (ValueError, OverflowError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
