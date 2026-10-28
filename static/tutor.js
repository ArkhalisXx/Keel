(() => {
 const button=document.querySelector('[data-reader-width]');
 if(button){
  function toggle(wide){document.body.classList.toggle('reader-wide',wide);button.setAttribute('aria-pressed',String(wide));button.textContent=wide?'Comfortable width':'Wide view';}
  try{toggle(localStorage.getItem('keel-wide-reader')==='true')}catch(_){}
  button.addEventListener('click',()=>{const wide=!document.body.classList.contains('reader-wide');toggle(wide);try{localStorage.setItem('keel-wide-reader',String(wide))}catch(_){}});
 }
 document.querySelectorAll('[data-jump-question]').forEach(link=>link.addEventListener('click',event=>{event.preventDefault();const field=document.getElementById('tutor-question');field.scrollIntoView({block:'center'});field.focus({preventScroll:true});}));
 document.querySelector('[data-latest-answer]')?.addEventListener('click',event=>{event.preventDefault();const message=document.querySelector('.chat-assistant:last-of-type')||document.querySelector('.chat-log');message.scrollIntoView({block:'start'});});
})();
