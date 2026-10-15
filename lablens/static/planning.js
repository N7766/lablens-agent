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
    $('plan-steps').replaceChildren(); $('plan-unhandled').replaceChildren();
    for (const step of p.steps) {
      const card = node('div', '', 'plan-step');
      card.append(node('h3', `${step.id} · ${kindNames[step.kind]}`), node('p', `对应原文：“${step.request_excerpt}”`));
      const list = document.createElement('ul');
      for (const condition of step.conditions) list.append(node('li', condition));
      card.append(list); $('plan-steps').append(card);
    }
    unhandledView(p.unhandled, $('plan-unhandled'));
    $('plan-review').hidden = false; $('plan-execute').disabled = false;
    $('plan-status').textContent = `待核对 · ${p.steps.length}步 · ${p.unhandled.length}项未处理 · 预览不调用模型`;
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

$('plan-draft').onclick = async () => {
  invalidateAnalysis();
  const rev = analysisRevision;
  $('plan-editor').value = ''; draftTrace = null; $('plan-trace-download').hidden = true;
  $('plan-draft').disabled = true;
  $('plan-status').textContent = '正在生成计划 · 单次模型调用，尚未执行计划…';
  try {
    const result = await api('/api/plan/draft', {question: $('plan-question').value});
    if (rev !== analysisRevision) return;
    draftTrace = result; $('plan-trace-download').hidden = false;
    if (result.plan) $('plan-editor').value = JSON.stringify(result.plan, null, 2);
    if (result.status !== 'needs_review' && result.plan) $('plan-editor-details').open = true;
    if (result.status !== 'needs_review') throw Error(result.error || `未生成有效计划（${result.status}）`);
    await reviewAnalysis();
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
    $('plan-result').hidden = false;
    $('plan-result-status').textContent = `${result.executed_step_ids.length}个计划步骤已执行 · ${result.unhandled.length}项未处理 · 执行阶段0次模型调用`;
    $('plan-status').textContent = '计划内步骤执行完成；请继续核对是否完整覆盖了问题。';
  } catch (err) {
    if (rev === analysisRevision) $('plan-status').textContent = '计划执行失败：' + err.message;
  } finally { if (rev === analysisRevision) $('plan-execute').disabled = false; }
};
$('plan-receipt-download').onclick = () => { if (analysisReceipt) download(analysisReceipt, 'lablens-plan-receipt.json'); };
$('plan-trace-download').onclick = () => { if (draftTrace) download(draftTrace, `lablens-plan-${draftTrace.id}.json`); };
