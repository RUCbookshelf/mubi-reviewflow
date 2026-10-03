(function () {
  for (const readonly of [false,true]) {
    const prefix = readonly ? 'anSurvivalMedianView' : 'anSurvivalMedian';
    const panel = $('#'+prefix+'Panel'), fields = $('#'+prefix+'Fields'), host = $('#'+prefix+'Result');
    const run = $('#'+prefix+'Run'), download = $('#'+prefix+'Export');
    let generation = 0, activeRequest = 0, record = null, timer = null, pending = false;
    const clear = () => { generation++; record = null; download.disabled = true; run.disabled = pending; if (!pending) host.replaceChildren(); };
    function input(key, label, choices, parent = fields) {
      const node = choices ? el('select',{class:'input','data-survival-key':key,'aria-label':label}) :
        el('input',{class:'input','data-survival-key':key,'aria-label':label,type:['time_unit','source_note'].includes(key) ? 'text' : 'number',step:'any'});
      if (choices) for (const [value,text] of [['','Choose '+label],...choices]) node.append(el('option',{value,text}));
      parent.append(el('label',{text:label},node)); return node;
    }
    const conversion = readonly ? null : input('conversion','Conversion',[['hr','Two-arm approximate hazard ratio'],['rate','Single-arm approximate event rate']]);
    input('assumption','Model assumption',[['acknowledged','I accept a constant hazard (exponential survival) model and approximate uncertainty']]);
    input('definition','Median definition',[['km_median_time_to_event','Reached Kaplan–Meier median time to the defined event']]);
    input('time_unit','Time unit (same for both arms)');
    if (readonly) input('source_note','Study source, event definition and table/page');
    const arms = el('div',{class:'row'}); fields.append(arms);
    function renderArms() {
      clear(); arms.replaceChildren();
      const kind = readonly ? 'rate' : conversion.value;
      if (!kind) return;
      if (kind === 'hr') input('direction','HR direction',[['treatment_vs_control','Treatment event hazard / control event hazard']],arms);
      for (const arm of kind === 'hr' ? ['treatment','control'] : ['single']) {
        const suffix = kind === 'hr' ? '_'+arm : '';
        input('median'+suffix,arm+' median',null,arms);
        if (readonly) continue;
        if (kind === 'hr') input('n'+suffix,arm+' number at risk',null,arms);
        const policy = input('policy'+suffix,arm+' event-count source',[['reported','Reported event count'],['half_n','Approximate events as n/2 (sensitivity)']],arms);
        const count = el('div'); arms.append(count);
        policy.addEventListener('change',() => {
          clear(); count.replaceChildren();
          if (policy.value === 'reported') input('events'+suffix,arm+' reported events',null,count);
          else if (policy.value === 'half_n' && kind === 'rate') input('n',arm+' number at risk',null,count);
        });
      }
    }
    if (conversion) conversion.addEventListener('change',renderArms);
    renderArms();
    const ids = readonly ? [] : ['anEffectStudy','anComparison','anOutcome','anTimepoint','anSource','anSourceLocator','anAppendEffect','anEffectDirection'];
    const context = () => JSON.stringify([S.task?.task_id,...ids.map(id => $('#'+id).type === 'checkbox' ? $('#'+id).checked : anValue(id)),...Array.from(fields.querySelectorAll('input,select'),node => node.value)]);
    panel.addEventListener('input',clear); ids.forEach(id => $('#'+id).addEventListener('input',clear));
    document.addEventListener('reviewflow:analysis-core-loaded',clear);
    document.addEventListener('reviewflow:analysis-task-changed', () => {
      activeRequest++; clearInterval(timer); pending = false;
      fields.querySelectorAll('input,select').forEach(node => node.value = '');
      renderArms(); clear();
    });
    run.addEventListener('click',async () => {
      if (pending) return;
      clear(); const ticket = generation, original = context(), taskId = S.task?.task_id, request = ++activeRequest;
      let requestTimer;
      try {
        const raw = {};
        fields.querySelectorAll('[data-survival-key]').forEach(node => {
          if (!node.value.trim()) throw new Error('Complete all visible fields and explicitly choose assumptions and event sources.');
          raw[node.dataset.survivalKey] = node.value.trim();
        });
        const kind = readonly ? 'rate' : raw.conversion;
        const values = {time_unit:raw.time_unit}, event_sources = {};
        if (kind === 'hr') { values.direction = raw.direction; values.oe_definition = 'implied_observed_minus_expected_treatment'; }
        else values.median_definition = raw.definition;
        for (const arm of kind === 'hr' ? ['treatment','control'] : ['single']) {
          const suffix = kind === 'hr' ? '_'+arm : '';
          event_sources[arm] = readonly ? 'unavailable' : raw['policy'+suffix];
          for (const key of ['median'+suffix,'n'+suffix,'events'+suffix]) if (raw[key] !== undefined) {
            const value = Number(raw[key]);
            if (!Number.isFinite(value) || value <= 0 || (!key.startsWith('median') && !Number.isSafeInteger(value))) throw new Error('Medians must be positive and counts must be positive integers.');
            values[key] = value;
          }
        }
        const body = {conversion:kind,exponential_assumption:raw.assumption,event_sources,values};
        if (readonly) values.source_provenance = {source_note:raw.source_note,median_definition:raw.definition};
        else {
          Object.assign(body,{study_id:anValue('anEffectStudy'),comparison:anValue('anComparison'),outcome:anValue('anOutcome'),timepoint:anValue('anTimepoint'),source_key:anValue('anSource'),source_locator:anValue('anSourceLocator'),append:$('#anAppendEffect').checked});
          if (kind === 'hr') {
            if (!anValue('anEffectDirection')) throw new Error('Choose how the HR direction maps to Comparison.');
            body.effect_direction = anValue('anEffectDirection');
          }
          if (!body.source_locator) throw new Error('Record source table/page and event definition above.');
          values.source_provenance = {source_key:body.source_key,source_locator:body.source_locator,median_definition:raw.definition};
        }
        pending = true; run.disabled = true;
        const started = performance.now();
        const progress = () => { if (request === activeRequest) host.textContent = T("Calculating {0}… {1} s elapsed.", T(ticket === generation ? 'survival summary' : 'previous inputs'), ((performance.now()-started)/1000).toFixed(1)); };
        progress(); timer = setInterval(progress,500); requestTimer = timer;
        const response = await api(taskApi()+'/analysis/survival-summary/'+(readonly ? 'convert' : 'effects'),{method:'POST',body});
        if (request !== activeRequest) { if (!readonly) toast(T("Saved a survival effect in the earlier task {0}.", taskId), 'warn'); return; }
        if (ticket !== generation) {
          if (!readonly && S.task?.task_id === taskId) { await loadAnalysis(); host.textContent = 'Saved the previous inputs. Review the effect list before saving again.'; }
          else if (!readonly) toast(T("Saved a survival effect in the earlier task {0}.", taskId), 'warn');
          return;
        }
        if (!readonly) {
          $('#anMeasure').value = response.measure; $('#anMeasure').dispatchEvent(new Event('change'));
          $('#anEstimate').value = response.estimate; $('#anSe').value = response.se;
          await loadAnalysis();
          if (context() !== original) {
            if (S.task?.task_id === taskId) host.textContent = 'Saved the previous inputs. Review the effect list before saving again.';
            else toast(T("Saved a survival effect in the earlier task {0}.", taskId), 'warn');
            return;
          }
        }
        const result = readonly ? response : response.calculation;
        host.append(el('p',{text:T("{0} {1}: {2}. {3}.", T(readonly ? 'Read-only' : 'Saved'),kind === 'hr' ? 'HR' : result.rate_unit,Number(result.estimate).toPrecision(6),result.se === null ? T('No SE or interval; cannot enter synthesis.') : T('Approximate log-scale SE:')+Number(result.se).toPrecision(6))}));
        if (kind === 'hr') host.append(el('p',{text:T("Reconstructed O−E={0}, V={1}; these are not reported log-rank statistics.", Number(result.implied_logrank_oe).toPrecision(6),Number(result.implied_logrank_variance).toPrecision(6))}));
        for (const text of [...result.warnings,...result.method_sources]) host.append(el('p',{class:'hint',text}));
        record = {request:body,result:response}; download.disabled = false;
      } catch (error) { if (request === activeRequest && S.task?.task_id === taskId) host.textContent = error.message; }
      finally { clearInterval(requestTimer); if (request === activeRequest) { pending = false; run.disabled = false; if (S.task?.task_id !== taskId || readonly && ticket !== generation) host.replaceChildren(); } }
    });
    download.addEventListener('click',() => {
      if (!record) return;
      const url = URL.createObjectURL(new Blob([JSON.stringify(record,null,2)],{type:'application/json'}));
      const link = el('a',{href:url,download:'reviewflow_survival_median.json'});
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
    });
  }
})();
