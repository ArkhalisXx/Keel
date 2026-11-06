(() => {
 const workspace=document.querySelector('.flashcard-workspace');if(!workspace)return;
 window.addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
 const form=document.querySelector('[data-flashcard-answer]');
 const nextForm=workspace.querySelector('.flashcard-next-form');
 const shortcuts=workspace.querySelector('[data-flashcard-shortcuts]');
 function keyboardAvailable(event){
   return !event.defaultPrevented && !event.repeat && !event.isComposing && event.keyCode!==229
     && !event.ctrlKey && !event.metaKey && !event.altKey && !event.shiftKey
     && !event.target?.closest?.('input,textarea,select,button,a,summary,[contenteditable]:not([contenteditable="false"])')
     && !document.querySelector('dialog[open],.assistant-drawer:not([hidden])');
 }
 if(shortcuts && (form || nextForm))shortcuts.hidden=false;
 if(form){
   const toggle=form.querySelector('[name=reveal]');
   try{toggle.checked=localStorage.getItem('keel-card-feedback')==='yes';}catch(_){}
   function feedbackPreference(){
     form.querySelector('button[type=submit]').textContent=toggle.checked?'Check answer →':'Answer & next →';
     form.querySelector('.flashcard-instruction').textContent=toggle.checked?'Choose an answer to check it. Continue when you’re ready.':'Choose an answer to move straight to the next card.';
   }
   feedbackPreference();
   toggle.addEventListener('change',()=>{try{localStorage.setItem('keel-card-feedback',toggle.checked?'yes':'no');}catch(_){} feedbackPreference(); });
   let submitted=false;
   form.addEventListener('submit',event=>{
     if(submitted){event.preventDefault();return;}
     submitted=true;form.setAttribute('aria-busy','true');form.querySelector('button[type=submit]').textContent='Saving answer…';
   });
   form.querySelectorAll('[name=answer]').forEach(input=>input.addEventListener('change',()=>{if(!submitted)form.requestSubmit();}));
   document.addEventListener('keydown',event=>{
     if(!keyboardAvailable(event) || submitted || !/^[1-4]$/.test(event.key))return;
     const option=form.querySelector(`[name=answer][value="${Number(event.key)-1}"]`);
     if(!option || option.disabled)return;
     event.preventDefault();
     option.checked=true;
     option.closest('.choice')?.classList.add('study-keyboard-choice');
     option.focus({preventScroll:true});
     option.dispatchEvent(new Event('change',{bubbles:true}));
   });
 }
 if(nextForm){
   let advanced=false;
   nextForm.addEventListener('submit',event=>{
     if(advanced){event.preventDefault();return;}
     advanced=true;nextForm.setAttribute('aria-busy','true');
     const button=nextForm.querySelector('button');if(button)button.textContent='Continuing…';
   });
   document.addEventListener('keydown',event=>{
     if(!keyboardAvailable(event) || advanced || event.key!=='Enter')return;
     event.preventDefault();nextForm.requestSubmit();
   });
 }
 workspace.querySelectorAll('.card-help-button').forEach(button=>button.addEventListener('click',async()=>{
   const output=button.closest('.card-help').querySelector('.card-help-output');
   button.disabled=true;output.textContent='Keel is reading this card…';
   const timer=setTimeout(()=>{output.textContent='The AI provider is still responding. Answer checking remains available.';},8000);
   try{
     const response=await fetch(workspace.dataset.helpUrl,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':workspace.dataset.csrf,'Accept':'application/json'},body:JSON.stringify({card:Number(button.dataset.card),intent:button.dataset.intent})});
     if(response.redirected)throw Error('Your session expired. Refresh and sign in again.');
     if(!response.headers.get('content-type')?.includes('application/json'))throw Error('Could not read the response. Try again shortly.');
     const data=await response.json();
     if(!response.ok)throw Error(data.error||'The AI could not complete this request.');
     output.replaceChildren();const text=document.createElement('p');text.className='answer';text.textContent=data.answer;output.append(text);
     window.KeelStudy?.addCopyButton(output,data.answer);
     if(data.warning){const warning=document.createElement('p');warning.className='privacy-note';warning.textContent=data.warning;output.append(warning);}
     for(const [i,source] of (data.document_sources||[]).entries()){
       const details=document.createElement('details');const heading=document.createElement('summary');heading.textContent=`[D${i+1}] ${source.title} · Page/section ${source.page}`;
       const passage=document.createElement('p');passage.textContent=source.text;details.append(heading,passage);output.append(details);
     }
   }catch(error){output.textContent=error.message;}finally{clearTimeout(timer);button.disabled=false;}
 }));
})();
