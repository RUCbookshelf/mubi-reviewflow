/* Single navigation vocabulary for page headings, sidebar, breadcrumbs and deep links.
 * Presentation metadata only. No API endpoints, statistical defaults or user data. */
(function () {
  'use strict';
  const pages = [
    {id:'home',label:'首页与设置',stage:null},
    {id:'import',label:'导入文献',stage:'screening'},
    {id:'screen',label:'文献初筛',stage:'screening'},
    {id:'fulltext',label:'文献复筛',stage:'screening'},
    {id:'merge',label:'合并仲裁',stage:'screening'},
    {id:'export',label:'导出决策',stage:'screening'},
    {id:'prisma',label:'PRISMA 流程图',stage:'screening'},
    {id:'scheme',label:'编码方案',stage:'coding'},
    {id:'bench',label:'文献编码',stage:'coding'},
    {id:'analysis',label:'数据分析',stage:'analysis'},
    {id:'trace',label:'轨迹',stage:null},
    {id:'help',label:'帮助',stage:null}
  ];
  const stages = [
    {id:'screening',label:'筛选',defaultPage:'import'},
    {id:'coding',label:'编码',defaultPage:'scheme'},
    {id:'analysis',label:'分析',defaultPage:'analysis'}
  ];
  const groups = [
    {id:'data',label:'数据准备',description:'关联研究、汇总特征并记录偏倚风险。'},
    {id:'effects',label:'效应量计算',description:'从原始报告计算效应量，核对区间与尺度。'},
    {id:'synthesis',label:'总体效应合并',description:'选择与数据结构匹配的合并模型。'},
    {id:'heterogeneity',label:'异质性与调节分析',description:'描述研究间差异，并探索预先规定的调节变量。'},
    {id:'sensitivity',label:'敏感性分析',description:'检验结果对方法选择与纳入研究的敏感程度。'},
    {id:'bias',label:'发表偏倚检验',description:'检查小样本效应与缺失证据；不对称不等于发表偏倚。'},
    {id:'special',label:'专题分析与证据综合',description:'按研究问题选择专门模型或定性证据综合。'},
    {id:'report',label:'报告与审计',description:'核对实际方法、研究来源及导出记录。'}
  ];
  const tools = [
    {id:'studies',group:'data',label:'研究与报告',view:'anJumpStudies',keywords:'study report source'},
    {id:'features',group:'data',label:'研究特征',view:'anJumpFeatures',keywords:'summary cross-tab year'},
    {id:'risk-of-bias',group:'data',label:'偏倚风险评价',view:'anJumpRob',exclude:['anRobSensitivityPanel'],keywords:'RoB 2 ROBINS-I QUADAS-2'},
    {id:'effect-entry',group:'effects',label:'效应量录入与计算',view:'entry',keywords:'SMD MD RR OR HR Fisher z SMCR SMCC median paired cluster multi-arm'},
    {id:'study-intervals',group:'effects',label:'单研究区间与换算',view:'anIntervalsPanel',keywords:'CI NNT rate risk survival'},
    {id:'pooled-effect',group:'synthesis',label:'合并效应与森林图',view:'synthesis',tab:'results',keywords:'forest fixed random DL PM REML'},
    {id:'binary-pooling',group:'synthesis',label:'二分类固定效应合并',view:'anBinaryPoolPanel',keywords:'binary Mantel-Haenszel Peto'},
    {id:'single-counts',group:'synthesis',label:'单组计数似然模型',view:'anSingleGroupPanel',exclude:['anPetersPanel'],keywords:'binomial Poisson GLMM'},
    {id:'dependent-effects',group:'synthesis',label:'相关结局联合合并',view:'anDependentPanel',keywords:'dependent covariance'},
    {id:'multilevel',group:'synthesis',label:'三层随机截距 Meta 分析',view:'anMultilevelPanel',keywords:'multilevel three-level'},
    {id:'bayesian',group:'synthesis',label:'贝叶斯随机效应合并',view:'anBayesianPanel',keywords:'Bayesian posterior'},
    {id:'heterogeneity',group:'heterogeneity',label:'异质性统计量与区间',view:'synthesis',tab:'heterogeneity',keywords:'Q I² tau τ² profile prediction'},
    {id:'subgroups',group:'heterogeneity',label:'亚组与单变量回归',view:'anExploratoryPanel',modes:['subgroup','meta-regression'],keywords:'subgroup meta-regression moderator'},
    {id:'meta-regression',group:'heterogeneity',label:'多协变量 Meta 回归',view:'anMetaRegressionPanel',keywords:'multiple covariates moderator'},
    {id:'model-comparison',group:'sensitivity',label:'模型与区间比较',view:'synthesis',tab:'compare',keywords:'sensitivity estimator CI'},
    {id:'influence',group:'sensitivity',label:'逐研究删除与影响诊断',view:'synthesis',tab:'diagnostics',keywords:'leave-one-out LOSO LOO Cook leverage'},
    {id:'cumulative',group:'sensitivity',label:'按年份累积分析',view:'anExploratoryPanel',modes:['cumulative'],keywords:'cumulative year'},
    {id:'zero-cells',group:'sensitivity',label:'零单元格敏感性分析',view:'entry',entryMode:'anZeroCellPanel',keywords:'zero cells OR RR correction'},
    {id:'risk-exclusion',group:'sensitivity',label:'按偏倚风险排除',view:'anJumpRob',fragment:'anRobSensitivityPanel',keywords:'RoB exclusion ROBINS-I'},
    {id:'bayesian-priors',group:'sensitivity',label:'贝叶斯先验敏感性',view:'anRobustBayesPanel',keywords:'prior sensitivity Student t half-normal'},
    {id:'funnel-egger',group:'bias',label:'漏斗图与 Egger 检验',view:'anExploratoryPanel',modes:['small-study-effects'],keywords:'funnel Egger small-study asymmetry'},
    {id:'rank-failsafe',group:'bias',label:'等级相关与失效安全数',view:'anJumpBias',exclude:['anTrimFillPanel','anPetPeesePanel'],keywords:'Begg Kendall Rosenthal Rosenberg Orwin fail-safe'},
    {id:'trim-fill',group:'bias',label:'剪补法敏感性分析',view:'anJumpBias',fragment:'anTrimFillPanel',keywords:'trim fill Duval Tweedie L0 R0 Q0'},
    {id:'pet-peese',group:'bias',label:'PET/PEESE 精度回归',view:'anJumpBias',fragment:'anPetPeesePanel',keywords:'PET PEESE SE precision'},
    {id:'peters',group:'bias',label:'Peters 单组比例检验',view:'anSingleGroupPanel',fragment:'anPetersPanel',keywords:'Peters single proportion'},
    {id:'network',group:'special',label:'网络 Meta 分析',view:'anJumpNetwork',keywords:'network NMA ranking node-split inconsistency multi-arm'},
    {id:'dose-response',group:'special',label:'剂量反应分析',view:'anJumpDose',keywords:'dose response nonlinear spline'},
    {id:'ipd',group:'special',label:'个体参与者数据',view:'anJumpIpd',keywords:'IPD Cox survival interaction'},
    {id:'diagnostic-accuracy',group:'special',label:'诊断准确性',view:'anJumpDta',keywords:'DTA HSROC SROC sensitivity specificity'},
    {id:'qualitative',group:'special',label:'定性证据综合',view:'anJumpQual',keywords:'qualitative findings categories synthesis'},
    {id:'evidence-map',group:'data',label:'证据地图',view:'rfEvidenceMapPanel',keywords:'evidence map heatmap cluster gap'},
    {id:'grade',group:'report',label:'GRADE 评估',view:'rfGradePanel',keywords:'certainty quality SoF'},
    {id:'manuscript',group:'report',label:'撰写稿件',view:'rfManuscriptPanel',keywords:'manuscript Word LaTeX Markdown'},
    {id:'living-review',group:'report',label:'活体综述',view:'rfLivingReviewPanel',keywords:'living review update search monitoring'},
    {id:'methods',group:'report',label:'方法与引用',view:'synthesis',tab:'methods',keywords:'citation sources audit export Word PowerPoint LaTeX SVG CSV'}
  ];
  const pageMap=new Map(pages.map(item=>[item.id,item]));
  const groupMap=new Map(groups.map(item=>[item.id,item]));
  const toolMap=new Map(tools.map(item=>[item.id,item]));
  const legacy={anAll:null,anJumpEffects:'effect-entry',anModel:'pooled-effect',anConfigSummary:'pooled-effect',
    anSensitivityPanel:'model-comparison',anSensitivityWorkspace:'model-comparison',anEstimatorComparison:'model-comparison',
    anHeterogeneityNote:'heterogeneity',anTauProfileResult:'heterogeneity',anTauProfileRun:'heterogeneity',
    anLeaveOneOutResult:'influence',anInfluenceResult:'influence',anSubgroupRandomPanel:'subgroups',
    anRobSensitivityPanel:'risk-exclusion',anTrimFillPanel:'trim-fill',anPetPeesePanel:'pet-peese',anPetersPanel:'peters',
    anZeroCellPanel:'zero-cells',anUsedMethods:'methods',anUsedSources:'methods',
    anStudyIntervalPanel:'study-intervals',anPairedIntervalPanel:'study-intervals',anNntPanel:'study-intervals',
    anSurvivalIntervalsPanel:'study-intervals',anNntIntervalsPanel:'study-intervals'};
  for(const tool of tools)if(!(tool.view in legacy)&&!['entry','synthesis'].includes(tool.view))legacy[tool.view]=tool.id;
  const routeFor = tool => ({page:'analysis',group:tool.group,tool:tool.id});
  function href(route){
    if(typeof route==='string')route={page:route};
    const base='#workspace/'+route.page;
    return route.page==='analysis'&&route.group?base+'/'+route.group+(route.tool?'/'+route.tool:''):base;
  }
  function parse(hash){
    let raw;try{raw=decodeURIComponent((hash||'').replace(/^#/,''));}catch{return null;}
    if(Object.hasOwn(legacy,raw))return legacy[raw]?{...routeFor(toolMap.get(legacy[raw])),anchor:raw}:{page:'analysis'};
    const parts=raw.split('/');
    if(parts[0]!=='workspace'||!pageMap.has(parts[1]))return null;
    if(parts[1]!=='analysis')return parts.length===2?{page:parts[1]}:null;
    if(parts.length===2)return{page:'analysis'};
    if(parts.length>4||!groupMap.has(parts[2]))return null;
    if(parts.length===3)return{page:'analysis',group:parts[2]};
    const tool=toolMap.get(parts[3]);return tool?.group===parts[2]?routeFor(tool):null;
  }
  function fromView(view,tab){return tools.find(tool=>tool.view===view&&(!tab||tool.tab===tab))||null;}
  function forElement(id){
    if(Object.hasOwn(legacy,id))return legacy[id]?toolMap.get(legacy[id]):null;
    const target=document.getElementById(id);if(!target)return null;
    for(const tool of tools){if(tool.fragment&&document.getElementById(tool.fragment)?.contains(target))return tool;}
    const tab=target.closest('.an-task-view')?.id.replace('anView-','');
    if(tab)return fromView('synthesis',tab);
    const card=target.closest('.an-workspace>.card');
    if(card?.id==='anJumpEffects')return toolMap.get('effect-entry');
    return fromView(card?.id);
  }
  // Freeze every nested item: menus may read this registry but may not mutate API/UI state.
  for(const collection of [pages,stages,groups,tools])for(const item of collection){
    Object.values(item).forEach(value=>{if(Array.isArray(value))Object.freeze(value);});Object.freeze(item);
  }
  window.RFNavigation=Object.freeze({pages:Object.freeze(pages),stages:Object.freeze(stages),groups:Object.freeze(groups),tools:Object.freeze(tools),
    page:id=>pageMap.get(id),group:id=>groupMap.get(id),tool:id=>toolMap.get(id),
    stage:id=>stages.find(item=>item.id===id),siblings:id=>pages.filter(item=>item.stage===pageMap.get(id)?.stage),
    methods:group=>group?tools.filter(item=>item.group===group):tools,href,parse,routeFor,fromView,forElement});
})();
