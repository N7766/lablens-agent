// Reports are downloaded as files. Their contents never enter the workbench DOM.
function createReportExport(prefix) {
  const group = document.getElementById(`${prefix}-reports`);
  const status = document.getElementById(`${prefix}-report-status`);
  const buttons = [...group.querySelectorAll('[data-report-format]')];
  let source = null, revision = 0, busy = false;

  function sync() {
    group.hidden = !source;
    group.setAttribute('aria-busy', String(busy));
    for (const button of buttons) button.disabled = !source || busy;
  }

  function setReceipt(receipt) {
    revision++;
    source = receipt;
    busy = false;
    status.textContent = '';
    status.classList.remove('error');
    sync();
  }

  for (const button of buttons) button.onclick = async () => {
    if (!source || busy) return;
    const rev = revision, receipt = source, format = button.dataset.reportFormat;
    const label = format === 'html' ? 'HTML' : 'Markdown';
    busy = true;
    status.classList.remove('error');
    status.textContent = `正在复核并生成 ${label} 报告…`;
    sync();
    try {
      const report = await api('/api/report/export', {receipt, format});
      if (rev !== revision || source !== receipt) return;
      if (report.format !== format || typeof report.content !== 'string' || typeof report.filename !== 'string') {
        throw Error('报告内容不完整，请重试。');
      }
      const mediaType = format === 'html' ? 'text/html;charset=utf-8' : 'text/markdown;charset=utf-8';
      const url = URL.createObjectURL(new Blob([report.content], {type: mediaType}));
      const link = document.createElement('a');
      link.href = url;
      link.download = report.filename;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      status.textContent = `${label} 报告已下载。本次报告生成不调用模型。`;
    } catch (error) {
      if (rev === revision && source === receipt) {
        status.textContent = '报告导出失败：' + error.message;
        status.classList.add('error');
      }
    } finally {
      if (rev === revision && source === receipt) { busy = false; sync(); }
    }
  };
  sync();
  return {setReceipt, clear: () => setReceipt(null)};
}
