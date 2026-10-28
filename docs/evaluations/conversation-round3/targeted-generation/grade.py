from pathlib import Path
import json,hashlib,datetime
p=Path(__file__).resolve().parent
records=[json.loads(x) for x in (p/'run/results.jsonl').read_text().splitlines()]
fixtures={x['id']:x for x in json.loads((p/'known-failures.json').read_text())}
rows=[]
for row in records:
 cid=row['id'];r=row['result'];docs={d['id']:d for d in fixtures[cid.split('-')[0]]['documents']}
 by_id={c['chunk_id']:c for c in r.get('citations',[])}
 quotes=[q for a in r.get('assessments',[]) for q in a.get('quotes',[])]
 exactquotes=[q['chunk_id'] in by_id and q['quote'] in by_id[q['chunk_id']]['text'] for q in quotes]
 exactcitations=[c['text']==docs[c['document_id']]['text'] and hashlib.sha256(c['text'].encode()).hexdigest()==c['content_sha256'] for c in by_id.values()]
 if cid=='d01-targeted':
  grade={'primary_targeted_proposition':'PASS','semantic_claims':'FAIL','source_grounding':'FAIL','false_refusal':'PASS','causal_historical_scope':'PASS','whole_report_strict_pass':False,
   'reason':'The old missing-SFT error is corrected, with correct439/46295.02% baseline and153/46233.12% DPO plus61.9percentagepoint gap; uniqueoverfittingcause is properly withheld. Additional32.42/79.68 outputlength claims call the values tokens without distinguishing that the provided evaluation implementation uses len(prediction.split()), not tokenizer tokens. This prevents whole-report semantic pass.',
   'source_note':'Selected table rows omit column headers needed to attribute439/32.42 toStage3LoRASFT versus153/79.68 toStage4. The full cited chunk contains the headers, but exactselectedpassage entailment is weaker. The lengthclaims do not cite the provided implementation of output_tokens. No factual error is inferred just from colloquial显著; it is noted as ambiguous language, not established statisticalsignificance.',
   'complete_answer_conditions_note':'The report cites a passage mentioning same462questions and max_new_tokens128; answerbody states samevalidationset but omits generationlimit. Primary baseline claim still passes; no generalized quality inference.'}
 else:
  grade={'primary_targeted_proposition':'PASS','semantic_claims':'PASS','source_grounding':'PASS','false_refusal':'PASS','causal_historical_scope':'PASS','whole_report_strict_pass':True,
   'reason':'Final supported claim is literal configfalse only. Historical per-step recomputation and an assertion about missing runtime logs are labelled insufficient with neutral explanation requiring matching runtime evidence. No historical behavior is accepted as established. Documentedscalar remains visible.',
   'source_note':'Exact config scalar selected quote supports onlytheconfig fact. Additionalconfig/code citations are context for insufficient items, not treated as proof of historicalbehavior.',
   'presentation_note':'The insufficient item retains an assertive proposition headline; read with its verdict and explanation. UI can be clearer, but it is not counted as an accepted runtime fact.'}
 rows.append({'case':cid,'result_status':r['status'],'usage':row['usage'],'quote_existence':{'matched':sum(exactquotes),'total':len(exactquotes)},'citation_existence':{'matched':sum(exactcitations),'total':len(exactcitations)},'answer':r['answer'],**grade})
summary={'scope':'Candidate1 exposed generation-only real DPO regression; AI developer-side review; not retrieval or Agent quality, not independent human evaluation','graded_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'input_manifest_sha256':(p/'frozen-manifest.sha256').read_text().split()[0],'raw_manifest_sha256':(p/'raw-output-manifest.sha256').read_text().split()[0],'requests':2,'model_calls':sum(x['usage']['model_calls'] for x in records),'automatic_retries':0,'runtime_answered':2,'primary_known_failure_passes':2,'whole_report_strict_passes':1,'known_tokens':{k:sum(x['usage']['generation_tokens'][k] for x in records) for k in ['prompt_tokens','completion_tokens','total_tokens']},'usage_unknown_tasks':sum(x['usage']['provider_usage_unknown'] for x in records),'final_quote_existence':{k:sum(x['quote_existence'][k] for x in rows) for k in ['matched','total']},'final_citation_existence':{k:sum(x['citation_existence'][k] for x in rows) for k in ['matched','total']},'scope_notes':['Original fixture includes actual retrieved parent documents, all supplied directly; no new retrieval evaluation.','Legacy _generate does not enable workspace-only scoped_review_contract; this checks shared generation/config guards, not all Round3 Agent quality guards.','2/2 primary bug outcomes do not mean both entire reports passed.','Do not call tokenusage a provider billing reconciliation.']}
(p/'grading').mkdir(exist_ok=True)
for name,value in [('per-case.json',{'scope':summary['scope'],'cases':rows}),('summary.json',summary)]:
 (p/'grading'/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
(p/'README.md').write_text('''# Round 3 targeted real DPO generation regression\n\nTwo exposed real-document questions, one attempt each,4modelcalls. Both old primary errors are corrected: SFT baseline recognized, configfalse not accepted as historical per-step runtime proof. Whole-report strict score1/2: d01 adds token-length wording without the supplied whitespace-count implementation qualification, and selectedtable rows omit columnheaders. Fullper-case reasons are in `grading/per-case.json`.\n\nScope is generation on originalretrieved parentdocuments, not freshretrieval, multi-turnAgent or independent human evaluation. The ordinary generation path does not enable workspace scopedreviewcontract. Keep these2/2primary outcomes separate from six-turnAgent scores.\n''')
files={str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted((p/'grading').rglob('*')) if f.is_file()}
(p/'grading-manifest.json').write_text(json.dumps({'scope':summary['scope'],'files':files},ensure_ascii=False,indent=2)+'\n');(p/'grading-manifest.sha256').write_text(hashlib.sha256((p/'grading-manifest.json').read_bytes()).hexdigest()+'  grading-manifest.json\n')
print(json.dumps(summary,ensure_ascii=False,indent=2))
