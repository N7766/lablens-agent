async (page) => {
 const posts=[];page.on('response',r=>{if(r.request().method()==='POST'&&/\/tasks$/.test(r.url()))posts.push(r);});
 await page.locator('#new-name').fill('故障恢复浏览器验收');await page.locator('#create-form button').click();
 await page.locator('#revision').filter({hasText:'0 份资料'}).waitFor();
 await page.locator('#file').setInputFiles({name:'closure-evidence.txt',mimeType:'text/plain',buffer:Buffer.from('长度单位按照空白分隔词数统计，不能据此推断推理加速。')});
 await page.locator('#revision').filter({hasText:'1 份资料'}).waitFor();
 await page.locator('#question').fill('长度单位如何统计？');await page.locator('#submit').click();
 await page.locator('#result-status').filter({hasText:'失败'}).waitFor();
 const first=JSON.parse(await page.locator('#answer pre').textContent());
 await page.locator('#answer summary').click();
 if(first.execution_usage.model_calls!==0||first.execution_usage.embedding_requests!==0)throw new Error('Offline failed task must preserve known zero usage');
 await page.locator('#submit').click();await page.locator('#notice').filter({hasText:'已找到同一请求的任务'}).waitFor();
 const a=await posts[0].json(),b=await posts[1].json();
 if(a.id!==b.id||!b.reused)throw new Error('Replay must retain failed task');
 const count=posts.length;await page.locator('#prepare-retry').click();
 if(posts.length!==count||!(await page.locator('#notice').innerText()).includes('尚未提交'))throw new Error('Preparing retry must not submit');
 await page.setViewportSize({width:1440,height:1000});await page.locator('#answer').scrollIntoViewIfNeeded();
 await page.screenshot({path:'docs/evaluations/v0.12/closure/failed-task.png'});
 return {workspace_id:await page.locator('#workspace').inputValue(),failed_task:first,first_key:posts[0].request().headers()['idempotency-key'],checks:{actual_worker_unreachable_causes_failed_task:first.status==='FAILED',known_zero_usage_visible:true,old_intent_reuses_failed_task:true,prepare_retry_no_post:true},phase:'worker unreachable',real_model_calls:0};
}
