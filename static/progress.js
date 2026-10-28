(() => {
 const form=document.querySelector('[data-analytics-filter]');if(!form)return;
 const period=form.elements.period;
 function update(){form.querySelectorAll('[data-period-control]').forEach(label=>{const active=label.dataset.periodControl===period.value;label.hidden=!active;label.querySelectorAll('input').forEach(input=>input.disabled=!active)});}
 period.addEventListener('change',update);update();
})();
