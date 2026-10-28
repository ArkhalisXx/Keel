(() => {
 const panel=document.querySelector('[data-reviewer-status]');if(!panel)return;
 let stopped=false;
 async function check(){
   if(stopped)return;
   try{const r=await fetch(panel.dataset.reviewerStatus,{headers:{Accept:'application/json'}});
     if(r.redirected||r.status===404){stopped=true;return;}
     if(r.ok){const data=await r.json();if(['ready','failed'].includes(data.status)||(data.can_retry && panel.dataset.stalled!=='true')){location.reload();return;}}
   }catch(_){/* Preserve page and retry metadata request, never re-submit generation. */}
   setTimeout(check,5000);
 }
 setTimeout(check,3000);
})();
