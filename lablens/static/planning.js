// All imported notes, model excerpts and errors are rendered as text, never HTML.
function notesView(notes, container) {
  for (const n of notes) {
    const detail = document.createElement('details');
    detail.append(node('summary', n.title || n.id), node('p', n.text), node('span', n.id, 'source'));
    container.append(detail);
  }
}

function evidenceView(view, container) {
  const out = view.result, section = node('div', '', 'query-card');
  if (view.kind === 'compare') {
    section.append(node('h3', '计算比较结果'), node('p', `基线 ${out.baseline.id} → 候选 ${out.candidate.id}`, 'source'));
    const wrap = node('div', '', 'table-wrap'), t = document.createElement('table');
    const head = document.createElement('thead'), hr = document.createElement('tr');
    for (const x of ['指标', '基线', '候选', '差值 / 减少比例']) hr.append(node('th', x));
    head.append(hr); t.append(head);
    const body = document.createElement('tbody');
    for (const values of [
      ['准确率', `${(out.baseline.accuracy * 100).toFixed(4)}%`, `${(out.candidate.accuracy * 100).toFixed(4)}%`, `${out.accuracy_delta_percentage_points.toFixed(4)} 个百分点`],
      ['正确题数', out.baseline.correct, out.candidate.correct, `${out.additional_correct} 题（候选 − 基线）`],
      [`平均长度（${out.baseline.length_unit}）`, out.baseline.mean_length.toFixed(6), out.candidate.mean_length.toFixed(6), `${out.length_reduction_percent.toFixed(4)}%（减少比例）`]
    ]) {
      const row = document.createElement('tr');
      for (const value of values) row.append(node('td', String(value)));
      body.append(row);
    }
    t.append(body); wrap.append(t); section.append(wrap);
    section.append(node('p', '准确率差、正确题数差 = 候选 − 基线；长度减少比例 = (基线 − 候选) / 基线。减少比例为负表示长度增加。', 'small'));
    notesView(out.limitations, section);
  } else if (view.kind === 'describe') {
    section.append(node('h3', '资料范围与原文说明'), node('p', '已有字段：' + out.fields.join(' · '), 'source'),
      node('p', '此快照未记录：' + out.missing_fields.join(' · ')), node('p', out.scope, 'small'));
    notesView(out.notes, section);
  } else if (view.kind === 'notes') {
    section.append(node('h3', '检索到的原文说明'));
    notesView(out.notes, section);
  }
  container.append(section);
}

let analysisRevision = 0, analysisPreview = null, analysisReceipt = null, draftTrace = null;
const kindNames = {query: '查询', compare: '比较', describe: '查看资料'};
const reasons = {missing_data: '资料缺失', unsupported_analysis: '当前不支持此分析', ambiguous_request: '需要明确要求'};
const coverageStates = {mapped: '已关联步骤', unhandled: '标为未处理', unmapped: '未关联完整分句', conflict: '关联与未处理冲突'};
const requestCheckStates = {matched: '已核对支持条件', conflict: '条件不一致', missing: '缺少对应查询', ambiguous: '无法唯一匹配'};
const queryFieldNames = {
  min_accuracy: '最低准确率（含边界）', max_accuracy: '最高准确率（含边界）',
  min_correct: '最少正确题数', max_length: '平均长度严格小于',
  more_accurate_than_baseline: '准确率严格高于', shorter_than_baseline: '长度严格短于',
  order_by: '排序指标', descending: '排序方向', top_k: '结果数量',
  include_ties: '边界并列', pareto_only: 'Pareto 筛选'
};

function queryFieldValue(key, value) {
  if (value === undefined) return '未提供';
  if (value === null) return key === 'top_k' ? '不截断，保留全部' : '不限制';
  if (key === 'min_accuracy' || key === 'max_accuracy') return `${Number((value * 100).toFixed(6))}%`;
  if (key === 'order_by') return {accuracy: '准确率', mean_length: '平均长度', correct: '正确题数'}[value] || String(value);
  if (key === 'descending') return value ? '降序' : '升序';
  if (key === 'include_ties') return value ? '保留边界并列' : '不扩展边界并列';
  if (key === 'pareto_only') return value ? '仅保留 Pareto 集合' : '不限制';
  if (key === 'top_k') return `前 ${value} 条`;
  if (key === 'min_correct') return `${value} 题`;
  return String(value);
}

function requestDifferenceText(difference) {
  const text = String(difference);
  const match = text.match(/^(.+) 的 ([a-z_]+) 应为 (.+)，实际为 (.+)$/);
  if (!match || !Object.prototype.hasOwnProperty.call(queryFieldNames, match[2])) return text;
  function parseValue(value) {
    if (value === 'None') return null;
    if (value === 'True') return true;
    if (value === 'False') return false;
    if (/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(value)) {
      const number = Number(value);
      return Number.isFinite(number) ? number : undefined;
    }
    const quoted = value.match(/^'([^'\\]*)'$/);
    return quoted ? quoted[1] : undefined;
  }
  const expected = parseValue(match[3]), actual = parseValue(match[4]);
  if (expected === undefined || actual === undefined) return text;
  const field = match[2];
  return `${match[1]} 的${queryFieldNames[field]}应为“${queryFieldValue(field, expected)}”，实际为“${queryFieldValue(field, actual)}”`;
}

function requestChecksView(report, container) {
  if (!report) return;
  container.append(node('h3', '请求规则核对'), node('p', '只核对少量受支持句式中的查询条件；规则匹配不保证完整理解原问题，比较方向、资料解释和其他要求仍需逐项确认。', 'small'));
  const units = report.units || [];
  const summary = report.has_conflicts ? '发现请求与计划条件不一致，请核对后再决定执行。'
    : units.length ? '已核对以下受支持条件；其余要求不在本规则结论范围内。'
      : '本问题没有可按现有规则核对的条件，尚未进行请求规则核对。';
  container.append(node('p', summary, report.has_conflicts ? 'error' : ''));
  for (const unit of units) {
    const card = node('div', '', 'plan-step');
    card.append(node('h3', `${unit.id} · ${requestCheckStates[unit.state] || '尚未核对'}`, unit.state === 'matched' ? '' : 'error'),
      node('p', `对应原文：“${unit.text}”`),
      node('p', `关联步骤：${unit.step_ids.length ? unit.step_ids.join('、') : '未找到'}`, 'source'));
    const detail = document.createElement('details');
    detail.open = unit.state !== 'matched';
    detail.append(node('summary', '查看原文要求与实际参数'));
    const actual = unit.actual || [];
    const wrap = node('div', '', 'table-wrap'), table = document.createElement('table');
    const head = document.createElement('thead'), heading = document.createElement('tr');
    heading.append(node('th', '条件'), node('th', '原文要求'));
    for (const step of actual) heading.append(node('th', `${step.step_id} 实际参数`));
    if (!actual.length) heading.append(node('th', '实际参数'));
    head.append(heading); table.append(head);
    const body = document.createElement('tbody');
    const checkedKeys = unit.checked_fields || Object.keys(unit.expected || {});
    for (const key of checkedKeys) {
      const expected = unit.expected?.[key];
      const row = document.createElement('tr');
      row.append(node('td', queryFieldNames[key] || key), node('td', queryFieldValue(key, expected)));
      for (const step of actual) row.append(node('td', queryFieldValue(key, step.query?.[key]), step.query?.[key] !== expected ? 'error' : ''));
      if (!actual.length) row.append(node('td', '缺少对应查询', 'error'));
      body.append(row);
    }
    table.append(body); wrap.append(table); detail.append(wrap); card.append(detail);
    if (unit.differences?.length) {
      const list = document.createElement('ul');
      for (const difference of unit.differences) {
        list.append(node('li', requestDifferenceText(difference), 'error'));
      }
      card.append(list);
    }
    container.append(card);
  }
  if (report.unassessed?.length) {
    container.append(node('h3', '以下内容尚未按规则核对'));
    const list = document.createElement('ul');
    for (const item of report.unassessed) list.append(node('li', `“${item.text}” · ${item.reason || '当前句式不在支持范围内'}`));
    container.append(list);
  }
  if (report.unexpected_step_ids?.length) container.append(node('p', `未匹配到受支持条件的查询步骤：${report.unexpected_step_ids.join('、')}。请检查是否加入了原问题未要求的查询。`, 'error'));
  if (report.has_conflicts) container.append(node('p', '自动生成遇到规则冲突时会尝试修正一次。手动编辑可以保留冲突；仍可按所列步骤执行，但不表示原问题已完整完成。', 'small'));
}

function coverageView(report, container) {
  if (!report) return;
  container.append(node('h3', '原问题分句核对'), node('p', '按分号、句号、问号和换行分句；这里只检查原文关联。即使全部已关联，参数或同句中的其他要求仍可能有误。', 'small'));
  const list = document.createElement('ul');
  for (const item of report.clauses) {
    list.append(node('li', `${item.id} · ${item.text} → ${coverageStates[item.state]}${item.step_ids.length ? '：' + item.step_ids.join('、') : ''}`));
  }
  container.append(list);
}

function invalidateAnalysis() {
  analysisRevision++;
  analysisPreview = analysisReceipt = null;
  $('plan-review').hidden = $('plan-result').hidden = true;
  $('plan-execute').disabled = true;
  $('plan-status').textContent = '问题或计划已变化，请重新预览。';
}

function unhandledView(items, container) {
  if (!items.length) return;
  const box = node('div', '', 'notice');
  box.append(node('h3', '计划中尚未处理的要求'));
  for (const item of items) box.append(node('p', `“${item.request_excerpt}” · ${reasons[item.reason]}`));
  container.append(box);
}

async function reviewAnalysis() {
  invalidateAnalysis();
  const rev = analysisRevision;
  try {
    const payload = {question: $('plan-question').value, plan: JSON.parse($('plan-editor').value)};
    const p = await api('/api/plan/preview', payload);
    if (rev !== analysisRevision) return;
    analysisPreview = {...payload, preview_id: p.preview_id};
    $('plan-steps').replaceChildren(); $('plan-unhandled').replaceChildren(); $('plan-coverage').replaceChildren(); $('plan-request-checks').replaceChildren();
    coverageView(p.coverage, $('plan-coverage'));
    requestChecksView(p.request_checks, $('plan-request-checks'));
    for (const step of p.steps) {
      const card = node('div', '', 'plan-step');
      card.append(node('h3', `${step.id} · ${kindNames[step.kind]}`), node('p', `对应原文：“${step.request_excerpt}”`));
      const list = document.createElement('ul');
      for (const condition of step.conditions) list.append(node('li', condition));
      card.append(list); $('plan-steps').append(card);
    }
    unhandledView(p.unhandled, $('plan-unhandled'));
    $('plan-review').hidden = false; $('plan-execute').disabled = false;
    $('plan-status').textContent = `${p.request_checks?.has_conflicts ? '⚠ 请求规则存在冲突，请核对' : '待核对'} · ${p.steps.length}步 · ${p.unhandled.length}项未处理 · 预览不调用模型`;
  } catch (err) {
    if (rev === analysisRevision) $('plan-status').textContent = '计划预览失败：' + err.message;
  }
}

$('plan-question').oninput = $('plan-editor').oninput = invalidateAnalysis;
$('plan-preview').onclick = reviewAnalysis;
$('plan-example').onclick = () => {
  $('plan-question').value = '找出准确率最高的所有并列记录；以SFT为基线比较该唯一结果；说明验证集和显著性边界。';
  $('plan-editor').value = JSON.stringify({steps: [
    {id: 'best', kind: 'query', request_excerpt: '找出准确率最高的所有并列记录', query: {top_k: 1}},
    {id: 'delta', kind: 'compare', request_excerpt: '以SFT为基线比较该唯一结果', baseline: {run_id: 'run:sft'}, candidate: {query_step: 'best'}},
    {id: 'limits', kind: 'describe', request_excerpt: '说明验证集和显著性边界'}
  ], unhandled: []}, null, 2);
  draftTrace = null; $('plan-trace-download').hidden = true;
  reviewAnalysis();
};

$('plan-check-example').onclick = () => {
  const queryClause = '分别列出正确题数至少440的完整Pareto集合，以及正确题数至少443的所有记录';
  const describeClause = '说明峰值显存测量是否记录';
  $('plan-question').value = `${queryClause}；${describeClause}。`;
  const baseQuery = {min_accuracy: 0, max_accuracy: 1, min_correct: 0, max_length: null,
    more_accurate_than_baseline: null, shorter_than_baseline: null, order_by: 'accuracy', descending: true,
    top_k: null, include_ties: true, pareto_only: false};
  $('plan-editor').value = JSON.stringify({steps: [
    {id: 'pareto', kind: 'query', request_excerpt: queryClause, query: {...baseQuery, min_correct: 440, pareto_only: true}},
    {id: 'all_records', kind: 'query', request_excerpt: queryClause, query: {...baseQuery, min_correct: 443}},
    {id: 'measurements', kind: 'describe', request_excerpt: describeClause}
  ], unhandled: []}, null, 2);
  draftTrace = null; $('plan-trace-download').hidden = true;
  reviewAnalysis();
};

$('plan-draft').onclick = async () => {
  invalidateAnalysis();
  const rev = analysisRevision;
  $('plan-editor').value = ''; draftTrace = null; $('plan-trace-download').hidden = true;
  $('plan-draft').disabled = true;
  $('plan-status').textContent = '正在生成计划 · 最多2次模型请求（校验失败可修正1次），尚未执行计划…';
  try {
    const result = await api('/api/plan/draft', {question: $('plan-question').value});
    if (rev !== analysisRevision) return;
    draftTrace = result; $('plan-trace-download').hidden = false;
    if (result.plan) $('plan-editor').value = JSON.stringify(result.plan, null, 2);
    if (result.status !== 'needs_review' && result.plan) $('plan-editor-details').open = true;
    if (result.status !== 'needs_review') throw Error(result.error || `未生成有效计划（${result.status}）`);
    await reviewAnalysis();
    if (draftTrace === result && analysisPreview?.question === result.question) $('plan-status').textContent += ` · 本次${result.model_calls}次模型请求`;
  } catch (err) {
    if (rev === analysisRevision) $('plan-status').textContent = '生成失败：' + err.message;
  } finally { $('plan-draft').disabled = false; }
};

$('plan-execute').onclick = async () => {
  if (!analysisPreview) return;
  const rev = analysisRevision;
  $('plan-execute').disabled = true;
  $('plan-status').textContent = '正在执行已预览步骤…';
  try {
    const result = await api('/api/plan/execute', analysisPreview);
    if (rev !== analysisRevision) return;
    analysisReceipt = result.receipt; $('plan-results').replaceChildren();
    for (const step of result.results) {
      const block = node('div', '', 'plan-step');
      block.append(node('h3', `${step.id} · ${kindNames[step.kind]}`), node('p', `对应原文：“${step.request_excerpt}”`));
      if (step.kind === 'query') table(step.result, block); else evidenceView(step, block);
      $('plan-results').append(block);
    }
    unhandledView(result.unhandled, $('plan-results'));
    coverageView(result.coverage, $('plan-results'));
    requestChecksView(result.request_checks, $('plan-results'));
    $('plan-result').hidden = false;
    $('plan-result-status').textContent = `${result.executed_step_ids.length}个计划步骤已执行 · ${result.unhandled.length}项未处理 · 执行阶段0次模型调用`;
    $('plan-status').textContent = result.request_checks?.has_conflicts
      ? '⚠ 已按所列步骤执行，但请求规则仍存在冲突；不表示原问题已完整完成。'
      : '计划内步骤执行完成；请继续核对是否完整覆盖了问题。';
  } catch (err) {
    if (rev === analysisRevision) $('plan-status').textContent = '计划执行失败：' + err.message;
  } finally { if (rev === analysisRevision) $('plan-execute').disabled = false; }
};
$('plan-receipt-download').onclick = () => { if (analysisReceipt) download(analysisReceipt, 'lablens-plan-receipt.json'); };
$('plan-trace-download').onclick = () => { if (draftTrace) download(draftTrace, `lablens-plan-${draftTrace.id}.json`); };
