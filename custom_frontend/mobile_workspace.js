/* Mobile header uses the existing task, language, theme and route handlers. */
(function () {
  'use strict';
  const top = document.querySelector('#appMain>.top');
  if (!top || !window.RFWorkspaceShell || !window.RFNavigationSheet) return;
  const media = matchMedia('(max-width:760px)');
  const menu = document.createElement('button');
  menu.id = 'rfMobileNavigation';
  menu.type = 'button';
  menu.setAttribute('aria-controls', 'rfSheetContent');
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(svg.namespaceURI, 'path');
  path.setAttribute('d', 'M4 6h16M4 12h16M4 18h16');
  svg.append(path);menu.append(svg);
  const title = document.createElement('strong');
  title.id = 'rfMobilePageTitle';
  title.setAttribute('aria-live', 'polite');
  top.prepend(menu, title);
  let lastExpanded = 'false';
  function refresh() {
    menu.hidden = title.hidden = !media.matches;
    const page = window.RFNavigation?.pages?.find(item => item.id === S.page);
    const selected = document.querySelector('.page.on');
    const heading = selected?.querySelector('h2,.page-cap>b,.an-page-header h2');
    const crumb = top.querySelector('.rf-breadcrumb-list');
    const label = page?.label || heading?.textContent || crumb?.innerText || '';
    title.textContent = T(label);
    menu.setAttribute('aria-label', T('展开侧边栏'));
    menu.title = T('展开侧边栏');
    const original = document.getElementById('rfSidebarToggle');
    const expanded = original?.getAttribute('aria-expanded') || 'false';
    menu.setAttribute('aria-expanded', expanded);
    if (media.matches && expanded === 'false' && lastExpanded === 'true' &&
        (document.activeElement === original || document.activeElement === document.body)) menu.focus({preventScroll:true});
    lastExpanded = expanded;
  }
  menu.addEventListener('click', () => {
    const open = document.getElementById('rfSidebarToggle')?.getAttribute('aria-expanded') === 'true';
    window.RFNavigationSheet.setOpen(!open);
    refresh();
  });
  for (const name of ['reviewflow:workspace-changed','reviewflow:language-changed','reviewflow:task-context-changed'])
    document.addEventListener(name, refresh);
  media.addEventListener('change', refresh);
  new MutationObserver(refresh).observe(document.getElementById('rfSidebarToggle'), {
    attributes:true, attributeFilter:['aria-expanded']
  });
  refresh();
})();
