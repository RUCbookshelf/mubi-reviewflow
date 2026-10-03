/* Forest display and SVG export. Numerical transformations match the existing frontend.
 * Source metadata remain complete; long citations live in the Methods & citations view.
 */
function createAnalysisForestSvg(result, scope, style) {
  scope = scope || Object.fromEntries(['anComparison','anOutcome','anTimepoint'].map(id=>[id,anValue(id)]));
  style = style || {color:anValue('anForestColor'),digits:Number(anValue('anForestDigits')),title:anValue('anForestCustomTitle'),prediction:$('#anForestShowPrediction').checked};
  const titleOffset=style.title?32:0;
  const width=920,left=220,right=660,rowH=32,top=48+titleOffset;
  const accent=/^#[0-9a-f]{6}$/i.test(style.color)?style.color:'#28695f';
  const digits=[2,3,4].includes(Number(style.digits))?Number(style.digits):3;
  const ratio=['RR','OR','HR','RATE_RATIO'].includes(result.measure);
  const correlation=result.measure==='FISHER_Z',proportion=result.measure==='LOGIT_PROP',rate=result.measure==='LOG_RATE';
  const logRatio=['LOG_ROM','PAIRED_OR'].includes(result.measure);
  const pointEstimate=['MEAN','LOGIT_PROP','LOG_RATE'].includes(result.measure);
  const neutral=ratio||logRatio?1:0;
  const toScale=x=>ratio||logRatio?Math.log(x):correlation?Math.atanh(x):proportion?Math.log(x/(1-x)):rate?Math.log(x):x;
  const fromScale=x=>ratio||rate||logRatio?Math.exp(x):correlation?Math.tanh(x):proportion?1/(1+Math.exp(-x)):x;
  const number=x=>Number.isFinite(x)?x.toPrecision(digits):String(x);
  const studyCiMethod='normal 95% (estimate ± 1.96 × SE on analysis scale)';
  const term=value=>window.RFAnalysisLocale?RFAnalysisLocale.term(value):T(value);
  const rows=result.effects.map(effect=>{
    const mid=correlation||proportion||rate||logRatio?effect.estimate:toScale(effect.estimate);
    return {label:effect.study_id+(effect.input_data?.estimated_summary?' [E]':'')+(effect.input_data?.survival_summary_conversion?' [M]':''),mid,lo:mid-1.96*effect.se,hi:mid+1.96*effect.se};
  });
  rows.push({label:T('合并结果'),pooled:true,mid:result.pooled_analysis??toScale(result.pooled),lo:result.ci_analysis_low??toScale(result.ci_low),hi:result.ci_analysis_high??toScale(result.ci_high)});
  if(result.prediction_interval_95&&style.prediction)rows.push({label:T('预测区间 95%'),prediction:true,mid:result.pooled_analysis??toScale(result.pooled),lo:result.prediction_interval_analysis_95?.[0]??toScale(result.prediction_interval_95[0]),hi:result.prediction_interval_analysis_95?.[1]??toScale(result.prediction_interval_95[1])});
  const footnotes=[T('{0} · {1} · {2} 项研究',result.measure,term(result.method),result.n_studies),
    T('单研究区间：{0}',term(studyCiMethod)),T('合并区间：{0}',term(result.ci_method))];
  if(result.effects.some(effect=>effect.input_data?.estimated_summary))footnotes.push(T('[E] 各组均值/标准差由公式估计；效应量 SE 未计入重建不确定性。'),T('请比较纳入与排除这些研究的结果；方法和来源见估计审计记录。'));
  if(result.effects.some(effect=>effect.input_data?.survival_summary_conversion))footnotes.push(T('[M] 由中位生存时间近似换算，假设生存时间服从指数分布（即瞬时风险率恒定）。'),T('请核对事件数和删失情况；优先采用原文 HR/CI 或 log-rank 数据。'));
  (result.warnings||[]).forEach(warning=>footnotes.push(T('警示：')+term(warning)));
  footnotes.push(T('完整方法、限制和研究来源见“方法与引用”；计算输入保留于 SVG 元数据。'));
  // Wrap by measured width, including CJK text without spaces.
  const canvas=document.createElement('canvas'),ctx=canvas.getContext('2d');ctx.font='11px Arial, sans-serif';
  const wrapped=[];
  for(const text of footnotes){let line='';for(const char of String(text)){if(ctx.measureText(line+char).width>width-34){wrapped.push(line);line=char;}else line+=char;}if(line)wrapped.push(line);}
  const axisY=top+rows.length*rowH+9,footerY=axisY+45,height=footerY+wrapped.length*16+10;
  // 坐标系防护：任一有限数值都没有时，min/max 为 ±Infinity、x() 全 NaN，
  // 图会静默输出一片空白（P2-11）——显式报错而不是空白图。
  const plotValues=rows.flatMap(row=>[row.mid,row.lo,row.hi]).filter(Number.isFinite);
  if(!plotValues.length)throw new Error(T('森林图数据不含有限的效应值或区间端点，无法建立坐标系；请检查效应值与 SE。'));
  const values=rows.flatMap(row=>[row.lo,row.hi]).filter(Number.isFinite);if(!pointEstimate)values.push(toScale(neutral));
  const min=Math.min(...values),max=Math.max(...values),span=max-min||1;
  const x=value=>left+(value-min)/span*(right-left);
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  Object.entries({viewBox:`0 0 ${width} ${height}`,width:'100%',role:'img','aria-labelledby':'anForestTitle anForestDescription',translate:'no'}).forEach(([key,value])=>svg.setAttribute(key,value));
  const add=(tag,attrs,text)=>{const node=document.createElementNS('http://www.w3.org/2000/svg',tag);Object.entries(attrs).forEach(([key,value])=>node.setAttribute(key,String(value)));if(text!==undefined)node.textContent=String(text);svg.append(node);return node;};
  const textAttrs={fill:'#293d33','font-family':'Arial, Microsoft YaHei, Noto Sans CJK SC, sans-serif','font-size':13};
  add('title',{id:'anForestTitle'},style.title||T('{0} 森林图：{1}，{2}',result.measure,scope.anOutcome,scope.anTimepoint));
  add('desc',{id:'anForestDescription'},T('方法：{0}；单研究区间：{1}；合并区间：{2}。所选结果 ID：{3}。计算输入与来源见 SVG 审计元数据。请核对原文并引用所用方法和研究报告。',term(result.method),term(studyCiMethod),term(result.ci_method),result.effects.map(effect=>effect.result_id).join(', ')));
  add('metadata',{id:'anForestAudit',type:'application/json'},JSON.stringify({
    comparison:scope.anComparison,outcome:scope.anOutcome,timepoint:scope.anTimepoint,
    measure:result.measure,model:result.model,method:result.method,ci_method:result.ci_method,
    study_ci_method:studyCiMethod,pooled_ci_method:result.ci_method,effect_scale:result.effect_scale,
    pooled:result.pooled,pooled_analysis:result.pooled_analysis,ci_low:result.ci_low,ci_high:result.ci_high,
    ci_analysis_low:result.ci_analysis_low,ci_analysis_high:result.ci_analysis_high,
    q:result.q,q_df:result.q_df,q_p:result.q_p,i2_percent:result.i2_percent,tau2:result.tau2,
    prediction_interval_95:result.prediction_interval_95,prediction_interval_analysis_95:result.prediction_interval_analysis_95,
    heterogeneity_intervals:result.heterogeneity_intervals,n_studies:result.n_studies,warnings:result.warnings,
    method_sources:result.method_sources||[],effects:result.effects.map(effect=>({...anSourceFields(effect.source_key),...effect}))
  }));
  add('rect',{x:0,y:0,width,height,fill:'#fff'});
  if(style.title)add('text',{...textAttrs,x:8,y:22,'font-size':16,'font-weight':600},style.title);
  add('text',{...textAttrs,x:8,y:22+titleOffset,'font-size':11,fill:'#617169'},T('研究'));
  add('text',{...textAttrs,x:690,y:22+titleOffset,'font-size':11,fill:'#617169'},T('效应及 95% 置信区间'));
  add('line',{x1:8,x2:width-8,y1:33+titleOffset,y2:33+titleOffset,stroke:'#dce3de'});
  rows.forEach((row,index)=>{
    const y=top+index*rowH;
    if(row.pooled)add('rect',{x:0,y:y-16,width,height:rowH,fill:'#edf4f0'});
    else if(index%2===1&&!row.prediction)add('rect',{x:0,y:y-16,width,height:rowH,fill:'#fafbf9'});
  });
  if(!pointEstimate)add('line',{x1:x(toScale(neutral)),x2:x(toScale(neutral)),y1:35+titleOffset,y2:axisY,stroke:'#88998f','stroke-dasharray':'3 4','stroke-width':1});
  rows.forEach((row,index)=>{
    const y=top+index*rowH;
    const label=[...String(row.label)];const renderedLabel=label.length>27?label.slice(0,26).join('')+'…':row.label;
    const labelNode=add('text',{...textAttrs,x:8,y:y+4,'font-weight':row.pooled?600:400},renderedLabel);
    if(renderedLabel!==row.label){const title=document.createElementNS(svg.namespaceURI,'title');title.textContent=row.label;labelNode.append(title);}
    add('line',{x1:x(row.lo),x2:x(row.hi),y1:y,y2:y,stroke:accent,'stroke-width':1.5,'stroke-dasharray':row.prediction?'5 3':''});
    if(row.pooled)add('polygon',{points:`${x(row.lo)},${y} ${x(row.mid)},${y-6} ${x(row.hi)},${y} ${x(row.mid)},${y+6}`,fill:accent});
    else if(!row.prediction)add('rect',{x:x(row.mid)-3.5,y:y-3.5,width:7,height:7,rx:.6,fill:accent});
    if(!row.pooled){add('line',{x1:x(row.lo),x2:x(row.lo),y1:y-3,y2:y+3,stroke:accent});add('line',{x1:x(row.hi),x2:x(row.hi),y1:y-3,y2:y+3,stroke:accent});}
    add('text',{...textAttrs,x:690,y:y+4,'font-size':12,'font-weight':row.pooled?600:400},`${number(fromScale(row.mid))}  [${number(fromScale(row.lo))}, ${number(fromScale(row.hi))}]`);
  });
  add('line',{x1:left,x2:right,y1:axisY,y2:axisY,stroke:'#a7b4ac'});
  for(let i=0;i<=4;i++){const value=min+span*i/4,pos=x(value);add('line',{x1:pos,x2:pos,y1:axisY,y2:axisY+5,stroke:'#a7b4ac'});add('text',{...textAttrs,x:pos,y:axisY+20,'text-anchor':'middle','font-size':10,fill:'#617169'},number(fromScale(value)));}
  wrapped.forEach((line,index)=>add('text',{...textAttrs,x:8,y:footerY+index*16,fill:'#617169','font-size':11},line));
  return svg;
}
function renderForest(result) {
  anLastForestResult=result;
  $('#anForest').replaceChildren(createAnalysisForestSvg(result));
  for(const id of ['anForestExport','anReportDocx','anReportPptx','anReportTex','anSynthesisCsv'])$('#'+id).disabled=false;
  document.dispatchEvent(new Event('reviewflow:analysis-forest-rendered'));
}
['anForestCustomTitle','anForestColor','anForestDigits','anForestShowPrediction'].forEach(id=>$('#'+id).addEventListener('input',()=>{if(anLastForestResult)renderForest(anLastForestResult);}));
// Changes to model fields are committed by the workspace; draft controls never dispatch here.
['anComparison','anOutcome','anTimepoint','anMeasure','anModel','anCiMethod'].forEach(id=>$('#'+id).addEventListener('input',invalidateForest));
document.addEventListener('reviewflow:language-changed',()=>{if(anLastForestResult)renderForest(anLastForestResult);});
$('#anForestExport').addEventListener('click',()=>{
  const svg=$('#anForest svg');if(!svg||!anLastForestResult)return;
  const source=new XMLSerializer().serializeToString(svg);
  const url=URL.createObjectURL(new Blob([source],{type:'image/svg+xml;charset=utf-8'}));
  const link=el('a',{href:url,download:'reviewflow_forest.svg'});document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
