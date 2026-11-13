(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const runBtn = $('runBtn');
  const statePill = $('statePill');
  const dialog = $('approvalDialog');
  let lastSignature = '';
  let modalWasOpened = false;
  let previousStatus = '';
  let lastFinal = '';
  let speaking = false;
  let validationState = null;
  let toastTimer;

  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function toast(msg) {
    $('toast').textContent = msg;
    $('toast').classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => $('toast').classList.remove('show'), 4500);
  }
  async function post(path, body) {
    const response = await fetch(path, {method:'POST',headers:{'Content-Type':'application/json','X-Keyomars-UI':'1'},body:JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw Error(data.error || data.message || 'درخواست انجام نشد');
    return data;
  }
  function elapsed(n) {
    const seconds = Math.max(0, Math.floor(n));
    return `${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;
  }
  const tags = {idle:'آماده شروع', running:'در حال تحلیل واقعی', approval:'منتظر تأیید شما', done:'تحلیل تمام شد', error:'خطا در تحلیل'};
  const glyphs = {info:'✦',thinking:'◌',running:'↻',success:'✓',error:'!',approval:'⚑'};

  function renderLogs(logs) {
    const signature = logs.map(l=>l.id).join(',');
    if (signature===lastSignature) return;
    lastSignature = signature;
    if (!logs.length) {
      $('steps').innerHTML = `<div class="empty-logs"><div class="empty-logs-icon">⌘</div><strong>هنوز تحلیلی شروع نشده</strong><p>روی «شروع تحلیل پروژه» کلیک کن تا فعالیت واقعی کیومرث اینجا نمایش داده بشه.</p></div>`;
      return;
    }
    $('steps').innerHTML = logs.map(item => `<div class="log-item ${esc(item.kind)}">
      <div class="log-icon">${esc(glyphs[item.kind] || '·')}</div>
      <div class="log-copy"><div class="log-heading"><b>${esc(item.message)}</b><span class="log-time">${esc(item.time)}</span></div>
      ${item.details ? `<p>${esc(item.details)}</p>` : ''}</div>
    </div>`).join('');
    $('steps').scrollTop = $('steps').scrollHeight;
  }

  function renderValidation(v, summary) {
    validationState = v;
    const badge=$('validationPill'), issueBox=$('validationIssues'), dayBox=$('dailyPlan');
    $('extraHours').textContent = v ? `${v.extra_hours} ساعت` : '—';
    if(summary) {
      $('baseCapacity').textContent=`${summary.base_capacity_hours} ساعت`;
      $('requiredHours').textContent=`${summary.mandatory_hours} ساعت`;
    }
    if (!v) {
      badge.textContent='هنوز بررسی نشده';badge.className='validation-pill';
      dayBox.innerHTML='';issueBox.innerHTML='';
      $('validationMessage').textContent='پس از پیشنهاد برنامه، نتیجه بررسی مستقل اینجا نمایش داده می‌شود.';
      return;
    }
    $('baseCapacity').textContent=`${v.base_capacity_hours} ساعت`;
    $('requiredHours').textContent=`${v.mandatory_hours} ساعت`;
    const labels={pass:'✓ معتبر',needs_approval:'⚠ نیازمند تأیید اضافه‌کاری',invalid:'✕ نیازمند اصلاح'};
    badge.textContent=labels[v.verdict]||'نامشخص';badge.className=`validation-pill ${v.verdict}`;
    $('validationMessage').textContent=v.verdict==='invalid'
       ? `برنامه ${v.error_count} خطای قطعی دارد و اجازه ذخیره داده نمی‌شود.`
       : v.verdict==='needs_approval'
       ? `ترتیب کارها صحیح است، اما ${v.extra_hours} ساعت اضافه‌کاری نیاز دارد؛ بدون تأیید صریح ذخیره نمی‌شود.`
       : 'تمام تسک‌های ضروری، ظرفیت روزانه و پیش‌نیازها تأیید شدند.';
    dayBox.innerHTML=(v.daily||[]).map(d=>`<div class="day-row"><span>روز ${esc(d.day)}</span><strong>${esc(d.hours)} / ${esc(d.base_capacity)} ساعت</strong>${d.extra_required>0?'<small>نیاز به اضافه‌کاری</small>':''}</div>`).join('');
    issueBox.innerHTML=(v.issues||[]).map(i=>`<div class="issue ${esc(i.severity)}"><span>${i.severity==='error'?'✕':'!'}</span><p>${esc(i.message)}</p></div>`).join('');
  }

  function render(s) {
    $('metricTotal').textContent = s.task_count;
    $('metricDone').textContent = s.task_done;
    $('metricHigh').textContent = s.task_high;
    $('elapsed').textContent = `زمان اجرا: ${elapsed(s.elapsed)}`;
    statePill.className = `state-pill ${s.status}`;
    $('stateText').textContent = tags[s.status] || 'در حال بررسی';
    runBtn.disabled = s.status==='running' || s.status==='approval';
    runBtn.innerHTML = s.status==='running' || s.status==='approval'
      ? '<span class="btn-icon">◌</span> کیومرث در حال کاره...'
      : '<span class="btn-icon">✦</span> شروع تحلیل پروژه <span class="left-arrow">←</span>';
    $('runHint').textContent = s.status==='running' ? 'پاسخ مدل محلی ممکن است چند دقیقه طول بکشد' : 'مدل زبانی روی سیستم خودت اجرا می‌شه';
    renderLogs(s.logs);
    renderValidation(s.validation,s.capacity_summary);
    const needsExtra = !!(s.validation && s.validation.extra_hours > 0);
    $('saveExtraWrap').hidden = !(s.status==='done' && needsExtra && s.validation.valid && !s.saved);
    $('approvalExtraWrap').hidden = !needsExtra;
    if(!needsExtra){$('approvalExtraCheck').checked=false;$('saveExtraCheck').checked=false;}
    const reportText = (s.final || s.plan_preview || '').trim();
    $('planDetails').hidden = !s.plan_preview;
    $('fullPlan').textContent = s.plan_preview || '';
    lastFinal = s.final || s.plan_preview || '';
    $('reportContent').textContent = reportText || (s.error ? `خطا: ${s.error}` : 'وقتی بررسی پروژه تمام شود، اولویت‌ها، ریسک‌ها و برنامه پیشنهادی در همین بخش نمایش داده می‌شوند.');
    $('reportContent').classList.toggle('is-empty', !reportText && !s.error);
    $('reportStatus').textContent = s.saved ? 'فایل ذخیره شد ✓' : reportText ? 'پیشنهاد آماده' : s.status==='error' ? 'نیاز به بررسی' : 'منتظر تحلیل';
    if(s.plan_origin) $('reportStatus').textContent += ' | '+s.plan_origin;
    $('downloadBtn').hidden = !s.output_exists;
    $('saveFinalBtn').hidden = s.status !== 'done' || s.saved || !reportText || !(s.validation && s.validation.valid);
    $('speakBtn').disabled = !lastFinal || !('speechSynthesis' in window);
    if (s.approval_pending) {
      if (!dialog.open && !modalWasOpened) {
        $('approvalText').textContent = s.plan_preview;
        $('approvalExtraCheck').checked = false;
        dialog.showModal();
        modalWasOpened = true;
      }
    } else if (dialog.open) {
      dialog.close();
    }
    if (s.status !== 'approval') modalWasOpened=false;
    if (s.status==='done' && previousStatus && previousStatus!=='done') toast('✓ کیومرث تحلیل را به پایان رساند.');
    if (s.status==='error' && previousStatus!=='error') toast('⚠ در اجرای مدل خطایی رخ داد. گزارش را ببین.');
    previousStatus = s.status;
  }

  async function refresh() {
    try {
      const res = await fetch('/api/state', {cache:'no-store'});
      if (!res.ok) throw Error('Server unavailable');
      const s = await res.json();
      $('connectionText').textContent = 'اتصال به پنل محلی برقرار است';
      $('connectionDot').style.background = '#28d9a3';
      render(s);
    } catch(e) {
      $('connectionText').textContent = 'اتصال به سرور قطع شد';
      $('connectionDot').style.background = '#ff6988';
    }
  }

  runBtn.addEventListener('click', async () => {
    const model=$('modelInput').value.trim(), task=$('taskInput').value.trim();
    if (!model || !task) {toast('مدل و دستور را کامل کن.');return;}
    runBtn.disabled=true;
    $('reportContent').textContent='کیومرث مشغول بررسی پروژه است...';
    try {
      await post('/api/start', {model,task,timeout:Number($('timeoutInput').value)});
      lastSignature='';
      await refresh();
    } catch(e) {toast('خطا: '+e.message);runBtn.disabled=false;}
  });
  async function decision(approved) {
    $('approveBtn').disabled=true;
    $('rejectBtn').disabled=true;
    try {
      const approve_extra = !!(approved && $('approvalExtraCheck').checked);
      await post('/api/approval',{approved,approve_extra});
      dialog.close();
      toast(approved?'درخواست تأیید شد؛ در حال ذخیره...':'درخواست ذخیره رد شد.');
    } catch(e) {toast(e.message);}
    finally { $('approveBtn').disabled=false; $('rejectBtn').disabled=false; await refresh(); }
  }
  $('approveBtn').addEventListener('click',()=>decision(true));
  $('rejectBtn').addEventListener('click',()=>decision(false));
  // Do not allow dismissing the approval modal without an explicit yes/no.
  dialog.addEventListener('cancel',e=>e.preventDefault());
  $('saveFinalBtn').addEventListener('click', async () => {
    try {const result=await post('/api/save-final',{approve_extra:!!$('saveExtraCheck').checked});toast(result.message);await refresh();}
    catch(e){toast(e.message);}
  });
  $('speakBtn').addEventListener('click', () => {
    if (!('speechSynthesis' in window)) {toast('مرورگر از خواندن متن پشتیبانی نمی‌کند.');return;}
    if (speaking) {window.speechSynthesis.cancel();speaking=false;$('speakBtn').textContent='◉ خواندن گزارش با صدا';return;}
    const message = lastFinal.replace(/[#*_`]/g,' ').slice(0,6000);
    if(!message)return;
    const utter=new SpeechSynthesisUtterance(message);
    utter.lang='fa-IR';utter.rate=.93;utter.pitch=1;
    const voices=window.speechSynthesis.getVoices();
    utter.voice=voices.find(v=>v.lang.toLowerCase().startsWith('fa')) || null;
    utter.onend=()=>{speaking=false;$('speakBtn').textContent='◉ خواندن گزارش با صدا';};
    utter.onerror=()=>{speaking=false;$('speakBtn').textContent='◉ خواندن گزارش با صدا';toast('صدای فارسی روی سیستم یا مرورگر موجود نیست.');};
    window.speechSynthesis.speak(utter);speaking=true;$('speakBtn').textContent='■ توقف خواندن';
  });
  refresh();
  setInterval(refresh, 1000);
})();
