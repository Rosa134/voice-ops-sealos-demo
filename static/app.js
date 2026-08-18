const state = { projectId: null, calls: [], selectedCall: null };

const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const api = async (path, options = {}) => {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
};
const jsonBlock = (value) => esc(JSON.stringify(value ?? {}, null, 2));

async function loadProjects() {
  const data = await api('/api/v1/projects');
  const select = $('#project-select');
  select.innerHTML = data.projects.map((project) => `<option value="${esc(project.id)}">${esc(project.name)} · ${esc(project.enterprise_id)}</option>`).join('');
  if (!state.projectId || !data.projects.some((item) => item.id === state.projectId)) state.projectId = data.projects[0]?.id;
  select.value = state.projectId;
  select.onchange = () => { state.projectId = select.value; loadProject(); };
  const current = data.projects.find((item) => item.id === state.projectId);
  $('#project-description').textContent = current ? `${current.agent_name} · ${current.description}` : '';
}

function metric(label, value, tone = '') { return `<div class="metric-card ${tone}"><span class="metric-label">${label}</span><strong class="metric-value">${value}</strong></div>`; }

async function loadProject() {
  try {
    const [overview, calls, badcases] = await Promise.all([
      api(`/api/v1/projects/${encodeURIComponent(state.projectId)}/overview`),
      api(`/api/v1/projects/${encodeURIComponent(state.projectId)}/calls`),
      api(`/api/v1/projects/${encodeURIComponent(state.projectId)}/badcases?status=open`),
    ]);
    $('#metric-cards').innerHTML = [
      metric('通话数', overview.call_count), metric('红线命中', overview.redline_count, overview.redline_count ? 'danger' : ''),
      metric('Badcase 总数', overview.badcase_count, overview.badcase_count ? 'warning' : ''), metric('待人工验收', overview.open_badcase_count, overview.open_badcase_count ? 'warning' : ''), metric('投诉/异常通话', overview.complaint_count, overview.complaint_count ? 'danger' : ''),
    ].join('');
    state.calls = calls.calls;
    $('#call-count-label').textContent = `${state.calls.length} 条展示记录`;
    $('#calls-body').innerHTML = state.calls.length ? state.calls.map(callRow).join('') : `<tr><td colspan="6" class="muted">当前项目暂无通话</td></tr>`;
    document.querySelectorAll('.call-row').forEach((row) => row.onclick = () => selectCall(row.dataset.uniqueId));
    $('#badcases-list').innerHTML = badcases.badcases.length ? badcases.badcases.map(queueItem).join('') : `<div class="empty-state">暂无待处理 Badcase</div>`;
    document.querySelectorAll('.queue-item').forEach((item) => item.onclick = () => selectCall(item.dataset.uniqueId));
    if (state.calls[0]) await selectCall(state.calls[0].unique_id); else clearDetail();
  } catch (error) {
    $('#calls-body').innerHTML = `<tr><td colspan="6" class="hit">加载失败：${esc(error.message)}</td></tr>`;
  }
}

function callRow(call) {
  const complaint = call.complaint_detected ? '<span class="status danger">是</span>' : '<span class="status">否</span>';
  return `<tr class="call-row" data-unique-id="${esc(call.unique_id)}"><td>${esc(formatTime(call.call_time))}</td><td>${esc(call.unique_id)}</td><td>${esc(call.intent || '-')}</td><td><span class="status ${call.task_status.includes('部分') ? 'warn' : ''}">${esc(call.task_status || '-')}</span></td><td>${esc(call.final_emotion || '-')}</td><td>${complaint}</td></tr>`;
}

function queueItem(item) {
  return `<div class="queue-item" data-unique-id="${esc(item.unique_id)}"><div class="item-title"><span>${esc(item.category)} · ${esc(item.subcategory)}</span><span class="status danger">${esc(item.severity)}</span></div><div class="item-body">${esc(item.tuning_recommendation)}</div><div class="item-meta">${esc(item.badcase_id)} · ${esc(item.unique_id)}</div></div>`;
}

async function selectCall(uniqueId) {
  state.selectedCall = await api(`/api/v1/projects/${encodeURIComponent(state.projectId)}/calls/${encodeURIComponent(uniqueId)}`);
  const call = state.selectedCall;
  $('#detail-empty').classList.add('hidden'); $('#detail-content').classList.remove('hidden');
  $('#detail-title').textContent = call.unique_id;
  $('#detail-meta').textContent = `${formatTime(call.call_time)} · ${call.agent_name}`;
  $('#call-facts').innerHTML = [
    fact('意图', call.intent), fact('任务状态', call.task_status), fact('通话时长', `${Math.round(call.duration_ms / 1000)} 秒`),
    fact('轮次数', call.turn_count), fact('打断次数', call.barge_in_count), fact('TTFA 均值', `${call.ttfa_ms_avg} ms`),
    fact('终态情绪', call.final_emotion), fact('投诉/异常', call.complaint_detected ? '命中' : '未命中', call.complaint_detected ? 'hit' : 'pass'), fact('终止原因', call.termination_reason),
  ].join('');
  $('#quality-list').innerHTML = call.quality_checks.length ? call.quality_checks.map(qualityItem).join('') : '<div class="empty-state">暂无质检指标</div>';
  $('#redline-list').innerHTML = call.redlines.length ? call.redlines.map(redlineItem).join('') : '<div class="empty-state">本通话未命中红线</div>';
  $('#detail-badcases').innerHTML = call.badcases.length ? call.badcases.map(detailBadcase).join('') : '<div class="empty-state">本通话暂无 Badcase</div>';
}

function fact(label, value, tone = '') { return `<div class="fact"><span class="fact-label">${esc(label)}</span><strong class="fact-value ${tone}">${esc(value ?? '-')}</strong></div>`; }
function qualityItem(item) {
  const tone = item.hit ? 'hit' : 'pass';
  return `<div class="quality-item"><div class="item-title"><span>${esc(item.name || item.check_id)} <small class="muted">${esc(item.category)}</small></span><span class="${tone}">${item.hit ? '命中' : '未命中'}</span></div><div class="item-body"><strong>分析原因：</strong>${esc(item.reason)}</div><div class="subsection"><strong>证据：</strong><pre>${jsonBlock(item.evidence)}</pre></div><div class="subsection"><strong>优化建议：</strong><pre>${jsonBlock(item.tuning)}</pre></div></div>`;
}
function redlineItem(item) { return `<div class="redline-item"><div class="item-title"><span>${esc(item.redline_id)}</span><span class="status ${item.hit ? 'danger' : ''}">${item.hit ? '命中' : '未命中'}</span></div><div class="item-body">${esc(item.reason)}</div><div class="item-meta">动作：${esc(item.action)} · 钉钉状态：${esc(item.notification_status)}</div></div>`; }
function detailBadcase(item) { return `<div class="badcase-item"><div class="item-title"><span>${esc(item.badcase_id)} · ${esc(item.category)}</span><span class="status danger">${esc(item.status)}</span></div><div class="item-body"><strong>期望：</strong>${esc(item.expected)}<br><strong>实际：</strong>${esc(item.observed)}</div><div class="item-meta">归属：${esc(item.owner_layer)} · 调优：${esc(item.tuning_recommendation)}</div></div>`; }
function formatTime(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'; }
function clearDetail() { $('#detail-empty').classList.remove('hidden'); $('#detail-content').classList.add('hidden'); $('#detail-title').textContent = '选择一条通话查看详情'; $('#detail-meta').textContent = ''; }

async function boot() { await loadProjects(); await loadProject(); }
$('#refresh-button').onclick = () => loadProject();
boot().catch((error) => { $('#detail-empty').textContent = `初始化失败：${error.message}`; });
