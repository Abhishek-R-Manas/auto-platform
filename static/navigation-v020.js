/* Temporary navigation disclosures share the existing routes and state. */
(() => {
'use strict';
const sidebar=document.getElementById('workspace-sidebar'),toggle=document.getElementById('sidebar-toggle'),more=document.getElementById('more-tools'),summary=more.querySelector('summary'),menu=document.getElementById('more-tools-menu');
let sidebarClose,moreClose,keyboardFocus=false;
document.addEventListener('keydown',event=>{if(event.key==='Tab')keyboardFocus=true;},true);
document.addEventListener('pointerdown',()=>{keyboardFocus=false;},true);
for(const button of sidebar.querySelectorAll('button[data-view]')){
 const text=[...button.childNodes].filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.textContent).join('').trim();
 if(!text)continue;
 button.setAttribute('aria-label',text);button.title=text;
 for(const node of [...button.childNodes])if(node.nodeType===Node.TEXT_NODE&&node.textContent.trim()){const label=document.createElement('span');label.className='nav-label';label.textContent=node.textContent.trim();node.replaceWith(label);}
}
function positionMenu(){
 if(!more.open)return;
 const anchor=summary.getBoundingClientRect(),width=menu.getBoundingClientRect().width,height=menu.getBoundingClientRect().height;
 menu.style.left=Math.max(8,Math.min(innerWidth-width-8,sidebar.getBoundingClientRect().right+4))+'px';
 menu.style.top=Math.max(8,Math.min(innerHeight-height-8,anchor.top))+'px';
}
function closeMore(){clearTimeout(moreClose);more.open=false;summary.setAttribute('aria-expanded','false');}
function setExpanded(open){clearTimeout(sidebarClose);sidebar.classList.toggle('is-expanded',open);toggle.setAttribute('aria-expanded',String(open));if(!open)closeMore();else positionMenu();}
function closeNavigation(restoreFocus=false){setExpanded(false);if(restoreFocus)toggle.focus();}
toggle.addEventListener('pointerenter',event=>{if(event.pointerType!=='touch')setExpanded(true);});
toggle.addEventListener('click',()=>setExpanded(!sidebar.classList.contains('is-expanded')));
sidebar.addEventListener('pointerenter',()=>clearTimeout(sidebarClose));
sidebar.addEventListener('pointermove',event=>{if(event.pointerType!=='touch'&&!sidebar.classList.contains('is-expanded'))setExpanded(true);});
sidebar.addEventListener('pointerleave',event=>{if(event.pointerType==='touch')return;sidebarClose=setTimeout(()=>closeNavigation(),160);});
sidebar.addEventListener('focusin',event=>{clearTimeout(sidebarClose);if(event.target!==toggle&&keyboardFocus)setExpanded(true);});
sidebar.addEventListener('focusout',()=>{sidebarClose=setTimeout(()=>{if(!sidebar.contains(document.activeElement))closeNavigation();},0);});
more.addEventListener('pointerenter',event=>{clearTimeout(moreClose);if(event.pointerType!=='touch'){more.open=true;summary.setAttribute('aria-expanded','true');positionMenu();}});
more.addEventListener('pointerleave',event=>{if(event.pointerType==='touch')return;moreClose=setTimeout(closeMore,160);});
menu.addEventListener('pointerenter',()=>{clearTimeout(sidebarClose);clearTimeout(moreClose);});
more.addEventListener('toggle',()=>{summary.setAttribute('aria-expanded',String(more.open));positionMenu();});
document.addEventListener('pointerdown',event=>{if(!sidebar.contains(event.target))closeNavigation();});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&(sidebar.classList.contains('is-expanded')||more.open)){event.preventDefault();closeNavigation(true);}});
window.addEventListener('artha:navigate',()=>{closeNavigation();if(sidebar.contains(document.activeElement)){const heading=document.getElementById('breadcrumb');heading.setAttribute('tabindex','-1');heading.focus({preventScroll:true});}});
window.addEventListener('resize',positionMenu);sidebar.querySelector('nav').addEventListener('scroll',positionMenu);
setExpanded(false);
})();
