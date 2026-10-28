(() => {
 document.querySelectorAll('[data-password-toggle]').forEach(button=>{
  const field=document.getElementById(button.getAttribute('aria-controls'));
  if(!field)return;
  button.addEventListener('click',()=>{const show=field.type==='password';field.type=show?'text':'password';button.textContent=show?'Hide':'Show';button.setAttribute('aria-pressed',String(show));const label=document.querySelector(`label[for="${field.id}"]`)?.textContent.replace(/:$/,'').toLowerCase()||'password';button.setAttribute('aria-label',`${show?'Hide':'Show'} ${label}`);});
 });
})();
