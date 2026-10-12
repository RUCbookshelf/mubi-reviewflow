/* Original SVG role emblems. Text labels remain the primary role indication. */
window.teamMemberIdentity = function(member, ownerId) {
  const identity=el('div',{class:'team-identity'},el('strong',{text:member.username,translate:'no'}));
  const roles=[...(member.user_id===ownerId?['owner']:[]),...(member.roles||[])];
  for(const role of [...new Set(roles)]){
    const label={owner:'Task owner',coder:'Coder',reconciler:'Adjudicator'}[role];
    if(!label)continue;
    const badge=el('span',{class:'team-role-badge role-'+role},el('span',{text:T(label)}));
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    for(const [key,value] of Object.entries({viewBox:'0 0 24 24',width:24,height:24,fill:'none',stroke:'currentColor','stroke-width':'1.6','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'}))svg.setAttribute(key,value);
    const path=document.createElementNS(svg.namespaceURI,'path');
    path.setAttribute('d',{
      owner:'M5 8 3 3l5 2 4-3 4 3 5-2-2 5H5Zm4 5a3 3 0 1 0 6 0 3 3 0 0 0-6 0Zm-4 9c0-4 3-6 7-6s7 2 7 6',
      coder:'M8 5H5v16h13v-6M9 3h6v4H9V3Zm0 9h3m-3 4h2m3-3 6-6 2 2-6 6-3 1 1-3Z',
      reconciler:'M12 3v17M7 21h10M4 7h16M7 7l-4 8h8L7 7Zm10 0-4 8h8l-4-8ZM3 15c0 3 8 3 8 0m2 0c0 3 8 3 8 0'
    }[role]);svg.append(path);badge.prepend(svg);identity.append(badge);
  }
  return identity;
};
