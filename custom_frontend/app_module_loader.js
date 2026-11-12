/* Keep workspace initialization ordered and out of the signed-out page. */
(() => {
  'use strict';
  let flight=null,complete=false;
  const parsed=document.readyState==='loading'
    ?new Promise(resolve=>document.addEventListener('DOMContentLoaded',resolve,{once:true}))
    :Promise.resolve();
  async function load(){
    await parsed;
    const modules=[...document.querySelectorAll('script[data-rf-app]')];
    for(const marker of modules){
      if(marker.dataset.rfAppLoaded==='true')continue;
      await new Promise((resolve,reject)=>{
        const script=document.createElement('script');
        script.async=false;
        script.src=marker.getAttribute('src');
        script.onload=()=>{marker.dataset.rfAppLoaded='true';resolve();};
        script.onerror=()=>{script.remove();reject(new Error('工作区载入失败，请重试登录。'));};
        marker.after(script);
      });
    }
    complete=true;
  }
  function ready(){
    if(complete)return Promise.resolve();
    if(!flight)flight=load().finally(()=>{flight=null;});
    return flight;
  }
  window.RFAppModules=Object.freeze({ready});
})();
